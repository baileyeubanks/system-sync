import json
import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.ws_manager import connect, disconnect, publish, review_channel, transcode_channel

router = APIRouter()


@router.websocket("/ws/review/{asset_id}/{version_id}")
async def ws_review(ws: WebSocket, asset_id: str, version_id: str):
    """WebSocket for live comment updates and typing indicators."""
    channel = review_channel(asset_id, version_id)
    await connect(channel, ws)
    try:
        while True:
            data = await ws.receive_text()
            try:
                msg = json.loads(data)
                event_type = msg.get("type")

                # Typing indicators — broadcast to all other viewers
                if event_type in ("typing_start", "typing_stop"):
                    await publish(channel, {
                        "type": event_type,
                        "user": msg.get("user", "Someone"),
                    })
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        await disconnect(channel, ws)


@router.websocket("/ws/transcode/{job_id}")
async def ws_transcode(ws: WebSocket, job_id: str):
    """WebSocket for transcode progress updates."""
    channel = transcode_channel(job_id)
    await connect(channel, ws)
    try:
        while True:
            # Client doesn't send anything, just receives
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await disconnect(channel, ws)
