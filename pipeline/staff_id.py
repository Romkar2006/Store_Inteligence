import cv2
import numpy as np
from typing import List, Tuple, Optional

def extract_torso_crop(frame: np.ndarray, bbox: Tuple[float, float, float, float]) -> Optional[np.ndarray]:
    """Extract the torso crop (top 45% of the bounding box) (Step 13)."""
    try:
        x1, y1, x2, y2 = map(int, bbox)
        h = y2 - y1
        torso_y2 = y1 + int(h * 0.45)
        
        # Clip to frame boundaries
        fh, fw = frame.shape[:2]
        x1 = max(0, min(x1, fw - 1))
        x2 = max(0, min(x2, fw - 1))
        y1 = max(0, min(y1, fh - 1))
        torso_y2 = max(y1, min(torso_y2, fh - 1))
        
        if x2 <= x1 or torso_y2 <= y1:
            return None
            
        return frame[y1:torso_y2, x1:x2]
    except Exception:
        return None

def get_black_fraction(torso_crop: Optional[np.ndarray]) -> float:
    """Calculate the fraction of black pixels (S < 70, V < 90) in HSV space (Step 13)."""
    if torso_crop is None or torso_crop.size == 0:
        return 0.0
    try:
        hsv = cv2.cvtColor(torso_crop, cv2.COLOR_BGR2HSV)
        s = hsv[:, :, 1]
        v = hsv[:, :, 2]
        
        # Torso criteria: S < 70 and V < 90
        black_pixels = np.sum((s < 70) & (v < 90))
        total_pixels = torso_crop.shape[0] * torso_crop.shape[1]
        return float(black_pixels / total_pixels) if total_pixels > 0 else 0.0
    except Exception:
        return 0.0

def get_lateral_movement_score(centroid_history: List[Tuple[float, float]]) -> int:
    """Calculate lateral movement ratio over the last 20 centroids (Step 13)."""
    coords = centroid_history[-20:]
    if len(coords) < 2:
        return 0
    try:
        dxs = [abs(coords[i][0] - coords[i-1][0]) for i in range(1, len(coords))]
        dys = [abs(coords[i][1] - coords[i-1][1]) for i in range(1, len(coords))]
        
        mean_dx = sum(dxs) / len(dxs)
        mean_dy = sum(dys) / len(dys)
        
        ratio = mean_dx / (mean_dy + 1e-3)
        return 1 if ratio > 2.5 else 0
    except Exception:
        return 0

def classify_staff(
    camera_id: str,
    first_centroid_x: float,
    centroid_history: List[Tuple[float, float]],
    torso_crop: Optional[np.ndarray]
) -> bool:
    """
    Composite 3-signal staff classifier.
    Requires at least 2 of 3 signals to flag as staff (is_staff=True).
    """
    # Signal 1: Torso color
    black_frac = get_black_fraction(torso_crop)
    score_colour = 1 if black_frac > 0.35 else 0
    
    # Signal 2: Entry side (CAM_2 specific, skip/default 0 for others)
    score_entry = 0
    if camera_id in ("CAM_MAKEUP_02", "CAM_2"):
        score_entry = 1 if first_centroid_x > 400 else 0
        
    # Signal 3: Lateral movement
    score_lateral = get_lateral_movement_score(centroid_history)
    
    total_score = score_colour + score_entry + score_lateral
    return total_score >= 2
