"""In-process WebSocket fan-out to dashboard clients.

For multi-instance deployments replace this with a Redis pub/sub backed hub (see
docs/ARCHITECTURE.md); the interface (``broadcast``) stays the same.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

from app.timeutil import utcnow

log = logging.getLogger(__name__)


class EventHub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def broadcast(self, event: str, data: Any) -> None:
        if not self._clients:
            return
        message = json.dumps({"event": event, "ts": utcnow().isoformat(), "data": data}, default=str)
        async with self._lock:
            clients = list(self._clients)
        dead: list[WebSocket] = []
        for ws in clients:
            try:
                await asyncio.wait_for(ws.send_text(message), timeout=5)
            except Exception:  # noqa: BLE001 - any send failure means the client is gone
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)


hub = EventHub()
