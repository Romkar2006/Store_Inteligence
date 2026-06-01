from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncConnection
from datetime import datetime
from typing import Dict

from app.db import get_db_conn, events, pos_transactions
from app.models import MetricsResponse

router = APIRouter()

@router.get("/stores/{store_id}/metrics", response_model=MetricsResponse)
async def get_store_metrics(store_id: str, conn: AsyncConnection = Depends(get_db_conn)):
    # 1. Fetch unique non-staff visitors (Correction 3 - exclude staff)
    unique_visitors_query = select(func.count(func.distinct(events.c.visitor_id))).where(
        events.c.store_id == store_id,
        events.c.is_staff == 0
    )
    res_unique = await conn.execute(unique_visitors_query)
    unique_visitors = res_unique.scalar() or 0

    # 2. Fetch average dwell per zone from ZONE_EXIT events
    avg_dwell_query = select(
        events.c.zone_id,
        func.avg(events.c.dwell_ms)
    ).where(
        events.c.store_id == store_id,
        events.c.event_type == 'ZONE_EXIT',
        events.c.is_staff == 0,
        events.c.zone_id.isnot(None)
    ).group_by(events.c.zone_id)
    res_dwell = await conn.execute(avg_dwell_query)
    avg_dwell_per_zone = {row[0]: round(row[1], 2) for row in res_dwell.fetchall()}

    # 3. Fetch latest queue depth from BILLING_QUEUE_JOIN events
    queue_query = select(events.c.queue_depth).where(
        events.c.store_id == store_id,
        events.c.event_type == 'BILLING_QUEUE_JOIN',
        events.c.is_staff == 0
    ).order_by(events.c.timestamp.desc()).limit(1)
    res_queue = await conn.execute(queue_query)
    queue_row = res_queue.fetchone()
    current_queue_depth = queue_row[0] if queue_row else 0

    # 4. Fetch queue join & abandon counts for abandonment rate
    queue_counts_query = select(
        events.c.event_type,
        func.count()
    ).where(
        events.c.store_id == store_id,
        events.c.event_type.in_(['BILLING_QUEUE_JOIN', 'BILLING_QUEUE_ABANDON']),
        events.c.is_staff == 0
    ).group_by(events.c.event_type)
    res_counts = await conn.execute(queue_counts_query)
    counts = {row[0]: row[1] for row in res_counts.fetchall()}
    
    joins = counts.get('BILLING_QUEUE_JOIN', 0)
    abandons = counts.get('BILLING_QUEUE_ABANDON', 0)
    abandonment_rate = round(abandons / joins, 4) if joins > 0 else 0.0

    # 5. Conversion Rate Calculation (Correction 3 - division by zero safety)
    conversion_rate = 0.0
    if unique_visitors > 0:
        # Get earliest billing counter enter time per visitor
        billing_enters_query = select(
            events.c.visitor_id,
            func.min(events.c.timestamp)
        ).where(
            events.c.store_id == store_id,
            events.c.is_staff == 0,
            events.c.event_type.in_(['ZONE_ENTER', 'ZONE_DWELL']),
            events.c.zone_id == 'BILLING_COUNTER'
        ).group_by(events.c.visitor_id)
        res_enters = await conn.execute(billing_enters_query)
        visitor_enters = res_enters.fetchall()

        # Get POS transactions for the store
        pos_query = select(pos_transactions.c.timestamp).where(
            pos_transactions.c.store_id == store_id
        )
        res_pos = await conn.execute(pos_query)
        pos_times = [datetime.fromisoformat(row[0]) for row in res_pos.fetchall()]

        converted_visitors = 0
        for visitor_id, enter_time_str in visitor_enters:
            enter_time = datetime.fromisoformat(enter_time_str)
            # Check if any transaction occurs within [enter_time, enter_time + 5 minutes]
            converted = False
            for txn_time in pos_times:
                diff_sec = (txn_time - enter_time).total_seconds()
                if 0 <= diff_sec <= 300:
                    converted = True
                    break
            if converted:
                converted_visitors += 1

        conversion_rate = round(converted_visitors / unique_visitors, 4)

    # Determine date of events (default to today if empty)
    date_query = select(events.c.timestamp).where(
        events.c.store_id == store_id
    ).order_by(events.c.timestamp.asc()).limit(1)
    res_date = await conn.execute(date_query)
    date_row = res_date.fetchone()
    date_str = date_row[0][:10] if date_row else datetime.now().strftime("%Y-%m-%d")

    return MetricsResponse(
        store_id=store_id,
        date=date_str,
        unique_visitors=unique_visitors,
        conversion_rate=conversion_rate,
        avg_dwell_per_zone=avg_dwell_per_zone,
        current_queue_depth=current_queue_depth,
        abandonment_rate=abandonment_rate,
        data_window="today"
    )
