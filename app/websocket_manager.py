from __future__ import annotations
import logging
from fastapi import WebSocket

logger = logging.getLogger(__name__)

class WebSocketManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
    
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
    
    async def broadcast(self, data: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(data)
            except Exception as e:
                logger.error("Failed to send WebSocket message: %s", e)
                # Remove broken connection
                self.disconnect(connection)

# Global instance
manager = WebSocketManager()

# Convenience function for broadcasting
async def broadcast(data: dict):
    await manager.broadcast(data)