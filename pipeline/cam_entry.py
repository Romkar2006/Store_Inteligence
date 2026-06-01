import cv2
import os
import argparse
import json
import uuid
import sys
from pathlib import Path
import numpy as np
from datetime import datetime, timedelta, timezone
from typing import Optional
from ultralytics import YOLO

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.emit import create_event
from pipeline.reid import compute_appearance_descriptor, ReIDManager
from pipeline.staff_id import extract_torso_crop, classify_staff

IST = timezone(timedelta(hours=5, minutes=30))
Y_LINE = 580
X_RANGE = (200, 1200)
DETECTION_CONFIDENCE = 0.25

def process_entry_exit(
    video_path: str,
    model_path: str,
    tracker_config: Optional[str],
    store_id: str,
    clip_start_str: str,
    reid_manager: ReIDManager,
    frame_skip: int = 3,
    min_conf: float = DETECTION_CONFIDENCE
):
    """
    Process CAM_3 to detect ENTRY, EXIT, and REENTRY events (Step 15).
    """
    # 1. Parse start time in IST (Correction 1)
    clip_start = datetime.fromisoformat(clip_start_str)
    if clip_start.tzinfo is None:
        clip_start = clip_start.replace(tzinfo=IST)
        
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"Error: Could not open entry video {video_path}")
        return [], {}

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    
    # Load YOLO model
    model = YOLO(model_path)
    
    # Track states
    # track_id -> list of foot points (x, y)
    track_history = {}
    # track_id -> visitor_id
    active_sessions = {}
    # track_id -> first_centroid_x
    first_centroids = {}
    # track_id -> list of raw centroids (x, y)
    centroid_history = {}
    
    events_emitted = []
    
    # Active tracks count in a frame for group entry detection
    crossings_this_frame = []
    
    # Virtual crossing line properties
    y_line = Y_LINE
    x_range = X_RANGE
    
    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
            
        if frame_idx % frame_skip != 0:
            frame_idx += 1
            continue
            
        timestamp = clip_start + timedelta(seconds=frame_idx / fps)
        timestamp_str = timestamp.isoformat()
        
        # Run tracking on this frame
        # If tracker_config is botsort, we pass it, otherwise bytetrack
        tracker = tracker_config if tracker_config else "bytetrack.yaml"
        results = model.track(
            source=frame,
            persist=True,
            classes=[0],  # Person only
            conf=min_conf,
            tracker=tracker,
            verbose=False
        )
        
        frame_crossings = []
        
        if results and len(results) > 0 and results[0].boxes is not None:
            boxes = results[0].boxes
            for box in boxes:
                # Get tracking details
                if box.id is None:
                    continue
                    
                track_id = int(box.id[0])
                conf = float(box.conf[0])
                bbox = box.xyxy[0].cpu().numpy()
                x1, y1, x2, y2 = bbox
                
                # Bottom-centre of bbox is the foot point (Step 15)
                foot_x = (x1 + x2) / 2.0
                foot_y = y2
                
                # Centroid
                centroid_x = (x1 + x2) / 2.0
                centroid_y = (y1 + y2) / 2.0
                
                # Update history
                if track_id not in track_history:
                    track_history[track_id] = []
                    first_centroids[track_id] = centroid_x
                    centroid_history[track_id] = []
                    
                    # Generate a new visitor_id for session (Step 15)
                    # We check ReID similarity later if they cross the line
                    visitor_id = "VIS_" + uuid.uuid4().hex[:8]
                    active_sessions[track_id] = {
                        "visitor_id": visitor_id,
                        "entry_time": timestamp_str,
                        "last_zone": None,
                        "is_staff": False
                    }
                    
                track_history[track_id].append((foot_x, foot_y))
                centroid_history[track_id].append((centroid_x, centroid_y))
                
                # Check for line crossing if we have a previous point
                if len(track_history[track_id]) >= 2:
                    prev_x, prev_y = track_history[track_id][-2]
                    curr_x, curr_y = foot_x, foot_y
                    
                    # Check if the person moved across the doorway divider while staying within the vertical gate.
                    if x_range[0] <= curr_y <= x_range[1]:
                        # ENTRY: right to left across the divider.
                        if prev_x > y_line >= curr_x:
                            frame_crossings.append({
                                "track_id": track_id,
                                "type": "ENTRY",
                                "bbox": bbox,
                                "conf": conf
                            })
                        # EXIT: left to right across the divider.
                        elif prev_x < y_line <= curr_x:
                            frame_crossings.append({
                                "track_id": track_id,
                                "type": "EXIT",
                                "bbox": bbox,
                                "conf": conf
                            })
                            
        # Handle group crossings and Re-ID checks
        for crossing in frame_crossings:
            track_id = crossing["track_id"]
            c_type = crossing["type"]
            bbox = crossing["bbox"]
            conf = crossing["conf"]
            
            # Torso crop for staff check
            torso_crop = extract_torso_crop(frame, bbox)
            is_staff = classify_staff("CAM_ENTRY_03", first_centroids[track_id], centroid_history[track_id], torso_crop)
            
            # Get descriptor for Re-ID (Step 14)
            desc = compute_appearance_descriptor(frame, bbox)
            
            visitor_id = active_sessions[track_id]["visitor_id"]
            active_sessions[track_id]["is_staff"] = is_staff
            
            if c_type == "ENTRY":
                # Check if this is a Re-entry (Step 14)
                matched_id = reid_manager.check_reentry(desc, timestamp)
                if matched_id:
                    # Same person re-entered!
                    visitor_id = matched_id
                    active_sessions[track_id]["visitor_id"] = visitor_id
                    
                    # Emit REENTRY event
                    evt = create_event(
                        store_id=store_id,
                        camera_id="CAM_ENTRY_03",
                        visitor_id=visitor_id,
                        event_type="REENTRY",
                        timestamp=timestamp_str,
                        confidence=conf,
                        is_staff=is_staff,
                        session_seq=1
                    )
                else:
                    # Fresh ENTRY
                    evt = create_event(
                        store_id=store_id,
                        camera_id="CAM_ENTRY_03",
                        visitor_id=visitor_id,
                        event_type="ENTRY",
                        timestamp=timestamp_str,
                        confidence=conf,
                        is_staff=is_staff,
                        session_seq=1
                    )
                events_emitted.append(evt)
                
            elif c_type == "EXIT":
                # Close session, register exit in Re-ID manager
                reid_manager.register_exit(visitor_id, desc, timestamp)
                
                evt = create_event(
                    store_id=store_id,
                    camera_id="CAM_ENTRY_03",
                    visitor_id=visitor_id,
                    event_type="EXIT",
                    timestamp=timestamp_str,
                    confidence=conf,
                    is_staff=is_staff,
                    session_seq=2
                )
                events_emitted.append(evt)
                
        frame_idx += 1
        
    cap.release()
    return events_emitted, active_sessions


