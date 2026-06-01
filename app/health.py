import time
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncConnection
from datetime import datetime, timezone, timedelta

from app.db import get_db_conn, events

router = APIRouter()

# Track start time of the module
START_TIME = time.time()
IST = timezone(timedelta(hours=5, minutes=30))

@router.get("/health", response_model=None)
async def get_health(conn: AsyncConnection = Depends(get_db_conn)):
    checked_at = datetime.now(IST)
    uptime_seconds = int(time.time() - START_TIME)
    
    try:
        # 1. Fetch last event timestamp per store
        stmt = select(
            events.c.store_id,
            func.max(events.c.timestamp)
        ).group_by(events.c.store_id)
        
        res = await conn.execute(stmt)
        rows = res.fetchall()
        
        last_event_per_store = {}
        stale_feeds = []
        
        for store_id, last_ts_str in rows:
            if last_ts_str:
                last_event_per_store[store_id] = last_ts_str
                # Parse timestamp and check if older than 10 minutes relative to current server time
                last_ts = datetime.fromisoformat(last_ts_str)
                if checked_at - last_ts > timedelta(minutes=10):
                    stale_feeds.append(store_id)
            else:
                stale_feeds.append(store_id)
                
        return {
            "status": "OK",
            "db_connected": True,
            "last_event_per_store": last_event_per_store,
            "stale_feeds": stale_feeds,
            "uptime_seconds": uptime_seconds,
            "checked_at": checked_at.isoformat()
        }
        
    except Exception as e:
        # Return 503 if database connection fails or queries timeout (Correction 3)
        return JSONResponse(
            status_code=503,
            content={
                "status": "DEGRADED",
                "db_connected": False,
                "error": "Database unavailable"
            }
        )
