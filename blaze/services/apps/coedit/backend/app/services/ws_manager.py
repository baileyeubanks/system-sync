import json
import asyncio
import logging
from typing import Dict, Set
from fastapi import WebSocket
import redis.asyncio as aioredis

from app.config import REDIS_URL

logger = logging.getLogger("coedit.ws")

# In-memory connection tracking per channel
_connections: Dict[str, Set[WebSocket]] = {}
_redis_sub_tasks: Dict[str, asyncio.Task] = {}
_redis_client = None


async def get_redis():
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client


async def publish(channel: str, event: dict):
    """Publish an event to a Redis channel."""
    r = await get_redis()
    await r.publish(channel, json.dumps(event))


async def _redis_listener(channel: str):
    """Subscribe to a Redis channel and broadcast to all local WebSocket connections."""
    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    pubsub = r.pubsub()
    await pubsub.subscribe(channel)
    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                data = message["data"]
                dead = set()
                for ws in _connections.get(channel, set()):
                    try:
                        await ws.send_text(data)
                    except Exception:
                        dead.add(ws)
                # Clean up dead connections
                if dead and channel in _connections:
                    _connections[channel] -= dead
    except asyncio.CancelledError:
        pass
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
        await r.close()


def _ensure_listener(channel: str):
    """Start a Redis listener task for a channel if not already running."""
    if channel not in _redis_sub_tasks or _redis_sub_tasks[channel].done():
        _redis_sub_tasks[channel] = asyncio.create_task(_redis_listener(channel))


async def connect(channel: str, ws: WebSocket):
    """Register a WebSocket connection for a channel."""
    await ws.accept()
    if channel not in _connections:
        _connections[channel] = set()
    _connections[channel].add(ws)
    _ensure_listener(channel)
    logger.info("WS connect: %s (total: %d)", channel, len(_connections[channel]))


async def disconnect(channel: str, ws: WebSocket):
    """Remove a WebSocket connection from a channel."""
    if channel in _connections:
        _connections[channel].discard(ws)
        if not _connections[channel]:
            del _connections[channel]
            # Cancel listener if no more connections
            if channel in _redis_sub_tasks:
                _redis_sub_tasks[channel].cancel()
                del _redis_sub_tasks[channel]
    logger.info("WS disconnect: %s", channel)


def review_channel(asset_id: str, version_id: str) -> str:
    return "review:{}:{}".format(asset_id, version_id)


def transcode_channel(job_id: str) -> str:
    return "transcode:{}".format(job_id)
