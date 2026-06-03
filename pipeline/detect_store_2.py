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
from pipeline.cam_entry_store_2 import process_entry_exit
from pipeline.cam_zones_store_2 import process_zones
from pipeline.cam_billing import process_billing

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
    parser = argparse.ArgumentParser(description="Store 2 Intelligence Orchestrator")
    parser.add_argument("--entry1", required=True, help="Path to Store 2 entry_1 video")
    parser.add_argument("--entry2", required=True, help="Path to Store 2 entry_2 video")
    parser.add_argument("--zone", required=True, help="Path to Store 2 zone video")
    parser.add_argument("--billing", required=True, help="Path to Store 2 billing video")
    parser.add_argument("--pos", required=True, help="Path to Store 2 POS CSV")
    parser.add_argument("--store", default="ST1009", help="Store ID")
    parser.add_argument("--clip-start", default="2026-04-10T20:10:02+05:30", help="Clip start timestamp (IST)")
    parser.add_argument("--out", default="events_store_2.jsonl", help="Output path for events")
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

    # 1. Run Entry/Exit 1 (CAM_ENTRY_01) Tracker
    print("\n--- Running Entry/Exit 1 (CAM_ENTRY_01) Tracker ---")
    entry1_events, entry1_sessions = process_entry_exit(
        video_path=args.entry1,
        model_path=model_path,
        tracker_config=TRACKER_CONFIG,
        camera_id="CAM_ENTRY_01",
        store_id=args.store,
        clip_start_str=args.clip_start,
        reid_manager=reid_manager
    )
    print(f"CAM_ENTRY_01 processing generated {len(entry1_events)} events.")

    # 2. Run Entry/Exit 2 (CAM_ENTRY_02) Tracker
    print("\n--- Running Entry/Exit 2 (CAM_ENTRY_02) Tracker ---")
    entry2_events, entry2_sessions = process_entry_exit(
        video_path=args.entry2,
        model_path=model_path,
        tracker_config=TRACKER_CONFIG,
        camera_id="CAM_ENTRY_02",
        store_id=args.store,
        clip_start_str=args.clip_start,
        reid_manager=reid_manager
    )
    print(f"CAM_ENTRY_02 processing generated {len(entry2_events)} events.")

    # Merge entrance events
    entry_events = entry1_events + entry2_events

    # 3. Run Zone Tracker (CAM_ZONE_01)
    print("\n--- Running Main Floor Zone (CAM_ZONE_01) Tracker ---")
    zone_polygons = {
        "MK_GONDOLA_1": [(0, 540), (480, 540), (480, 1080), (0, 1080)],
        "MK_GONDOLA_2": [(0, 0), (480, 0), (480, 540), (0, 540)],
        "MAKEUP_TABLES": [(480, 0), (960, 0), (960, 1080), (480, 1080)]
    }
    zone_events, zone_tracks = process_zones(
        video_path=args.zone,
        model_path=model_path,
        tracker_config=TRACKER_CONFIG,
        camera_id="CAM_ZONE_01",
        store_id=args.store,
        clip_start_str=args.clip_start,
        zones=zone_polygons
    )
    print(f"CAM_ZONE_01 processing generated {len(zone_events)} events.")

    # 4. Run Billing Counter Tracker (CAM_BILLING_01)
    print("\n--- Running Billing Counter (CAM_BILLING_01) Tracker ---")
    billing_events, billing_tracks = process_billing(
        video_path=args.billing,
        model_path=model_path,
        tracker_config=TRACKER_CONFIG,
        store_id=args.store,
        clip_start_str=args.clip_start,
        pos_transactions_times=pos_times,
        camera_id="CAM_BILLING_01"
    )
    print(f"CAM_BILLING_01 processing generated {len(billing_events)} events.")

    # --- FIFO Visitor ID Propagation ---
    print("\n--- Running FIFO visitor_id Propagation ---")
    # Collect and sort all ENTRY events from both entrances
    entry_events_sorted = sorted(
        [e for e in entry_events if e["event_type"] in ("ENTRY", "REENTRY")],
        key=lambda x: datetime.fromisoformat(x["timestamp"])
    )
    
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

            if len(candidates) >= 1:
                # Assign oldest matching entry session candidate
                visitor_id = candidates[0]["visitor_id"]
                assigned_visitors.add(visitor_id)
            else:
                visitor_id = "VIS_SYN_" + uuid.uuid4().hex[:8]

            tracks_dict[track_id]["visitor_id"] = visitor_id
                
            # Propagate to all events for this track_id
            for evt in events_list:
                if evt["visitor_id"] == f"TRACK_{track_id}":
                    evt["visitor_id"] = visitor_id

    propagate_ids(zone_tracks, zone_events, "CAM_ZONE_01")
    propagate_ids(billing_tracks, billing_events, "CAM_BILLING_01")

    # Merge all events
    all_events = entry_events + zone_events + billing_events
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

    # Print summary
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
    print(" STORE 2 PIPELINE EVENT GENERATION SUMMARY")
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
