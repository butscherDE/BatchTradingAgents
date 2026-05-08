"""WebSocket endpoint for real-time push to frontend."""

import asyncio
import json
import logging
from typing import Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()
logger = logging.getLogger(__name__)

_connections: Set[WebSocket] = set()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    _connections.add(websocket)
    logger.info(f"WebSocket connected ({len(_connections)} total)")

    try:
        while True:
            # Send ping every 30s to keep connection alive (Windows ProactorEventLoop drops idle sockets)
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
            except asyncio.TimeoutError:
                await websocket.send_text('{"type":"ping"}')
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _connections.discard(websocket)
        logger.info(f"WebSocket disconnected ({len(_connections)} total)")


async def broadcast(event_type: str, data: dict):
    """Broadcast an event to all connected WebSocket clients."""
    if not _connections:
        return

    message = json.dumps({"type": event_type, "data": data})
    disconnected = set()

    for ws in _connections:
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.add(ws)

    _connections.difference_update(disconnected)
