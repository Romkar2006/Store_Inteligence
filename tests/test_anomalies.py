# PROMPT: Generate pytest tests for a FastAPI GET /stores/{store_id}/anomalies and GET /health endpoints. Verify queue depth anomalies (WARN and CRITICAL), dead zone detections (INFO), health service stale feed tracking, and DB connection dropout handling.
# CHANGES MADE: Added explicit mock database error simulation tests and verified timezone offset offsets.

import os
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import MetaData, select
from sqlalchemy.exc import OperationalError

# Set up test database path before importing app/db
TEST_DB_PATH = "test_store_intelligence_anomalies.db"
os.environ["DB_PATH"] = TEST_DB_PATH

from app.main import app
from app.db import get_db_conn, events, pos_transactions, metadata

# Test database connection setup
DATABASE_URL = f"sqlite+aiosqlite:///{TEST_DB_PATH}"
test_async_engine = None

@pytest_asyncio.fixture(autouse=True)
async def setup_test_db():
    global test_async_engine
    test_async_engine = create_async_engine(DATABASE_URL)
    
    async with test_async_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
        await conn.run_sync(metadata.create_all)
        
    async def override_get_db_conn():
        async with test_async_engine.connect() as conn:
            yield conn
            
    app.dependency_overrides[get_db_conn] = override_get_db_conn
    
    yield
    
    app.dependency_overrides.pop(get_db_conn, None)
    await test_async_engine.dispose()
    
    if os.path.exists(TEST_DB_PATH):
        try:
            os.remove(TEST_DB_PATH)
        except Exception:
            pass

def make_mock_event(event_id: str, event_type: str, queue_depth=None, timestamp="2026-04-10T20:10:02+05:30", zone_id=None):
    return {
        "event_id": event_id,
        "store_id": "ST1008",
        "camera_id": "CAM_ENTRY_03",
        "visitor_id": "VIS_001",
        "event_type": event_type,
        "timestamp": timestamp,
        "zone_id": zone_id,
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.95,
        "metadata": {
            "queue_depth": queue_depth,
            "sku_zone": None,
            "session_seq": 1
        }
    }

@pytest.mark.asyncio
async def test_anomaly_queue_spike_warn():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        evts = [make_mock_event("e1", "BILLING_QUEUE_JOIN", queue_depth=6)]
        await ac.post("/events/ingest", json={"events": evts})
        
        response = await ac.get("/stores/ST1008/anomalies")
        assert response.status_code == 200
        data = response.json()
        
        spikes = [a for a in data["anomalies"] if a["type"] == "BILLING_QUEUE_SPIKE"]
        assert len(spikes) == 1
        assert spikes[0]["severity"] == "WARN"

@pytest.mark.asyncio
async def test_anomaly_queue_spike_critical():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        evts = [make_mock_event("e1", "BILLING_QUEUE_JOIN", queue_depth=9)]
        await ac.post("/events/ingest", json={"events": evts})
        
        response = await ac.get("/stores/ST1008/anomalies")
        data = response.json()
        spikes = [a for a in data["anomalies"] if a["type"] == "BILLING_QUEUE_SPIKE"]
        assert len(spikes) == 1
        assert spikes[0]["severity"] == "CRITICAL"

@pytest.mark.asyncio
async def test_anomaly_no_queue_spike():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        evts = [make_mock_event("e1", "BILLING_QUEUE_JOIN", queue_depth=2)]
        await ac.post("/events/ingest", json={"events": evts})
        
        response = await ac.get("/stores/ST1008/anomalies")
        data = response.json()
        spikes = [a for a in data["anomalies"] if a["type"] == "BILLING_QUEUE_SPIKE"]
        assert len(spikes) == 0

@pytest.mark.asyncio
async def test_anomaly_dead_zone():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Ingest a zone event in MAKEUP_MIRROR at 20:10
        # Then ingest a general event at 20:50 (40 mins later)
        # This makes MAKEUP_MIRROR dead (>30 mins without activity)
        evts = [
            make_mock_event("e1", "ZONE_ENTER", zone_id="MAKEUP_MIRROR", timestamp="2026-04-10T20:10:00+05:30"),
            make_mock_event("e2", "ENTRY", timestamp="2026-04-10T20:50:00+05:30")
        ]
        await ac.post("/events/ingest", json={"events": evts})
        
        response = await ac.get("/stores/ST1008/anomalies")
        data = response.json()
        dead_zones = [a for a in data["anomalies"] if a["type"] == "DEAD_ZONE"]
        assert len(dead_zones) == 1
        assert dead_zones[0]["severity"] == "INFO"
        assert "MAKEUP_MIRROR" in dead_zones[0]["suggested_action"]

@pytest.mark.asyncio
async def test_anomaly_empty_list_not_null():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # No events -> returns empty list
        response = await ac.get("/stores/ST1008/anomalies")
        data = response.json()
        assert "anomalies" in data
        assert data["anomalies"] == []

@pytest.mark.asyncio
async def test_health_stale_feed():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Ingest an event from long ago
        evts = [make_mock_event("e1", "ENTRY", timestamp="2026-04-10T20:10:00+05:30")]
        await ac.post("/events/ingest", json={"events": evts})
        
        response = await ac.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert "ST1008" in data["stale_feeds"]

@pytest.mark.asyncio
async def test_health_db_unavailable():
    # Force db error by raising exception in connection override
    async def bad_get_db_conn():
        raise OperationalError("SELECT", {}, Exception("Database connection failure"))
        yield None
        
    old_override = app.dependency_overrides.get(get_db_conn)
    app.dependency_overrides[get_db_conn] = bad_get_db_conn
    try:
        async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as ac:
            response = await ac.get("/health")
            assert response.status_code == 503
            data = response.json()
            assert data["status"] == "DEGRADED"
            assert data["db_connected"] is False
    finally:
        # Re-register healthy db connection mock
        if old_override is not None:
            app.dependency_overrides[get_db_conn] = old_override
        else:
            app.dependency_overrides.pop(get_db_conn, None)
