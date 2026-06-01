import os
import argparse
import pickle
import json
import uuid
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.reid import ReIDManager, calculate_cosine_similarity
from pipeline.cam_entry import process_entry_exit
from pipeline.cam_zones import process_zones
from pipeline.cam_billing import process_billing
from pipeline.cam_stockroom import process_stockroom

IST = timezone(timedelta(hours=5, minutes=30))
MAX_PROPAGATION_WINDOW = timedelta(seconds=60)

def verify_tracker() -> str:
    """Verify bytetrack.yaml config path or return fallback (Correction 7)."""
    try:
        import pkg_resources
        ul_path = pkg_resources.get_distribution('ultralytics').location
        bt_path = os.path.join(ul_path, 'ultralytics', 'cfg', 'trackers', 'bytetrack.yaml')
        if not os.path.exists(bt_path):
            print("WARNING: bytetrack.yaml not found at expected path. Falling back to 'botsort.yaml'.")
            return 'botsort.yaml'
        return 'bytetrack.yaml'
    except Exception as e:
        print(f"Tracker verification error: {e}. Falling back to default botsort.")
        return 'botsort.yaml'

TRACKER_CONFIG = verify_tracker()

def load_pos_transaction_times(pos_csv_path: str) -> List[datetime]:
    """Load POS transaction timestamps and parse them in IST (Correction 1 & 2)."""
    if not os.path.exists(pos_csv_path):
        print(f"WARNING: POS CSV not found at {pos_csv_path}")
        return []
    try:
        df = pd.read_csv(pos_csv_path, usecols=['order_date', 'order_time'])
        times = []
        for _, row in df.iterrows():
            dt = datetime.strptime(f"{row['order_date']} {row['order_time']}", "%d-%m-%Y %H:%M:%S")
            dt = dt.replace(tzinfo=IST)
            times.append(dt)
        return times
    except Exception as e:
        print(f"Error parsing POS CSV: {e}")
        return []

