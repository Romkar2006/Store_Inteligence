from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncConnection
from datetime import datetime, timedelta
import uuid

from app.db import get_db_conn, events
from app.models import AnomalyResponse, AnomalyItem
from app.metrics import get_store_metrics

router = APIRouter()

@router.get("/stores/{store_id}/anomalies", response_model=AnomalyResponse)
async def get_store_anomalies(store_id: str, conn: AsyncConnection = Depends(get_db_conn)):
    anomalies = []
    
    # 1. Fetch latest event timestamp for timezone and baseline tracking
    latest_ts_query = select(events.c.timestamp).where(
        events.c.store_id == store_id
    ).order_by(events.c.timestamp.desc()).limit(1)
    res_latest = await conn.execute(latest_ts_query)
    latest_row = res_latest.fetchone()
    
    if not latest_row:
        # No events in database -> no anomalies, return checked_at as current time
        return AnomalyResponse(
            store_id=store_id,
            anomalies=[],
            checked_at=datetime.now().isoformat()
        )
        
    latest_store_ts = datetime.fromisoformat(latest_row[0])
    checked_at_str = latest_store_ts.isoformat()

    # --- RULE 1: BILLING_QUEUE_SPIKE ---
    # Fetch current queue depth (latest queue depth from join events)
    queue_query = select(events.c.queue_depth).where(
        events.c.store_id == store_id,
        events.c.event_type == 'BILLING_QUEUE_JOIN',
        events.c.is_staff == 0
    ).order_by(events.c.timestamp.desc()).limit(1)
    res_queue = await conn.execute(queue_query)
    queue_row = res_queue.fetchone()
    current_depth = queue_row[0] if queue_row else 0

    if current_depth > 4:
        severity = "CRITICAL" if current_depth >= 8 else "WARN"
        anomalies.append(
            AnomalyItem(
                anomaly_id=str(uuid.uuid4()),
                type="BILLING_QUEUE_SPIKE",
                severity=severity,
                detected_at=checked_at_str,
                detail=f"Queue depth is {current_depth} — far above normal threshold of 4",
                suggested_action="Deploy additional staff to billing counter immediately"
            )
        )

    # --- RULE 2: CONVERSION_DROP ---
    # Fetch today's metrics
    metrics = await get_store_metrics(store_id, conn)
    conversion_rate = metrics.conversion_rate
    # 7-day average baseline is 0.28. Trigger if conversion_rate < (0.28 * 0.80) = 0.224
    if conversion_rate < 0.224:
        anomalies.append(
            AnomalyItem(
                anomaly_id=str(uuid.uuid4()),
                type="CONVERSION_DROP",
                severity="WARN",
                detected_at=checked_at_str,
                detail=f"Conversion rate is {conversion_rate:.4f} — below 80% of historical average (0.28)",
                suggested_action="Review staff deployment and zone activity in last 2 hours"
            )
        )

    # --- RULE 3: DEAD_ZONE ---
    # Get all unique zone_ids ever seen in this store (excluding null)
    zones_query = select(events.c.zone_id).where(
        events.c.store_id == store_id,
        events.c.zone_id.isnot(None)
    ).distinct()
    res_zones = await conn.execute(zones_query)
    zone_ids = [row[0] for row in res_zones.fetchall()]

    for zone_id in zone_ids:
        # Fetch latest ZONE_ENTER or ZONE_DWELL event for this zone
        latest_zone_query = select(events.c.timestamp).where(
            events.c.store_id == store_id,
            events.c.zone_id == zone_id,
            events.c.event_type.in_(['ZONE_ENTER', 'ZONE_DWELL'])
        ).order_by(events.c.timestamp.desc()).limit(1)
        res_latest_zone = await conn.execute(latest_zone_query)
        zone_row = res_latest_zone.fetchone()
        
        if zone_row:
            latest_zone_ts = datetime.fromisoformat(zone_row[0])
            # Check if difference exceeds 30 minutes
            time_diff = latest_store_ts - latest_zone_ts
            if time_diff > timedelta(minutes=30):
                anomalies.append(
                    AnomalyItem(
                        anomaly_id=str(uuid.uuid4()),
                        type="DEAD_ZONE",
                        severity="INFO",
                        detected_at=checked_at_str,
                        detail=f"No activity detected in zone {zone_id} since {zone_row[0]}",
                        suggested_action=f"Check camera feed for {zone_id} — no activity detected in 30 min"
                    )
                )

    return AnomalyResponse(
        store_id=store_id,
        anomalies=anomalies,
        checked_at=checked_at_str
    )
