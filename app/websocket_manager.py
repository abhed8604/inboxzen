from __future__ import annotations
import logging
from fastapi import WebSocket

logger = logging.getLogger(__name__)

_active_connections: list[WebSocket] = []


async def connect(websocket: WebSocket) -> None:
    await websocket.accept()
    _active_connections.append(websocket)


def disconnect(websocket: WebSocket) -> None:
    if websocket in _active_connections:
        _active_connections.remove(websocket)


async def broadcast(data: dict) -> None:
    for conn in list(_active_connections):
        try:
            await conn.send_json(data)
        except Exception as e:
            logger.error("Failed to send WebSocket message: %s", e)
            disconnect(conn)