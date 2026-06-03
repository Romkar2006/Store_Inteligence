import os
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import MetaData, select

TEST_DB_PATH = "test_store_intelligence_flow.db"
os.environ["DB_PATH"] = TEST_DB_PATH

from app.main import app
from app.db import get_db_conn, events, metadata

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

def make_event(event_id, event_type, visitor_id, timestamp, zone_id=None):
    return {
        "event_id": event_id,
        "store_id": "ST1008",
        "camera_id": "CAM_01",
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": timestamp,
        "zone_id": zone_id,
        "dwell_ms": 0,
        "is_staff": 0,
        "confidence": 0.95,
        "queue_depth": None,
        "sku_zone": None,
        "session_seq": 1,
        "ingested_at": "2026-06-03T17:00:00Z"
    }

@pytest.mark.anyio
async def test_flow_empty_db():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/stores/ST1008/flow")
        assert response.status_code == 200
        data = response.json()
        assert data["store_id"] == "ST1008"
        assert len(data["nodes"]) == 0
        assert len(data["links"]) == 0

@pytest.mark.anyio
async def test_flow_journey_transitions():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async with test_async_engine.connect() as conn:
            # VIS_01: ENTRY -> MAKEUP_MIRROR -> EXIT
            # VIS_02: ENTRY -> SKINCARE_SHELF_LEFT -> BILLING_QUEUE -> EXIT
            await insert_mock_events(conn, [
                make_event("e-1", "ENTRY", "VIS_01", "2026-04-10T20:10:00+05:30"),
                make_event("e-2", "ZONE_ENTER", "VIS_01", "2026-04-10T20:10:30+05:30", zone_id="MAKEUP_MIRROR"),
                make_event("e-3", "EXIT", "VIS_01", "2026-04-10T20:11:00+05:30"),
                
                make_event("e-4", "ENTRY", "VIS_02", "2026-04-10T20:10:05+05:30"),
                make_event("e-5", "ZONE_ENTER", "VIS_02", "2026-04-10T20:10:45+05:30", zone_id="SKINCARE_SHELF_LEFT"),
                make_event("e-6", "BILLING_QUEUE_JOIN", "VIS_02", "2026-04-10T20:11:15+05:30"),
                make_event("e-7", "EXIT", "VIS_02", "2026-04-10T20:11:55+05:30"),
            ])
            
        response = await ac.get("/stores/ST1008/flow")
        assert response.status_code == 200
        data = response.json()
        
        # Verify nodes are computed correctly
        nodes = [n["id"] for n in data["nodes"]]
        assert "ENTRY" in nodes
        assert "MAKEUP_MIRROR" in nodes
        assert "SKINCARE_SHELF_LEFT" in nodes
        assert "BILLING_QUEUE" in nodes
        assert "EXIT" in nodes
        
        # Verify links (transitions)
        links = data["links"]
        # Should have ENTRY -> MAKEUP_MIRROR with value 1
        link1 = next(l for l in links if l["source"] == "ENTRY" and l["target"] == "MAKEUP_MIRROR")
        assert link1["value"] == 1
        
        # Should have ENTRY -> SKINCARE_SHELF_LEFT with value 1
        link2 = next(l for l in links if l["source"] == "ENTRY" and l["target"] == "SKINCARE_SHELF_LEFT")
        assert link2["value"] == 1
        
        # Should have BILLING_QUEUE -> EXIT with value 1
        link3 = next(l for l in links if l["source"] == "BILLING_QUEUE" and l["target"] == "EXIT")
        assert link3["value"] == 1
