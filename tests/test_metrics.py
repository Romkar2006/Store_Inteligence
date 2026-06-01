# PROMPT: Generate pytest tests for a FastAPI GET /stores/{store_id}/metrics endpoint. Include tests for empty stores, excluding staff events from metrics, verifying conversion rate calculation using POS data, and checking that empty visitor lists don't trigger divide-by-zero errors.
# CHANGES MADE: Added explicit test_metrics_unknown_store. Pre-configured a mock POS transaction database. Ensured timezone parsing matches IST +05:30.

import os
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import MetaData, select

# Set up test database path before importing app/db
TEST_DB_PATH = "test_store_intelligence_metrics.db"
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

def make_mock_event(event_id: str, store_id="ST1008", event_type="ZONE_ENTER", visitor_id="VIS_00000001", is_staff=False, timestamp="2026-04-10T20:10:02+05:30", zone_id="MAKEUP_MIRROR", queue_depth=None):
    return {
        "event_id": event_id,
        "store_id": store_id,
        "camera_id": "CAM_ENTRY_03",
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": timestamp,
        "zone_id": zone_id,
        "dwell_ms": 10000 if event_type == "ZONE_EXIT" else 0,
        "is_staff": is_staff,
        "confidence": 0.95,
        "metadata": {
            "queue_depth": queue_depth,
            "sku_zone": "MAKEUP_TESTER",
            "session_seq": 1
        }
    }

@pytest.mark.asyncio
async def test_metrics_normal():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Ingest 3 customer visitors events
        evts = [
            make_mock_event("e1", visitor_id="VIS_1", event_type="ZONE_ENTER", zone_id="MAKEUP_MIRROR"),
            make_mock_event("e2", visitor_id="VIS_1", event_type="ZONE_EXIT", zone_id="MAKEUP_MIRROR"),
            make_mock_event("e3", visitor_id="VIS_2", event_type="ZONE_ENTER", zone_id="CENTER_AISLE"),
            make_mock_event("e4", visitor_id="VIS_2", event_type="ZONE_EXIT", zone_id="CENTER_AISLE")
        ]
        await ac.post("/events/ingest", json={"events": evts})
        
        response = await ac.get("/stores/ST1008/metrics")
        assert response.status_code == 200
        data = response.json()
        assert data["unique_visitors"] == 2
        assert "MAKEUP_MIRROR" in data["avg_dwell_per_zone"]
        assert data["avg_dwell_per_zone"]["MAKEUP_MIRROR"] == 10000.0

@pytest.mark.asyncio
async def test_metrics_empty_store():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/stores/ST1008/metrics")
        assert response.status_code == 200
        data = response.json()
        assert data["unique_visitors"] == 0
        assert data["conversion_rate"] == 0.0
        assert data["avg_dwell_per_zone"] == {}

@pytest.mark.asyncio
async def test_metrics_staff_excluded():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Ingest 1 customer, 1 staff
        evts = [
            make_mock_event("e1", visitor_id="VIS_1", is_staff=False, event_type="ZONE_ENTER"),
            make_mock_event("e2", visitor_id="VIS_2", is_staff=True, event_type="ZONE_ENTER")
        ]
        await ac.post("/events/ingest", json={"events": evts})
        
        response = await ac.get("/stores/ST1008/metrics")
        assert response.status_code == 200
        data = response.json()
        assert data["unique_visitors"] == 1  # Excludes staff visitor

@pytest.mark.asyncio
async def test_metrics_conversion_rate():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Write a mock POS transaction directly
        async with test_async_engine.begin() as conn:
            await conn.execute(pos_transactions.insert().values(
                transaction_id="TX_1001",
                store_id="ST1008",
                timestamp="2026-04-10T20:12:00+05:30",  # Falls within 5 mins of 20:10:02
                basket_value_inr=500.0
            ))
            
        # Ingest billing counter join + entry
        evts = [
            make_mock_event("e1", visitor_id="VIS_BUYER", event_type="ZONE_ENTER", zone_id="BILLING_COUNTER", timestamp="2026-04-10T20:10:02+05:30"),
            make_mock_event("e2", visitor_id="VIS_BUYER", event_type="BILLING_QUEUE_JOIN", queue_depth=2, timestamp="2026-04-10T20:10:05+05:30")
        ]
        await ac.post("/events/ingest", json={"events": evts})
        
        response = await ac.get("/stores/ST1008/metrics")
        assert response.status_code == 200
        data = response.json()
        assert data["unique_visitors"] == 1
        assert data["conversion_rate"] == 1.0  # 1 buyer / 1 visitor

@pytest.mark.asyncio
async def test_metrics_zero_division_safe():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Ingest customer who goes to billing counter, but no transactions exist
        evts = [
            make_mock_event("e1", visitor_id="VIS_NOBUY", event_type="ZONE_ENTER", zone_id="BILLING_COUNTER", timestamp="2026-04-10T20:10:02+05:30")
        ]
        await ac.post("/events/ingest", json={"events": evts})
        
        response = await ac.get("/stores/ST1008/metrics")
        assert response.status_code == 200
        data = response.json()
        assert data["unique_visitors"] == 1
        assert data["conversion_rate"] == 0.0  # Safe conversion, no divide by zero

@pytest.mark.asyncio
async def test_metrics_unknown_store():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/stores/FAKE_999/metrics")
        assert response.status_code == 200
        data = response.json()
        assert data["unique_visitors"] == 0
        assert data["conversion_rate"] == 0.0
        assert data["current_queue_depth"] == 0
