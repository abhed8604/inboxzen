from __future__ import annotations
from fastapi import WebSocket

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
                print(f"Failed to send WebSocket message: {e}")
                # Remove broken connection
                self.disconnect(connection)

# Global instance
manager = WebSocketManager()

# Convenience function for broadcasting
async def broadcast(data: dict):
    await manager.broadcast(data)