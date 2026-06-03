import os
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import MetaData, select

TEST_DB_PATH = "test_store_intelligence_ask.db"
os.environ["DB_PATH"] = TEST_DB_PATH
# Prevent tests from calling the external Gemini API
os.environ.pop("GEMINI_API_KEY", None)

from app.main import app
from app.db import get_db_conn, events, pos_transactions, metadata

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

async def insert_mock_events(conn, event_list):
    await conn.execute(events.insert(), event_list)
    await conn.commit()

# Mock event template helper
def make_event(event_id, event_type, visitor_id, timestamp="2026-04-10T20:10:02+05:30", zone_id=None, dwell_ms=0, queue_depth=None):
    return {
        "event_id": event_id,
        "store_id": "ST1008",
        "camera_id": "CAM_ENTRY_03",
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": timestamp,
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": 0,
        "confidence": 0.95,
        "queue_depth": queue_depth,
        "sku_zone": None,
        "session_seq": 1,
        "ingested_at": "2026-06-03T17:00:00Z"
    }

@pytest.mark.anyio
async def test_ask_empty_db():
    # Test behaviour when DB has no events
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post("/stores/ST1008/ask", json={"question": "how many visitors today?"})
        assert response.status_code == 200
        data = response.json()
        assert data["store_id"] == "ST1008"
        assert "0 unique shoppers" in data["answer"]
        assert data["mode"] == "fallback"

@pytest.mark.anyio
async def test_ask_dwell_time_question():
    # Insert mock events representing zone dwells
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Get active db connection
        async with test_async_engine.connect() as conn:
            # Visitor 1 enters and exits zone
            await insert_mock_events(conn, [
                make_event("id-1", "ENTRY", "VIS_01"),
                make_event("id-2", "ZONE_ENTER", "VIS_01", zone_id="MAKEUP_MIRROR"),
                make_event("id-3", "ZONE_EXIT", "VIS_01", zone_id="MAKEUP_MIRROR", dwell_ms=30000),
                make_event("id-4", "ZONE_ENTER", "VIS_02", zone_id="SKINCARE_SHELF_LEFT"),
                make_event("id-5", "ZONE_EXIT", "VIS_02", zone_id="SKINCARE_SHELF_LEFT", dwell_ms=10000),
            ])
        
        response = await ac.post("/stores/ST1008/ask", json={"question": "Which zone had the most dwell time today?"})
        assert response.status_code == 200
        data = response.json()
        assert "busiest zone" in data["answer"].lower()
        assert "MAKEUP_MIRROR" in data["answer"]
        assert "30s" in data["answer"]  # 30000ms is 30s

@pytest.mark.anyio
async def test_ask_queue_abandonment():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async with test_async_engine.connect() as conn:
            # 2 join queue, 1 abandon
            await insert_mock_events(conn, [
                make_event("id-1", "ENTRY", "VIS_01"),
                make_event("id-2", "BILLING_QUEUE_JOIN", "VIS_01", queue_depth=3),
                make_event("id-3", "BILLING_QUEUE_ABANDON", "VIS_01"),
                make_event("id-4", "ENTRY", "VIS_02"),
                make_event("id-5", "BILLING_QUEUE_JOIN", "VIS_02", queue_depth=2),
            ])
            
        response = await ac.post("/stores/ST1008/ask", json={"question": "How many customers abandoned the queue?"})
        assert response.status_code == 200
        data = response.json()
        assert "billing queue depth" in data["answer"].lower()
        # 1 abandon out of 2 joins = 50.0%
        assert "50.0%" in data["answer"]

@pytest.mark.anyio
async def test_ask_conversion_rate():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async with test_async_engine.connect() as conn:
            # 1 visitor enters billing counter area at 20:10:00
            await insert_mock_events(conn, [
                make_event("id-1", "ENTRY", "VIS_01", timestamp="2026-04-10T20:10:00+05:30"),
                make_event("id-2", "ZONE_ENTER", "VIS_01", timestamp="2026-04-10T20:10:00+05:30", zone_id="BILLING_COUNTER"),
                make_event("id-3", "BILLING_QUEUE_JOIN", "VIS_01", timestamp="2026-04-10T20:10:00+05:30", queue_depth=1),
            ])
            # Insert POS transaction at 20:12:00 (within 5 minutes window)
            await conn.execute(pos_transactions.insert(), [{
                "store_id": "ST1008",
                "timestamp": "2026-04-10T20:12:00+05:30",
                "basket_value_inr": 1500.0,
                "transaction_id": "TXN_0001"
            }])
            await conn.commit()
            
        response = await ac.post("/stores/ST1008/ask", json={"question": "what is the purchase conversion rate?"})
        assert response.status_code == 200
        data = response.json()
        assert "conversion rate" in data["answer"].lower()
        # 1 visitor converted out of 1 unique visitor = 100.0%
        assert "100.0%" in data["answer"]
