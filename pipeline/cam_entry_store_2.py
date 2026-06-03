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
Y_LINE = 540
X_RANGE = (0, 960)
DETECTION_CONFIDENCE = 0.25

def process_entry_exit(
    video_path: str,
    model_path: str,
    tracker_config: Optional[str],
    camera_id: str,
    store_id: str,
    clip_start_str: str,
    reid_manager: ReIDManager,
    frame_skip: int = 3,
    min_conf: float = DETECTION_CONFIDENCE
):
    """
    Process Store 2 entry video to detect ENTRY, EXIT, and REENTRY events.
    Midline horizontal crossing at y = 540.
    """
    clip_start = datetime.fromisoformat(clip_start_str)
    if clip_start.tzinfo is None:
        clip_start = clip_start.replace(tzinfo=IST)
        
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"Error: Could not open entry video {video_path}")
        return [], {}

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    
    # Load YOLO model
    model = YOLO(model_path)
    
    track_history = {}
    active_sessions = {}
    first_centroids = {}
    centroid_history = {}
    
    events_emitted = []
    
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
                if box.id is None:
                    continue
                    
                track_id = int(box.id[0])
                conf = float(box.conf[0])
                bbox = box.xyxy[0].cpu().numpy()
                x1, y1, x2, y2 = bbox
                
                # Bottom-centre of bbox is the foot point
                foot_x = (x1 + x2) / 2.0
                foot_y = y2
                
                # Centroid
                centroid_x = (x1 + x2) / 2.0
                centroid_y = (y1 + y2) / 2.0
                
                if track_id not in track_history:
                    track_history[track_id] = []
                    first_centroids[track_id] = centroid_x
                    centroid_history[track_id] = []
                    
                    visitor_id = "VIS_" + uuid.uuid4().hex[:8]
                    active_sessions[track_id] = {
                        "visitor_id": visitor_id,
                        "entry_time": timestamp_str,
                        "last_zone": None,
                        "is_staff": False
                    }
                    
                track_history[track_id].append((foot_x, foot_y))
                centroid_history[track_id].append((centroid_x, centroid_y))
                
                if len(track_history[track_id]) >= 2:
                    prev_x, prev_y = track_history[track_id][-2]
                    curr_x, curr_y = foot_x, foot_y
                    
                    # Horizontal gate span checking
                    if x_range[0] <= curr_x <= x_range[1]:
                        # ENTRY: bottom to top crossing (y decreases)
                        if prev_y > y_line >= curr_y:
                            frame_crossings.append({
                                "track_id": track_id,
                                "type": "ENTRY",
                                "bbox": bbox,
                                "conf": conf
                            })
                        # EXIT: top to bottom crossing (y increases)
                        elif prev_y < y_line <= curr_y:
                            frame_crossings.append({
                                "track_id": track_id,
                                "type": "EXIT",
                                "bbox": bbox,
                                "conf": conf
                            })
                            
        for crossing in frame_crossings:
            track_id = crossing["track_id"]
            c_type = crossing["type"]
            bbox = crossing["bbox"]
            conf = crossing["conf"]
            
            torso_crop = extract_torso_crop(frame, bbox)
            is_staff = classify_staff(camera_id, first_centroids[track_id], centroid_history[track_id], torso_crop)
            
            desc = compute_appearance_descriptor(frame, bbox)
            visitor_id = active_sessions[track_id]["visitor_id"]
            active_sessions[track_id]["is_staff"] = is_staff
            
            if c_type == "ENTRY":
                matched_id = reid_manager.check_reentry(desc, timestamp)
                if matched_id:
                    visitor_id = matched_id
                    active_sessions[track_id]["visitor_id"] = visitor_id
                    evt = create_event(
                        store_id=store_id,
                        camera_id=camera_id,
                        visitor_id=visitor_id,
                        event_type="REENTRY",
                        timestamp=timestamp_str,
                        confidence=conf,
                        is_staff=is_staff,
                        session_seq=1
                    )
                else:
                    evt = create_event(
                        store_id=store_id,
                        camera_id=camera_id,
                        visitor_id=visitor_id,
                        event_type="ENTRY",
                        timestamp=timestamp_str,
                        confidence=conf,
                        is_staff=is_staff,
                        session_seq=1
                    )
                events_emitted.append(evt)
                
            elif c_type == "EXIT":
                reid_manager.register_exit(visitor_id, desc, timestamp)
                evt = create_event(
                    store_id=store_id,
                    camera_id=camera_id,
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