def _main():
    parser = argparse.ArgumentParser(description="Run CAM_3 entry/exit detection")
    parser.add_argument("--video", required=True, help="Path to CAM_3 video")
    parser.add_argument("--store", required=True, help="Store ID")
    parser.add_argument("--clip-start", required=True, help="Clip start timestamp")
    parser.add_argument("--out", default="entry_test.jsonl", help="Output JSONL path")
    parser.add_argument("--model", default="yolov8n.pt", help="YOLO model path")
    parser.add_argument("--tracker", default=None, help="Tracker config path")
    parser.add_argument("--frame-skip", type=int, default=3, help="Process every Nth frame")
    parser.add_argument("--min-conf", type=float, default=DETECTION_CONFIDENCE, help="Detection confidence threshold")
    args = parser.parse_args()

    reid_manager = ReIDManager()
    events, _ = process_entry_exit(
        video_path=args.video,
        model_path=args.model,
        tracker_config=args.tracker,
        store_id=args.store,
        clip_start_str=args.clip_start,
        reid_manager=reid_manager,
        frame_skip=args.frame_skip,
        min_conf=args.min_conf,
    )

    with open(args.out, "w", encoding="utf-8") as output_file:
        for event in events:
            output_file.write(json.dumps(event) + "\n")

    print(f"Wrote {len(events)} events to {args.out}")


if __name__ == "__main__":
    _main()
