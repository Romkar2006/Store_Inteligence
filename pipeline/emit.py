import json
import uuid
from typing import Optional, Dict, Any

def create_event(
    store_id: str,
    camera_id: str,
    visitor_id: str,
    event_type: str,
    timestamp: str,  # ISO-8601 offset format
    confidence: float,
    zone_id: Optional[str] = None,
    dwell_ms: int = 0,
    is_staff: bool = False,
    queue_depth: Optional[int] = None,
    sku_zone: Optional[str] = None,
    session_seq: Optional[int] = None
) -> Dict[str, Any]:
    """Helper to build a schema-compliant event (Step 12)."""
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": store_id,
        "camera_id": camera_id,
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": timestamp,
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": is_staff,
        "confidence": round(float(confidence), 2),
        "metadata": {
            "queue_depth": queue_depth,
            "sku_zone": sku_zone,
            "session_seq": session_seq
        }
    }

def append_event_to_file(file_path: str, event: Dict[str, Any]):
    """Append a single event dictionary as a JSON Line to a file."""
    with open(file_path, mode='a', encoding='utf-8') as f:
        f.write(json.dumps(event) + '\n')
