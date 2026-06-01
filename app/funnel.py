from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncConnection
from datetime import datetime
from typing import Set

from app.db import get_db_conn, events, pos_transactions
from app.models import FunnelResponse, FunnelStage

router = APIRouter()

@router.get("/stores/{store_id}/funnel", response_model=FunnelResponse)
async def get_store_funnel(store_id: str, conn: AsyncConnection = Depends(get_db_conn)):
    # 1. Fetch all unique non-staff visitor IDs (ENTRY stage)
    entry_query = select(events.c.visitor_id).where(
        events.c.store_id == store_id,
        events.c.is_staff == 0,
        events.c.event_type.in_(['ENTRY', 'REENTRY'])
    ).distinct()
    res_entry = await conn.execute(entry_query)
    entry_visitors = {row[0] for row in res_entry.fetchall()}
    entry_count = len(entry_visitors)

    # 2. Fetch visitor IDs with ZONE_ENTER events (ZONE_VISIT stage)
    zone_query = select(events.c.visitor_id).where(
        events.c.store_id == store_id,
        events.c.event_type == 'ZONE_ENTER',
        events.c.is_staff == 0
    ).distinct()
    res_zone = await conn.execute(zone_query)
    # Intersect with entry_visitors to ensure sequence consistency
    zone_visitors = {row[0] for row in res_zone.fetchall()}.intersection(entry_visitors)
    zone_count = len(zone_visitors)

    # 3. Fetch visitor IDs with BILLING_QUEUE_JOIN events (BILLING_QUEUE stage)
    queue_query = select(events.c.visitor_id).where(
        events.c.store_id == store_id,
        events.c.event_type == 'BILLING_QUEUE_JOIN',
        events.c.is_staff == 0
    ).distinct()
    res_queue = await conn.execute(queue_query)
    # Intersect to maintain funnel constraints
    queue_visitors = {row[0] for row in res_queue.fetchall()}.intersection(entry_visitors)
    queue_count = len(queue_visitors)

    # 4. Fetch visitor IDs with purchase correlation (PURCHASE stage)
    purchase_count = 0
    if queue_count > 0:
        # Get earliest billing enter times
        billing_enters_query = select(
            events.c.visitor_id,
            func.min(events.c.timestamp)
        ).where(
            events.c.store_id == store_id,
            events.c.is_staff == 0,
            events.c.event_type.in_(['ZONE_ENTER', 'ZONE_DWELL']),
            events.c.zone_id == 'BILLING_COUNTER',
            events.c.visitor_id.in_(list(queue_visitors))
        ).group_by(events.c.visitor_id)
        
        res_enters = await conn.execute(billing_enters_query)
        visitor_enters = res_enters.fetchall()

        # Get POS transactions
        pos_query = select(pos_transactions.c.timestamp).where(
            pos_transactions.c.store_id == store_id
        )
        res_pos = await conn.execute(pos_query)
        pos_times = [datetime.fromisoformat(row[0]) for row in res_pos.fetchall()]

        for visitor_id, enter_time_str in visitor_enters:
            if not enter_time_str:
                continue
            enter_time = datetime.fromisoformat(enter_time_str)
            # Check for POS transaction within 5 minutes [enter_time, enter_time + 5 mins]
            for txn_time in pos_times:
                diff_sec = (txn_time - enter_time).total_seconds()
                if 0 <= diff_sec <= 300:
                    purchase_count += 1
                    break

    # Calculate drop-off percentages with zero-safe math
    drop_off_zone = round(((entry_count - zone_count) / entry_count) * 100, 1) if entry_count > 0 else 0.0
    drop_off_queue = round(((zone_count - queue_count) / zone_count) * 100, 1) if zone_count > 0 else 0.0
    drop_off_purchase = round(((queue_count - purchase_count) / queue_count) * 100, 1) if queue_count > 0 else 0.0

    funnel_stages = [
        FunnelStage(stage="ENTRY", visitors=entry_count, drop_off_pct=0.0),
        FunnelStage(stage="ZONE_VISIT", visitors=zone_count, drop_off_pct=drop_off_zone),
        FunnelStage(stage="BILLING_QUEUE", visitors=queue_count, drop_off_pct=drop_off_queue),
        FunnelStage(stage="PURCHASE", visitors=purchase_count, drop_off_pct=drop_off_purchase)
    ]

    return FunnelResponse(
        store_id=store_id,
        funnel=funnel_stages,
        session_count=entry_count
    )
