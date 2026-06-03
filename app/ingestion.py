from fastapi import APIRouter, Depends, Request, HTTPException
from typing import Dict, Any, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection
from datetime import datetime, timezone

from app.db import get_db_conn, events
from app.models import IngestResponse, EventIn, IngestError

router = APIRouter()

VALID_EVENT_TYPES = {
    "ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT", "ZONE_DWELL",
    "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "REENTRY"
}

@router.post("/events/ingest", response_model=IngestResponse)
async def ingest_events(
    request: Request,
    payload: Dict[str, Any],
    conn: AsyncConnection = Depends(get_db_conn)
):
    # 1. Validate payload shape and batch limits
    raw_events = payload.get("events")
    if raw_events is None or not isinstance(raw_events, list):
        raise HTTPException(status_code=422, detail="Missing or invalid 'events' list in request body")
    
    event_count = len(raw_events)
    if event_count > 500:
        raise HTTPException(status_code=422, detail=f"Batch size {event_count} exceeds maximum limit of 500")

    accepted_count = 0
    rejected_count = 0
    errors: List[IngestError] = []
    to_insert = []
    seen_in_batch = set()

    # 2. Extract event_ids to query existing entries in a single step for efficiency
    event_ids = []
    for item in raw_events:
        if isinstance(item, dict) and "event_id" in item:
            event_ids.append(str(item["event_id"]))

    existing_ids = set()
    if event_ids:
        query = select(events.c.event_id).where(events.c.event_id.in_(event_ids))
        res = await conn.execute(query)
        existing_ids = {row[0] for row in res.fetchall()}

    # 3. Process and validate each event
    for item in raw_events:
        if not isinstance(item, dict):
            rejected_count += 1
            errors.append(IngestError(event_id=None, reason="Event item must be a JSON object"))
            continue

        event_id = item.get("event_id")

        try:
            # Pydantic v2 field-level validations (handles missing fields automatically)
            event = EventIn.model_validate(item)
        except Exception as e:
            rejected_count += 1
            errors.append(IngestError(event_id=str(event_id) if event_id else None, reason=str(e)))
            continue

        # Validate event type enum strictly
        if event.event_type not in VALID_EVENT_TYPES:
            rejected_count += 1
            errors.append(IngestError(event_id=event.event_id, reason=f"Invalid event_type: {event.event_type}"))
            continue

        # Handle duplication and idempotency
        if event.event_id in existing_ids or event.event_id in seen_in_batch:
            # Skip duplicate event IDs silently as required
            continue

        seen_in_batch.add(event.event_id)
        ingested_at = datetime.now(timezone.utc).isoformat()
        
        # Store is_staff as integer 0/1
        to_insert.append({
            "event_id": event.event_id,
            "store_id": event.store_id,
            "camera_id": event.camera_id,
            "visitor_id": event.visitor_id,
            "event_type": event.event_type,
            "timestamp": event.timestamp,
            "zone_id": event.zone_id,
            "dwell_ms": event.dwell_ms,
            "is_staff": 1 if event.is_staff else 0,
            "confidence": event.confidence,
            "queue_depth": event.metadata.queue_depth,
            "sku_zone": event.metadata.sku_zone,
            "session_seq": event.metadata.session_seq,
            "ingested_at": ingested_at
        })
        accepted_count += 1

    # 4. Insert valid items inside a single database transaction
    if to_insert:
        await conn.execute(events.insert(), to_insert)
        await conn.commit()
        
        # Broadcast via WebSockets in the background to minimize response latency
        from app.websocket import manager
        import asyncio
        for event_dict in to_insert:
            asyncio.create_task(manager.broadcast_to_store(
                event_dict["store_id"],
                {
                    "type": "LIVE_EVENT",
                    "event": event_dict
                }
            ))

    # 5. Populate request state for logging middleware access
    request.state.event_count = event_count
    request.state.accepted = accepted_count
    request.state.rejected = rejected_count

    return IngestResponse(
        accepted=accepted_count,
        rejected=rejected_count,
        errors=errors
    )
