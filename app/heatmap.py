from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncConnection
from typing import Dict, Any

from app.db import get_db_conn, events
from app.models import HeatmapResponse, HeatmapZone

router = APIRouter()

@router.get("/stores/{store_id}/heatmap", response_model=HeatmapResponse)
async def get_store_heatmap(store_id: str, conn: AsyncConnection = Depends(get_db_conn)):
    # 1. Fetch total unique customer sessions in the store for data confidence (Correction 3 - exclude staff)
    total_sessions_query = select(func.count(func.distinct(events.c.visitor_id))).where(
        events.c.store_id == store_id,
        events.c.is_staff == 0
    )
    res_total = await conn.execute(total_sessions_query)
    total_sessions = res_total.scalar() or 0
    data_confidence = "HIGH" if total_sessions >= 20 else "LOW"

    # 2. Fetch visit count (ZONE_ENTER) per zone
    visit_query = select(
        events.c.zone_id,
        func.count()
    ).where(
        events.c.store_id == store_id,
        events.c.event_type == 'ZONE_ENTER',
        events.c.is_staff == 0,
        events.c.zone_id.isnot(None)
    ).group_by(events.c.zone_id)
    res_visit = await conn.execute(visit_query)
    visits_per_zone = {row[0]: row[1] for row in res_visit.fetchall()}

    # 3. Fetch average dwell per zone (ZONE_EXIT)
    dwell_query = select(
        events.c.zone_id,
        func.avg(events.c.dwell_ms)
    ).where(
        events.c.store_id == store_id,
        events.c.event_type == 'ZONE_EXIT',
        events.c.is_staff == 0,
        events.c.zone_id.isnot(None)
    ).group_by(events.c.zone_id)
    res_dwell = await conn.execute(dwell_query)
    dwell_per_zone = {row[0]: round(row[1], 2) for row in res_dwell.fetchall()}

    # 4. Find all active zones
    all_zones = set(visits_per_zone.keys()).union(dwell_per_zone.keys())

    # Build intermediate zone stats
    zone_stats = []
    max_avg_dwell = 0.0

    for zone_id in all_zones:
        visit_count = visits_per_zone.get(zone_id, 0)
        avg_dwell = dwell_per_zone.get(zone_id, 0.0)
        if avg_dwell > max_avg_dwell:
            max_avg_dwell = avg_dwell
        
        zone_stats.append({
            "zone_id": zone_id,
            "visit_count": visit_count,
            "avg_dwell_ms": int(avg_dwell)
        })

    # 5. Normalize heat score based on max average dwell across all zones
    heatmap_zones = []
    for stat in zone_stats:
        heat_score = 0
        if max_avg_dwell > 0:
            heat_score = round((stat["avg_dwell_ms"] / max_avg_dwell) * 100)
        
        heatmap_zones.append(
            HeatmapZone(
                zone_id=stat["zone_id"],
                visit_count=stat["visit_count"],
                avg_dwell_ms=stat["avg_dwell_ms"],
                heat_score=heat_score,
                data_confidence=data_confidence
            )
        )

    # Sort heatmap zones by heat_score descending for standard view
    heatmap_zones.sort(key=lambda x: x.heat_score, reverse=True)

    return HeatmapResponse(
        store_id=store_id,
        zones=heatmap_zones
    )
