from fastapi import WebSocket
from typing import Dict, Set
import structlog

logger = structlog.get_logger()

class ConnectionManager:
    def __init__(self):
        # Maps store_id -> Set of active WebSockets
        self.active_connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, store_id: str):
        await websocket.accept()
        if store_id not in self.active_connections:
            self.active_connections[store_id] = set()
        self.active_connections[store_id].add(websocket)
        logger.info("WebSocket connected", store_id=store_id, active_count=len(self.active_connections[store_id]))

    def disconnect(self, websocket: WebSocket, store_id: str):
        if store_id in self.active_connections:
            self.active_connections[store_id].discard(websocket)
            if not self.active_connections[store_id]:
                del self.active_connections[store_id]
            logger.info("WebSocket disconnected", store_id=store_id)

    async def broadcast_to_store(self, store_id: str, message: dict):
        if store_id in self.active_connections:
            disconnected = set()
            for connection in self.active_connections[store_id]:
                try:
                    await connection.send_json(message)
                except Exception as e:
                    logger.error("Failed to send WebSocket message", error=str(e))
                    disconnected.add(connection)
            
            for conn in disconnected:
                self.disconnect(conn, store_id)

manager = ConnectionManager()
