from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

class EventMetadata(BaseModel):
    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: Optional[int] = None

class EventIn(BaseModel):
    event_id: str
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: str
    timestamp: str  # ISO-8601 offset string format
    zone_id: Optional[str] = None
    dwell_ms: int = 0
    is_staff: bool = False
    confidence: float
    metadata: EventMetadata

class IngestRequest(BaseModel):
    events: List[EventIn] = Field(..., max_length=500)

class IngestError(BaseModel):
    event_id: Optional[str] = None
    reason: str

class IngestResponse(BaseModel):
    accepted: int
    rejected: int
    errors: List[IngestError]

class MetricsResponse(BaseModel):
    store_id: str
    date: str
    unique_visitors: int
    conversion_rate: float
    avg_dwell_per_zone: Dict[str, float]
    current_queue_depth: int
    abandonment_rate: float
    data_window: str

class FunnelStage(BaseModel):
    stage: str
    visitors: int
    drop_off_pct: float

class FunnelResponse(BaseModel):
    store_id: str
    funnel: List[FunnelStage]
    session_count: int

class HeatmapZone(BaseModel):
    zone_id: str
    visit_count: int
    avg_dwell_ms: int
    heat_score: int
    data_confidence: str  # HIGH or LOW

class HeatmapResponse(BaseModel):
    store_id: str
    zones: List[HeatmapZone]

class AnomalyItem(BaseModel):
    anomaly_id: str
    type: str
    severity: str
    detected_at: str
    detail: str
    suggested_action: str

class AnomalyResponse(BaseModel):
    store_id: str
    anomalies: List[AnomalyItem]
    checked_at: str

class HealthResponse(BaseModel):
    status: str
    db_connected: bool
    last_event_per_store: Dict[str, str]
    stale_feeds: List[str]
    uptime_seconds: int
    checked_at: str

class AskRequest(BaseModel):
    question: str

class AskResponse(BaseModel):
    store_id: str
    question: str
    answer: str
    mode: str

class FlowNode(BaseModel):
    id: str

class FlowLink(BaseModel):
    source: str
    target: str
    value: int

class FlowResponse(BaseModel):
    store_id: str
    nodes: List[FlowNode]
    links: List[FlowLink]

