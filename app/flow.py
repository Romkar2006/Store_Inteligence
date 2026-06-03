from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection
from typing import Dict, List, Any
import structlog

from app.db import get_db_conn, events
from app.models import FlowResponse, FlowNode, FlowLink

logger = structlog.get_logger()
router = APIRouter()

@router.get("/stores/{store_id}/flow", response_model=FlowResponse)
async def get_store_flow(store_id: str, conn: AsyncConnection = Depends(get_db_conn)):
    try:
        # Fetch all non-staff events for the store ordered by visitor_id and timestamp
        stmt = select(
            events.c.visitor_id,
            events.c.event_type,
            events.c.zone_id,
            events.c.timestamp
        ).where(
            events.c.store_id == store_id,
            events.c.is_staff == 0
        ).order_by(events.c.visitor_id, events.c.timestamp.asc())
        
        res = await conn.execute(stmt)
        rows = res.fetchall()
        
        # Group events by visitor_id
        visitor_journeys = {}
        for row in rows:
            vis_id = row[0]
            evt_type = row[1]
            zone_id = row[2]
            
            # Map event type to a logical Sankey node name
            state = None
            if evt_type in ('ENTRY', 'REENTRY'):
                state = 'ENTRY'
            elif evt_type == 'EXIT':
                state = 'EXIT'
            elif evt_type == 'BILLING_QUEUE_JOIN':
                state = 'BILLING_QUEUE'
            elif evt_type == 'BILLING_QUEUE_ABANDON':
                state = 'QUEUE_ABANDON'
            elif evt_type in ('ZONE_ENTER', 'ZONE_DWELL', 'ZONE_EXIT'):
                if zone_id:
                    state = zone_id
                    
            if state:
                if vis_id not in visitor_journeys:
                    visitor_journeys[vis_id] = []
                # Avoid consecutive duplicates in the sequence
                if not visitor_journeys[vis_id] or visitor_journeys[vis_id][-1] != state:
                    visitor_journeys[vis_id].append(state)
                    
        # Count transitions
        transitions = {}
        nodes_set = set()
        
        for journey in visitor_journeys.values():
            if not journey:
                continue
            
            for i in range(len(journey) - 1):
                source = journey[i]
                target = journey[i+1]
                
                # Exclude self-loops
                if source == target:
                    continue
                    
                nodes_set.add(source)
                nodes_set.add(target)
                
                pair = (source, target)
                transitions[pair] = transitions.get(pair, 0) + 1
                
        # Build FlowResponse structure
        # Sort nodes: ENTRY first, EXIT/ABANDON last, others in middle
        def node_sort_key(node_id):
            if node_id == 'ENTRY':
                return 0
            if node_id in ('EXIT', 'QUEUE_ABANDON'):
                return 99
            return 50
            
        sorted_nodes = sorted(list(nodes_set), key=node_sort_key)
        
        nodes_list = [FlowNode(id=nid) for nid in sorted_nodes]
        links_list = [
            FlowLink(source=src, target=tgt, value=val)
            for (src, tgt), val in transitions.items()
        ]
        
        return FlowResponse(
            store_id=store_id,
            nodes=nodes_list,
            links=links_list
        )
        
    except Exception as e:
        logger.error("Failed to compute flow diagram data", store_id=store_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to compute flow diagram data: {str(e)}")
