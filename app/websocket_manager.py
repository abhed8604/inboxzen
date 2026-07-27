from fastapi import WebSocket
from typing import List, Dict, Any

class WebSocketManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
    
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
    
    async def broadcast(self, data: Dict[str, Any]):
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
async def broadcast(data: Dict[str, Any]):
    await manager.broadcast(data)