def main():
    parser = argparse.ArgumentParser(description="Store Intelligence Orchestrator (Step 18)")
    parser.add_argument("--cam1", required=True, help="Path to CAM_1 video")
    parser.add_argument("--cam2", required=True, help="Path to CAM_2 video")
    parser.add_argument("--cam3", required=True, help="Path to CAM_3 video")
    parser.add_argument("--cam4", required=True, help="Path to CAM_4 video")
    parser.add_argument("--cam5", required=True, help="Path to CAM_5 video")
    parser.add_argument("--pos", required=True, help="Path to POS CSV")
    parser.add_argument("--store", default="ST1008", help="Store ID")
    parser.add_argument("--clip-start", default="2026-04-10T20:10:02+05:30", help="Clip start timestamp (IST)")
    parser.add_argument("--out", default="events.jsonl", help="Output path for events")
    parser.add_argument("--quality", default="low", choices=["low", "high"], help="YOLO model quality")
    args = parser.parse_args()

    # Determine YOLO model
    model_path = "yolov8s.pt" if args.quality == "high" else "yolov8n.pt"
    print(f"Using YOLO model: {model_path} with tracker config: {TRACKER_CONFIG}")

    # Load POS timestamps for billing counter abandonment checks
    pos_times = load_pos_transaction_times(args.pos)
    print(f"Loaded {len(pos_times)} POS transactions from {args.pos}")

    # Create ReID manager
    reid_manager = ReIDManager()

    # 1. Run Stockroom (CAM_4) Processor to compile staff appearance profiles (Correction 5)
    print("\n--- Running Stockroom (CAM_4) Tracker ---")
    staff_descriptors_pkl = "staff_descriptors.pkl"
    process_stockroom(
        video_path=args.cam4,
        model_path=model_path,
        tracker_config=TRACKER_CONFIG,
        clip_start_str=args.clip_start,
        output_pkl_path=staff_descriptors_pkl
    )

    # Load stockroom staff descriptors
    staff_profiles = {}
    if os.path.exists(staff_descriptors_pkl):
        with open(staff_descriptors_pkl, "rb") as f:
            staff_profiles = pickle.load(f)
    print(f"Loaded {len(staff_profiles)} staff descriptors from stockroom override.")

    # Helper function to check if a track matches stockroom staff
    def is_match_staff_profile(desc: np.ndarray) -> bool:
        for profile in staff_profiles.values():
            if calculate_cosine_similarity(desc, profile["descriptor"]) > 0.80:
                return True
        return False

    # 2. Run Entry/Exit (CAM_3) Tracker
    print("\n--- Running Entry/Exit (CAM_3) Tracker ---")
    entry_events, entry_sessions = process_entry_exit(
        video_path=args.cam3,
        model_path=model_path,
        tracker_config=TRACKER_CONFIG,
        store_id=args.store,
        clip_start_str=args.clip_start,
        reid_manager=reid_manager
    )
    print(f"CAM_3 processing generated {len(entry_events)} entry/exit events.")

    # 3. Run Makeup Zones (CAM_2) Tracker
    print("\n--- Running Makeup Zones (CAM_2) Tracker ---")
    cam2_zones = {
        "MAKEUP_MIRROR": [(50,250),(320,250),(320,800),(50,800)],
        "CENTER_AISLE": [(320,250),(550,250),(700,800),(320,800)],
        "SUMMER_DISPLAY": [(480,560),(900,560),(900,850),(480,850)],
        "MAKEUP_SHELF_MAIN": [(550,200),(1000,200),(1000,800),(550,800)],
        "MAKEUP_SHELF_PREMIUM": [(1000,200),(1600,200),(1600,800),(1000,800)]
    }
    cam2_events, cam2_tracks = process_zones(
        video_path=args.cam2,
        model_path=model_path,
        tracker_config=TRACKER_CONFIG,
        camera_id="CAM_MAKEUP_02",
        store_id=args.store,
        clip_start_str=args.clip_start,
        zones=cam2_zones
    )
    print(f"CAM_2 processing generated {len(cam2_events)} zone events.")

    # 4. Run Skincare Zones (CAM_1) Tracker
    print("\n--- Running Skincare Zones (CAM_1) Tracker ---")
    cam1_zones = {
        "SKINCARE_SHELF_LEFT": [(30,200),(500,200),(500,900),(30,900)],
        "SKINCARE_SHELF_RIGHT": [(500,200),(1100,200),(1100,900),(500,900)],
        "FRAGRANCE_DISPLAY": [(1100,400),(1500,400),(1500,900),(1100,900)]
    }
    cam1_events, cam1_tracks = process_zones(
        video_path=args.cam1,
        model_path=model_path,
        tracker_config=TRACKER_CONFIG,
        camera_id="CAM_SKINCARE_01",
        store_id=args.store,
        clip_start_str=args.clip_start,
        zones=cam1_zones
    )
    print(f"CAM_1 processing generated {len(cam1_events)} zone events.")

    # 5. Run Billing Counter (CAM_5) Tracker
    print("\n--- Running Billing Counter (CAM_5) Tracker ---")
    cam5_events, cam5_tracks = process_billing(
        video_path=args.cam5,
        model_path=model_path,
        tracker_config=TRACKER_CONFIG,
        store_id=args.store,
        clip_start_str=args.clip_start,
        pos_transactions_times=pos_times
    )
    print(f"CAM_5 processing generated {len(cam5_events)} billing events.")

    # --- Apply Stockroom Staff Overrides to all tracks (Correction 5) ---
    for tracks_dict, events_list in [(cam1_tracks, cam1_events), (cam2_tracks, cam2_events), (cam5_tracks, cam5_events)]:
        for track_id, track_state in tracks_dict.items():
            if is_match_staff_profile(track_state["descriptor"]):
                track_state["is_staff"] = True
                # Override events of this track
                for evt in events_list:
                    if evt["visitor_id"] == f"TRACK_{track_id}":
                        evt["is_staff"] = True

    # --- FIFO Visitor ID Propagation (Correction 4) ---
    print("\n--- Running FIFO visitor_id Propagation ---")
    # Step 1: Collect and sort ENTRY events from CAM_3
    entry_events_sorted = sorted(
        [e for e in entry_events if e["event_type"] in ("ENTRY", "REENTRY")],
        key=lambda x: datetime.fromisoformat(x["timestamp"])
    )
    
    # Track list of visitor sessions and exits
    open_sessions = []
    
    # We walk chronologically through all CAM_3 events to build open_sessions list dynamically
    cam3_sorted = sorted(entry_events, key=lambda x: datetime.fromisoformat(x["timestamp"]))
    
    def propagate_ids(tracks_dict: Dict[int, Any], events_list: List[Dict[str, Any]], cam_id: str):
        assigned_visitors = set()
        
        # Sort tracks by their first enter event timestamp
        tracks_sorted = []
        for track_id, state in tracks_dict.items():
            first_enter_ts = state["enter_time"] if "enter_time" in state else state["zone_enter_time"]
            if first_enter_ts is None:
                first_enter_ts = datetime.fromisoformat(args.clip_start)
            tracks_sorted.append((track_id, first_enter_ts))
            
        tracks_sorted.sort(key=lambda x: x[1])
        
        for track_id, first_ts in tracks_sorted:
            candidates = [
                s for s in entry_events_sorted
                if abs(datetime.fromisoformat(s["timestamp"]) - first_ts) <= MAX_PROPAGATION_WINDOW
                and s["visitor_id"] not in assigned_visitors
            ]

            if len(candidates) == 1:
                visitor_id = candidates[0]["visitor_id"]
                assigned_visitors.add(visitor_id)
            else:
                visitor_id = "VIS_SYN_" + uuid.uuid4().hex[:8]

            tracks_dict[track_id]["visitor_id"] = visitor_id
                
            # Propagate to all events for this track_id (Step 3 of algorithm)
            for evt in events_list:
                if evt["visitor_id"] == f"TRACK_{track_id}":
                    evt["visitor_id"] = visitor_id

    propagate_ids(cam1_tracks, cam1_events, "CAM_SKINCARE_01")
    propagate_ids(cam2_tracks, cam2_events, "CAM_MAKEUP_02")
    propagate_ids(cam5_tracks, cam5_events, "CAM_BILLING_05")

    # Merge all events
    all_events = entry_events + cam1_events + cam2_events + cam5_events
    # Sort events by timestamp
    all_events.sort(key=lambda x: datetime.fromisoformat(x["timestamp"]))

    # Add sequential order of session_seq per visitor_id session
    visitor_seq_counters = {}
    for evt in all_events:
        vid = evt["visitor_id"]
        if vid not in visitor_seq_counters:
            visitor_seq_counters[vid] = 1
        else:
            visitor_seq_counters[vid] += 1
        evt["metadata"]["session_seq"] = visitor_seq_counters[vid]

    # Write events to output JSONL
    print(f"\nWriting events to {args.out}...")
    with open(args.out, "w", encoding="utf-8") as f:
        for evt in all_events:
            f.write(json.dumps(evt) + "\n")

    # Print summary (Step 18)
    total_events = len(all_events)
    events_by_type = {}
    events_by_cam = {}
    staff_count = 0
    customer_count = 0

    for evt in all_events:
        etype = evt["event_type"]
        cam = evt["camera_id"]
        is_st = evt["is_staff"]

        events_by_type[etype] = events_by_type.get(etype, 0) + 1
        events_by_cam[cam] = events_by_cam.get(cam, 0) + 1
        if is_st:
            staff_count += 1
        else:
            customer_count += 1

    print("\n" + "═"*50)
    print(" PIPELINE EVENT GENERATION SUMMARY")
    print("═"*50)
    print(f"Total events written: {total_events}")
    print(f"Customer events:      {customer_count}")
    print(f"Staff events:         {staff_count}")
    print("\nEvents by Camera:")
    for cam, count in events_by_cam.items():
        print(f"  {cam:<18}: {count}")
    print("\nEvents by Type:")
    for etype, count in events_by_type.items():
        print(f"  {etype:<22}: {count}")
    print("═"*50)

if __name__ == "__main__":
    main()
