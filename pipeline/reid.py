import cv2
import numpy as np
from datetime import datetime, timedelta
from typing import List, Tuple, Optional

def compute_appearance_descriptor(frame: np.ndarray, bbox: Tuple[float, float, float, float]) -> np.ndarray:
    """
    Compute a 48-dimensional appearance descriptor (Step 14).
    Normalized 16-bin HSV histogram of the upper-body crop.
    """
    try:
        x1, y1, x2, y2 = map(int, bbox)
        h = y2 - y1
        upper_y2 = y1 + int(h * 0.50)  # Top 50% of bbox
        
        fh, fw = frame.shape[:2]
        x1 = max(0, min(x1, fw - 1))
        x2 = max(0, min(x2, fw - 1))
        y1 = max(0, min(y1, fh - 1))
        upper_y2 = max(y1, min(upper_y2, fh - 1))
        
        if x2 <= x1 or upper_y2 <= y1:
            return np.zeros(48, dtype=np.float32)
            
        crop = frame[y1:upper_y2, x1:x2]
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        
        # Calculate histograms for Hue (0-180), Saturation (0-256), Value (0-256)
        h_hist = cv2.calcHist([hsv], [0], None, [16], [0, 180])
        s_hist = cv2.calcHist([hsv], [1], None, [16], [0, 256])
        v_hist = cv2.calcHist([hsv], [2], None, [16], [0, 256])
        
        # Concat histograms to 48-dimensional vector
        hist = np.concatenate([h_hist, s_hist, v_hist]).flatten()
        
        # Normalize vector using L2 Norm
        norm = np.linalg.norm(hist)
        if norm > 0:
            hist = hist / norm
        return hist
    except Exception:
        return np.zeros(48, dtype=np.float32)

def calculate_cosine_similarity(desc1: np.ndarray, desc2: np.ndarray) -> float:
    """Calculate the cosine similarity between two normalized vectors."""
    try:
        dot = np.dot(desc1, desc2)
        norm1 = np.linalg.norm(desc1)
        norm2 = np.linalg.norm(desc2)
        if norm1 > 0 and norm2 > 0:
            return float(dot / (norm1 * norm2))
        return 0.0
    except Exception:
        return 0.0

class ReIDManager:
    def __init__(self, similarity_threshold: float = 0.82):
        self.similarity_threshold = similarity_threshold
        # Rolling buffer: List of tuples (visitor_id, descriptor, exit_time)
        self.buffer: List[Tuple[str, np.ndarray, datetime]] = []

    def register_exit(self, visitor_id: str, descriptor: np.ndarray, exit_time: datetime):
        """Register an exit event and add the descriptor to the buffer."""
        # Evict older than 10 mins before adding
        self.cleanup(exit_time)
        self.buffer.append((visitor_id, descriptor, exit_time))

    def check_reentry(self, descriptor: np.ndarray, current_time: datetime) -> Optional[str]:
        """
        Check if descriptor matches an exit in the last 10 minutes.
        Returns the visitor_id if found, otherwise None.
        """
        self.cleanup(current_time)
        best_similarity = -1.0
        matched_visitor_id = None
        
        for visitor_id, stored_desc, exit_time in self.buffer:
            sim = calculate_cosine_similarity(descriptor, stored_desc)
            if sim > self.similarity_threshold and sim > best_similarity:
                best_similarity = sim
                matched_visitor_id = visitor_id
                
        return matched_visitor_id

    def cleanup(self, current_time: datetime):
        """Remove entries older than 10 minutes from the buffer."""
        cutoff_time = current_time - timedelta(minutes=10)
        self.buffer = [entry for entry in self.buffer if entry[2] >= cutoff_time]
