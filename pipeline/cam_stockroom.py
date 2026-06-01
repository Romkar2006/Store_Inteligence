import cv2
import os
import pickle
import numpy as np
from datetime import datetime, timedelta, timezone
from ultralytics import YOLO
from typing import Optional

from pipeline.reid import compute_appearance_descriptor

IST = timezone(timedelta(hours=5, minutes=30))

def process_stockroom(
    video_path: str,
    model_path: str,
    tracker_config: Optional[str],
    clip_start_str: str,
    output_pkl_path: str = "staff_descriptors.pkl",
    frame_skip: int = 3,
    min_conf: float = 0.35
):
    """
    Process CAM_4.mp4 (stockroom) (Correction 5).
    Extract descriptors for all tracks seen in stockroom to classify them as staff.
    Does NOT emit any events. Writes descriptors to a pickle file.
    """
    clip_start = datetime.fromisoformat(clip_start_str)
    if clip_start.tzinfo is None:
        clip_start = clip_start.replace(tzinfo=IST)
        
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"Error: Could not open stockroom video {video_path}")
        return {}

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0  # CAM_4 is 25fps (specs check)
    model = YOLO(model_path)
    
    # staff_descriptors: { track_id: { 'descriptor': np.array, 'timestamp': str, 'is_staff': True } }
    staff_descriptors = {}
    
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
            classes=[0],
            conf=min_conf,
            tracker=tracker,
            verbose=False
        )
        
        if results and len(results) > 0 and results[0].boxes is not None:
            boxes = results[0].boxes
            for box in boxes:
                if box.id is None:
                    continue
                    
                track_id = int(box.id[0])
                bbox = box.xyxy[0].cpu().numpy()
                
                # Extract appearance descriptor (Correction 5)
                desc = compute_appearance_descriptor(frame, bbox)
                
                # Register/update staff appearance in the dictionary
                # Stockroom is restricted, so anyone appearing here is staff
                staff_descriptors[track_id] = {
                    "descriptor": desc,
                    "timestamp": timestamp_str,
                    "is_staff": True
                }
                
        frame_idx += 1
        
    cap.release()
    
    # Save descriptors to pkl file (Correction 5)
    with open(output_pkl_path, "wb") as f:
        pickle.dump(staff_descriptors, f)
        
    print(f"Successfully saved {len(staff_descriptors)} staff descriptors to {output_pkl_path}")
    return staff_descriptors
