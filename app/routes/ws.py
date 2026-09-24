from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.websocket_manager import connect, disconnect

router = APIRouter(tags=["websocket"])

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        disconnect(websocket)