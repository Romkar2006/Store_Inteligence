import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

TEST_DB_PATH = "test_store_intelligence_ws.db"
os.environ["DB_PATH"] = TEST_DB_PATH

from app.main import app
from app.db import get_db_conn, metadata, init_db

# Create test database and tables for the test client context
@pytest.fixture(autouse=True)
def setup_test_db():
    # Initialize the database (this creates the tables in sqlite)
    init_db()
    yield
    # Cleanup DB if created
    if os.path.exists(TEST_DB_PATH):
        try:
            os.remove(TEST_DB_PATH)
        except Exception:
            pass

def test_websocket_broadcast():
    # Use standard TestClient for WebSocket testing
    client = TestClient(app)
    
    # Connect to the store websocket
    with client.websocket_connect("/stores/ST1008/ws") as websocket:
        # Post a valid event to trigger ingestion and WS broadcast
        event = {
            "events": [{
                "event_id": "test-ws-id-12345",
                "store_id": "ST1008",
                "camera_id": "CAM_ENTRY_03",
                "visitor_id": "VIS_WS_0001",
                "event_type": "ENTRY",
                "timestamp": "2026-04-10T20:10:02+05:30",
                "zone_id": None,
                "dwell_ms": 0,
                "is_staff": False,
                "confidence": 0.95,
                "metadata": {
                    "queue_depth": None,
                    "sku_zone": None,
                    "session_seq": 1
                }
            }]
        }
        
        response = client.post("/events/ingest", json=event)
        assert response.status_code == 200
        
        # Verify the websocket received the event broadcast
        data = websocket.receive_json()
        assert data["type"] == "LIVE_EVENT"
        assert data["event"]["event_id"] == "test-ws-id-12345"
        assert data["event"]["store_id"] == "ST1008"
