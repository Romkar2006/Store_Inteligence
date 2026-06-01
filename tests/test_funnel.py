# PROMPT: Generate pytest tests for a FastAPI GET /stores/{store_id}/funnel endpoint. Validate session-level funnel tracking, visitor re-entry grouping (single visitor session), drop-off percentages calculation, and zero-activity scenarios.
# CHANGES MADE: Enforced custom setup for localized timestamps (+05:30) and verified drop-off calculation edge cases.

import os
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import MetaData, select

# Set up test database path before importing app/db
TEST_DB_PATH = "test_store_intelligence_funnel.db"
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

def make_mock_event(event_id: str, visitor_id: str, event_type: str, zone_id=None, timestamp="2026-04-10T20:10:02+05:30"):
    return {
        "event_id": event_id,
        "store_id": "ST1008",
        "camera_id": "CAM_ENTRY_03",
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": timestamp,
        "zone_id": zone_id,
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.95,
        "metadata": {
            "queue_depth": None,
            "sku_zone": None,
            "session_seq": 1
        }
    }

@pytest.mark.asyncio
async def test_funnel_happy_path():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Ingest mock POS data
        async with test_async_engine.begin() as conn:
            await conn.execute(pos_transactions.insert().values(
                transaction_id="TX_H1", store_id="ST1008", timestamp="2026-04-10T20:12:00+05:30", basket_value_inr=150.0
            ))

        # Ingest a visitor session progressing all the way
        evts = [
            make_mock_event("e1", "VIS_H1", "ENTRY"),
            make_mock_event("e2", "VIS_H1", "ZONE_ENTER", zone_id="MAKEUP_MIRROR"),
            make_mock_event("e3", "VIS_H1", "ZONE_ENTER", zone_id="BILLING_COUNTER", timestamp="2026-04-10T20:10:10+05:30"),
            make_mock_event("e4", "VIS_H1", "BILLING_QUEUE_JOIN")
        ]
        await ac.post("/events/ingest", json={"events": evts})
        
        response = await ac.get("/stores/ST1008/funnel")
        assert response.status_code == 200
        data = response.json()
        
        # Verify counts per stage
        stages = {s["stage"]: s for s in data["funnel"]}
        assert stages["ENTRY"]["visitors"] == 1
        assert stages["ZONE_VISIT"]["visitors"] == 1
        assert stages["BILLING_QUEUE"]["visitors"] == 1
        assert stages["PURCHASE"]["visitors"] == 1

@pytest.mark.asyncio
async def test_funnel_reentry_no_double_count():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Ingest user entering, exiting, and re-entering
        evts = [
            make_mock_event("e1", "VIS_R1", "ENTRY", timestamp="2026-04-10T20:10:00+05:30"),
            make_mock_event("e2", "VIS_R1", "EXIT", timestamp="2026-04-10T20:11:00+05:30"),
            make_mock_event("e3", "VIS_R1", "REENTRY", timestamp="2026-04-10T20:12:00+05:30")
        ]
        await ac.post("/events/ingest", json={"events": evts})
        
        response = await ac.get("/stores/ST1008/funnel")
        data = response.json()
        assert data["session_count"] == 1  # Deduped to 1 session
        assert data["funnel"][0]["visitors"] == 1

@pytest.mark.asyncio
async def test_funnel_drop_off_correct():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 4 unique visitors enter.
        # 3 enter a zone.
        # 2 join the billing queue.
        # 1 makes a purchase.
        async with test_async_engine.begin() as conn:
            await conn.execute(pos_transactions.insert().values(
                transaction_id="TX_F1", store_id="ST1008", timestamp="2026-04-10T20:12:00+05:30", basket_value_inr=150.0
            ))

        evts = [
            # Entry (4 visitors)
            make_mock_event("e1", "V1", "ENTRY"),
            make_mock_event("e2", "V2", "ENTRY"),
            make_mock_event("e3", "V3", "ENTRY"),
            make_mock_event("e4", "V4", "ENTRY"),
            
            # Zone visit (3 visitors: V1, V2, V3)
            make_mock_event("e5", "V1", "ZONE_ENTER", "MAKEUP_MIRROR"),
            make_mock_event("e6", "V2", "ZONE_ENTER", "CENTER_AISLE"),
            make_mock_event("e7", "V3", "ZONE_ENTER", "SUMMER_DISPLAY"),
            
            # Queue Join (2 visitors: V1, V2)
            make_mock_event("e8", "V1", "ZONE_ENTER", "BILLING_COUNTER", timestamp="2026-04-10T20:10:02+05:30"),
            make_mock_event("e9", "V1", "BILLING_QUEUE_JOIN"),
            make_mock_event("e10", "V2", "ZONE_ENTER", "BILLING_COUNTER", timestamp="2026-04-10T20:05:00+05:30"),
            make_mock_event("e11", "V2", "BILLING_QUEUE_JOIN"),
        ]
        await ac.post("/events/ingest", json={"events": evts})
        
        response = await ac.get("/stores/ST1008/funnel")
        data = response.json()
        stages = {s["stage"]: s for s in data["funnel"]}
        
        # Verify drop off percentages
        # ZONE_VISIT drop off: (4 - 3)/4 = 25.0%
        assert stages["ZONE_VISIT"]["drop_off_pct"] == 25.0
        # BILLING_QUEUE drop off: (3 - 2)/3 = 33.3%
        assert stages["BILLING_QUEUE"]["drop_off_pct"] == 33.3
        # PURCHASE drop off: (2 - 1)/2 = 50.0% (assuming V1 purchase, V2 none)
        assert stages["PURCHASE"]["drop_off_pct"] == 50.0

@pytest.mark.asyncio
async def test_funnel_no_purchases():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        evts = [
            make_mock_event("e1", "VIS_N1", "ENTRY"),
            make_mock_event("e2", "VIS_N1", "ZONE_ENTER", "MAKEUP_MIRROR"),
            make_mock_event("e3", "VIS_N1", "ZONE_ENTER", "BILLING_COUNTER"),
            make_mock_event("e4", "VIS_N1", "BILLING_QUEUE_JOIN")
        ]
        await ac.post("/events/ingest", json={"events": evts})
        
        response = await ac.get("/stores/ST1008/funnel")
        data = response.json()
        stages = {s["stage"]: s for s in data["funnel"]}
        assert stages["BILLING_QUEUE"]["visitors"] == 1
        assert stages["PURCHASE"]["visitors"] == 0
        assert stages["PURCHASE"]["drop_off_pct"] == 100.0

@pytest.mark.asyncio
async def test_funnel_empty_store():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/stores/ST1008/funnel")
        data = response.json()
        assert data["session_count"] == 0
        for stage in data["funnel"]:
            assert stage["visitors"] == 0
            assert stage["drop_off_pct"] == 0.0
