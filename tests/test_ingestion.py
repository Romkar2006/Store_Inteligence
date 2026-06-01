# PROMPT: Generate pytest tests for a FastAPI POST /events/ingest endpoint. Include idempotency test, partial success test, batch limit test, and missing field validation test.
# CHANGES MADE: Added edge case for empty batch. Fixed assertion on partial success — should return HTTP 200 not 207. Added test_ingest_duplicate_event_id which AI missed.

import os
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import MetaData, select

# Set up test database path before importing app/db
TEST_DB_PATH = "test_store_intelligence_ingest.db"
os.environ["DB_PATH"] = TEST_DB_PATH

from app.main import app
from app.db import get_db_conn, events, metadata

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

def make_mock_event(event_id: str, store_id="ST1008", event_type="ZONE_ENTER", visitor_id="VIS_00000001", camera_id="CAM_ENTRY_03"):
    return {
        "event_id": event_id,
        "store_id": store_id,
        "camera_id": camera_id,
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": "2026-04-10T20:10:02+05:30",
        "zone_id": "MAKEUP_MIRROR" if event_type.startswith("ZONE") else None,
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.95,
        "metadata": {
            "queue_depth": None,
            "sku_zone": "MAKEUP_TESTER",
            "session_seq": 1
        }
    }

@pytest.mark.asyncio
async def test_ingest_valid_batch():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        batch = [make_mock_event(f"evt-{i}") for i in range(10)]
        response = await ac.post("/events/ingest", json={"events": batch})
        assert response.status_code == 200
        data = response.json()
        assert data["accepted"] == 10
        assert data["rejected"] == 0
        assert len(data["errors"]) == 0

@pytest.mark.asyncio
async def test_ingest_idempotency():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        batch = [make_mock_event(f"evt-{i}") for i in range(10)]
        # First call
        response1 = await ac.post("/events/ingest", json={"events": batch})
        assert response1.status_code == 200
        assert response1.json()["accepted"] == 10
        # Second call
        response2 = await ac.post("/events/ingest", json={"events": batch})
        assert response2.status_code == 200
        assert response2.json()["accepted"] == 0

@pytest.mark.asyncio
async def test_ingest_partial_success():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        batch = [make_mock_event(f"evt-{i}") for i in range(8)]
        # Add 2 malformed events (e.g. missing confidence or bad type)
        malformed1 = make_mock_event("evt-bad1")
        del malformed1["confidence"]
        malformed2 = make_mock_event("evt-bad2")
        malformed2["confidence"] = "invalid-type"
        
        batch.extend([malformed1, malformed2])
        
        response = await ac.post("/events/ingest", json={"events": batch})
        assert response.status_code == 200  # Partial success returns 200
        data = response.json()
        assert data["accepted"] == 8
        assert data["rejected"] == 2
        assert len(data["errors"]) == 2

@pytest.mark.asyncio
async def test_ingest_empty_batch():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/events/ingest", json={"events": []})
        assert response.status_code == 200
        data = response.json()
        assert data["accepted"] == 0
        assert data["rejected"] == 0

@pytest.mark.asyncio
async def test_ingest_exceeds_limit():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        batch = [make_mock_event(f"evt-{i}") for i in range(501)]
        response = await ac.post("/events/ingest", json={"events": batch})
        assert response.status_code == 422  # Exceeds batch limit

@pytest.mark.asyncio
async def test_ingest_invalid_event_type():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        event = make_mock_event("evt-bad-type", event_type="MADE_UP")
        response = await ac.post("/events/ingest", json={"events": [event]})
        assert response.status_code == 200
        data = response.json()
        assert data["accepted"] == 0
        assert data["rejected"] == 1
        assert "Invalid event_type" in data["errors"][0]["reason"]

@pytest.mark.asyncio
async def test_ingest_missing_required_field():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        event = make_mock_event("evt-missing")
        del event["store_id"]
        response = await ac.post("/events/ingest", json={"events": [event]})
        assert response.status_code == 200
        data = response.json()
        assert data["accepted"] == 0
        assert data["rejected"] == 1
        assert "Field required" in data["errors"][0]["reason"]

@pytest.mark.asyncio
async def test_ingest_duplicate_event_id():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Same event_id twice in single batch
        event1 = make_mock_event("evt-dup")
        event2 = make_mock_event("evt-dup")
        response = await ac.post("/events/ingest", json={"events": [event1, event2]})
        assert response.status_code == 200
        data = response.json()
        assert data["accepted"] == 1
        assert data["rejected"] == 0  # Duplicates skipped silently, not rejected
