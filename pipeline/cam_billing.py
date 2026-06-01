import cv2
import os
import numpy as np
from datetime import datetime, timedelta, timezone
from ultralytics import YOLO
from typing import List, Dict, Any, Tuple, Optional

from pipeline.emit import create_event
from pipeline.reid import compute_appearance_descriptor
from pipeline.staff_id import extract_torso_crop, classify_staff

IST = timezone(timedelta(hours=5, minutes=30))

def process_billing(
    video_path: str,
    model_path: str,
    tracker_config: Optional[str],
    store_id: str,
    clip_start_str: str,
    pos_transactions_times: List[datetime],
    frame_skip: int = 3,
    min_conf: float = 0.35
):
    """
    Process CAM_5 to track queue depth, queue joins, and abandonments (Step 17).
    """
    clip_start = datetime.fromisoformat(clip_start_str)
    if clip_start.tzinfo is None:
        clip_start = clip_start.replace(tzinfo=IST)
        
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"Error: Could not open billing video {video_path}")
        return []

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0  # CAM_5 is 25fps (specs check)
    model = YOLO(model_path)
    
    # Track states
    # track_id -> { 'enter_time', 'last_seen_time', 'centroid_history', 'descriptor', 'is_staff', 'visitor_id' }
    active_tracks = {}
    
    events_emitted = []
    
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
        
        seen_track_ids_this_frame = set()
        
        # Calculate current queue depth (non-staff in frame) before processing new entries
        current_queue_depth = sum(1 for state in active_tracks.values() if not state["is_staff"])
        
        if results and len(results) > 0 and results[0].boxes is not None:
            boxes = results[0].boxes
            for box in boxes:
                if box.id is None:
                    continue
                    
                track_id = int(box.id[0])
                conf = float(box.conf[0])
                bbox = box.xyxy[0].cpu().numpy()
                x1, y1, x2, y2 = bbox
                
                centroid = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
                seen_track_ids_this_frame.add(track_id)
                
                # Bounding box coordinates represent the entire frame being the billing counter
                zone_name = "BILLING_COUNTER"
                
                # Low-confidence interpolation
                low_conf_interpolated = False
                if conf < 0.50 and track_id in active_tracks:
                    low_conf_interpolated = True
                
                # Check for new track
                if track_id not in active_tracks:
                    torso_crop = extract_torso_crop(frame, bbox)
                    centroid_history = [centroid]
                    is_staff = classify_staff("CAM_BILLING_05", centroid[0], centroid_history, torso_crop)
                    
                    descriptor = compute_appearance_descriptor(frame, bbox)
                    
                    active_tracks[track_id] = {
                        "enter_time": timestamp,
                        "last_seen_time": timestamp,
                        "centroid_history": centroid_history,
                        "descriptor": descriptor,
                        "is_staff": is_staff,
                        "session_seq": 1,
                        "low_conf_interpolated": low_conf_interpolated
                    }
                    
                    # If non-staff enters the billing frame
                    if not is_staff:
                        # 1. Emit ZONE_ENTER for BILLING_COUNTER
                        evt_enter = create_event(
                            store_id=store_id,
                            camera_id="CAM_BILLING_05",
                            visitor_id=f"TRACK_{track_id}",
                            event_type="ZONE_ENTER",
                            timestamp=timestamp_str,
                            confidence=conf,
                            zone_id="BILLING_COUNTER",
                            dwell_ms=0,
                            is_staff=False,
                            session_seq=active_tracks[track_id]["session_seq"]
                        )
                        if low_conf_interpolated:
                            evt_enter["metadata"]["low_conf_interpolated"] = True
                        events_emitted.append(evt_enter)
                        active_tracks[track_id]["session_seq"] += 1
                        
                        # 2. Emit BILLING_QUEUE_JOIN if there was already someone in the queue (Step 17)
                        if current_queue_depth > 0:
                            evt_join = create_event(
                                store_id=store_id,
                                camera_id="CAM_BILLING_05",
                                visitor_id=f"TRACK_{track_id}",
                                event_type="BILLING_QUEUE_JOIN",
                                timestamp=timestamp_str,
                                confidence=conf,
                                zone_id="BILLING_COUNTER",
                                dwell_ms=0,
                                is_staff=False,
                                queue_depth=current_queue_depth,
                                session_seq=active_tracks[track_id]["session_seq"]
                            )
                            if low_conf_interpolated:
                                evt_join["metadata"]["low_conf_interpolated"] = True
                            events_emitted.append(evt_join)
                            active_tracks[track_id]["session_seq"] += 1
                            
                    else:
                        # Staff ZONE_ENTER for billing (needed for heatmap / logs)
                        evt_enter = create_event(
                            store_id=store_id,
                            camera_id="CAM_BILLING_05",
                            visitor_id=f"TRACK_{track_id}",
                            event_type="ZONE_ENTER",
                            timestamp=timestamp_str,
                            confidence=conf,
                            zone_id="BILLING_COUNTER",
                            dwell_ms=0,
                            is_staff=True,
                            session_seq=active_tracks[track_id]["session_seq"]
                        )
                        events_emitted.append(evt_enter)
                        active_tracks[track_id]["session_seq"] += 1
                else:
                    # Update existing track stats
                    active_tracks[track_id]["last_seen_time"] = timestamp
                    active_tracks[track_id]["centroid_history"].append(centroid)
                    
        # Check for tracks that disappeared (> 3 seconds) (Step 17)
        disappeared_ids = []
        for track_id, track_state in list(active_tracks.items()):
            if track_id not in seen_track_ids_this_frame:
                time_missing = (timestamp - track_state["last_seen_time"]).total_seconds()
                if time_missing > 3.0:
                    disappeared_ids.append(track_id)
                    
                    dwell_time = (track_state["last_seen_time"] - track_state["enter_time"]).total_seconds()
                    
                    # 1. Emit ZONE_EXIT for BILLING_COUNTER (if dwell >= 2s)
                    if dwell_time >= 2.0:
                        evt_exit = create_event(
                            store_id=store_id,
                            camera_id="CAM_BILLING_05",
                            visitor_id=f"TRACK_{track_id}",
                            event_type="ZONE_EXIT",
                            timestamp=track_state["last_seen_time"].isoformat(),
                            confidence=0.50,
                            zone_id="BILLING_COUNTER",
                            dwell_ms=int(dwell_time * 1000),
                            is_staff=track_state["is_staff"],
                            session_seq=track_state["session_seq"]
                        )
                        events_emitted.append(evt_exit)
                        track_state["session_seq"] += 1
                        
                    # 2. Check for abandonment if customer (Step 17)
                    if not track_state["is_staff"]:
                        converted = False
                        # Check if any POS transaction occurs within [enter_time, enter_time + 5 minutes]
                        for txn_time in pos_transactions_times:
                            diff_sec = (txn_time - track_state["enter_time"]).total_seconds()
                            if 0 <= diff_sec <= 300:
                                converted = True
                                break
                        
                        if not converted:
                            # Customer left queue without POS purchase correlation within 5 mins -> Abandon (Step 17)
                            evt_abandon = create_event(
                                store_id=store_id,
                                camera_id="CAM_BILLING_05",
                                visitor_id=f"TRACK_{track_id}",
                                event_type="BILLING_QUEUE_ABANDON",
                                timestamp=track_state["last_seen_time"].isoformat(),
                                confidence=0.50,
                                zone_id="BILLING_COUNTER",
                                dwell_ms=int(dwell_time * 1000),
                                is_staff=False,
                                session_seq=track_state["session_seq"]
                            )
                            events_emitted.append(evt_abandon)
                            
        for track_id in disappeared_ids:
            del active_tracks[track_id]
            
        frame_idx += 1
        
    cap.release()
    return events_emitted, active_tracks
