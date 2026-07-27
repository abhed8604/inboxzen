from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.websocket_manager import manager

router = APIRouter(tags=["websocket"])

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive and handle incoming messages if needed
            data = await websocket.receive_text()
            # For MVP, we don't expect client messages, but we could handle pings
    except WebSocketDisconnect:
        manager.disconnect(websocket)