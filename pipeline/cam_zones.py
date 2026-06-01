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

def check_zone(centroid: Tuple[float, float], zones: Dict[str, List[Tuple[int, int]]]) -> Optional[str]:
    """Check which zone polygon contains the centroid using pointPolygonTest (Step 16)."""
    cx, cy = centroid
    # Ignore ceiling area or off-screen right
    if cy < 200 or cx > 1700:
        return None
        
    for name, poly in zones.items():
        poly_arr = np.array(poly, dtype=np.int32)
        # pointPolygonTest returns >= 0 if inside or on edge
        dist = cv2.pointPolygonTest(poly_arr, (float(cx), float(cy)), False)
        if dist >= 0:
            return name
    return None

def process_zones(
    video_path: str,
    model_path: str,
    tracker_config: Optional[str],
    camera_id: str,
    store_id: str,
    clip_start_str: str,
    zones: Dict[str, List[Tuple[int, int]]],
    frame_skip: int = 3,
    min_conf: float = 0.35
):
    """
    Process CAM_1 or CAM_2 to track zone entries, exits, and dwells (Step 16).
    """
    clip_start = datetime.fromisoformat(clip_start_str)
    if clip_start.tzinfo is None:
        clip_start = clip_start.replace(tzinfo=IST)
        
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"Error: Could not open zone video {video_path}")
        return []

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    model = YOLO(model_path)
    
    # Track states
    # track_id -> { 'current_zone', 'zone_enter_time', 'last_dwell_emit_time', 'last_seen_time', 'last_seen_frame', 'centroid_history', 'descriptor', 'is_staff', 'first_centroid_x', 'visitor_id' }
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
                
                # Check zone of centroid
                zone_name = check_zone(centroid, zones)
                
                # Occlusion handling (Step 16)
                low_conf_interpolated = False
                if conf < 0.50 and track_id in active_tracks:
                    # Keep using last known zone
                    zone_name = active_tracks[track_id]["current_zone"]
                    low_conf_interpolated = True
                
                # Initialize new tracks
                if track_id not in active_tracks:
                    torso_crop = extract_torso_crop(frame, bbox)
                    centroid_history = [centroid]
                    is_staff = classify_staff(camera_id, centroid[0], centroid_history, torso_crop)
                    
                    # Compute appearance descriptor for database-wide staff check
                    descriptor = compute_appearance_descriptor(frame, bbox)
                    
                    active_tracks[track_id] = {
                        "current_zone": None,
                        "zone_enter_time": None,
                        "last_dwell_emit_time": None,
                        "last_seen_time": timestamp,
                        "centroid_history": centroid_history,
                        "descriptor": descriptor,
                        "is_staff": is_staff,
                        "first_centroid_x": centroid[0],
                        "visitor_id": None,  # Will be mapped by orchestrator
                        "session_seq": 1,
                        "low_conf_interpolated": low_conf_interpolated
                    }
                else:
                    # Update existing track stats
                    active_tracks[track_id]["last_seen_time"] = timestamp
                    active_tracks[track_id]["centroid_history"].append(centroid)
                    active_tracks[track_id]["low_conf_interpolated"] = low_conf_interpolated
                
                track_state = active_tracks[track_id]
                prev_zone = track_state["current_zone"]
                
                # Handle zone transitions (Step 16)
                if zone_name != prev_zone:
                    # 1. Emit exit for previous zone if dwell time >= 2s
                    if prev_zone is not None:
                        dwell_time = (timestamp - track_state["zone_enter_time"]).total_seconds()
                        if dwell_time >= 2.0:
                            # Emit ZONE_EXIT
                            evt = create_event(
                                store_id=store_id,
                                camera_id=camera_id,
                                visitor_id=f"TRACK_{track_id}",  # Placeholder resolved by orchestrator
                                event_type="ZONE_EXIT",
                                timestamp=timestamp_str,
                                confidence=conf,
                                zone_id=prev_zone,
                                dwell_ms=int(dwell_time * 1000),
                                is_staff=track_state["is_staff"],
                                session_seq=track_state["session_seq"]
                            )
                            if low_conf_interpolated:
                                evt["metadata"]["low_conf_interpolated"] = True
                            events_emitted.append(evt)
                            track_state["session_seq"] += 1
                            
                    # 2. Emit ENTER for new zone
                    track_state["current_zone"] = zone_name
                    track_state["zone_enter_time"] = timestamp
                    track_state["last_dwell_emit_time"] = timestamp
                    
                    if zone_name is not None:
                        evt = create_event(
                            store_id=store_id,
                            camera_id=camera_id,
                            visitor_id=f"TRACK_{track_id}",
                            event_type="ZONE_ENTER",
                            timestamp=timestamp_str,
                            confidence=conf,
                            zone_id=zone_name,
                            dwell_ms=0,
                            is_staff=track_state["is_staff"],
                            session_seq=track_state["session_seq"]
                        )
                        if low_conf_interpolated:
                            evt["metadata"]["low_conf_interpolated"] = True
                        events_emitted.append(evt)
                        track_state["session_seq"] += 1
                        
                # Handle periodic ZONE_DWELL emits (every 30s)
                elif zone_name is not None:
                    time_in_zone = (timestamp - track_state["zone_enter_time"]).total_seconds()
                    time_since_last_dwell_emit = (timestamp - track_state["last_dwell_emit_time"]).total_seconds()
                    
                    if time_since_last_dwell_emit >= 30.0:
                        evt = create_event(
                            store_id=store_id,
                            camera_id=camera_id,
                            visitor_id=f"TRACK_{track_id}",
                            event_type="ZONE_DWELL",
                            timestamp=timestamp_str,
                            confidence=conf,
                            zone_id=zone_name,
                            dwell_ms=int(time_in_zone * 1000),
                            is_staff=track_state["is_staff"],
                            session_seq=track_state["session_seq"]
                        )
                        if low_conf_interpolated:
                            evt["metadata"]["low_conf_interpolated"] = True
                        events_emitted.append(evt)
                        track_state["last_dwell_emit_time"] = timestamp
                        track_state["session_seq"] += 1
                        
        # Check for tracks that disappeared (> 3 seconds) (Step 16)
        disappeared_ids = []
        for track_id, track_state in list(active_tracks.items()):
            if track_id not in seen_track_ids_this_frame:
                time_missing = (timestamp - track_state["last_seen_time"]).total_seconds()
                if time_missing > 3.0:
                    disappeared_ids.append(track_id)
                    # Emit exit for last known zone if dwell >= 2s
                    if track_state["current_zone"] is not None:
                        dwell_time = (track_state["last_seen_time"] - track_state["zone_enter_time"]).total_seconds()
                        if dwell_time >= 2.0:
                            evt = create_event(
                                store_id=store_id,
                                camera_id=camera_id,
                                visitor_id=f"TRACK_{track_id}",
                                event_type="ZONE_EXIT",
                                timestamp=track_state["last_seen_time"].isoformat(),
                                confidence=0.50,  # Fallback confidence
                                zone_id=track_state["current_zone"],
                                dwell_ms=int(dwell_time * 1000),
                                is_staff=track_state["is_staff"],
                                session_seq=track_state["session_seq"]
                            )
                            events_emitted.append(evt)
                            
        for track_id in disappeared_ids:
            del active_tracks[track_id]
            
        frame_idx += 1
        
    cap.release()
    return events_emitted, active_tracks
