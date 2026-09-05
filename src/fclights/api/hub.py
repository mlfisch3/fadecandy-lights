"""WebSocket broadcast hub.

Several phones will be pointed at this rig at once.  Without a push channel they
would each poll, show each other's changes late, and fight over the last write.
So every state change is broadcast to every connected client immediately, and
the phones stay in agreement.

Broadcasts are best-effort: a client that has wedged or gone out of range must
not stall the render loop or the API, so a send that fails or blocks drops that
client rather than the message.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

log = logging.getLogger(__name__)

SEND_TIMEOUT = 2.0


class Broadcaster:
    """Fan-out of JSON messages to connected WebSocket clients."""

    def __init__(self) -> None:
        self._clients: set[Any] = set()
        self._lock = asyncio.Lock()

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def add(self, websocket: Any) -> None:
        async with self._lock:
            self._clients.add(websocket)
        log.debug("websocket client attached (%d total)", len(self._clients))

    async def remove(self, websocket: Any) -> None:
        async with self._lock:
            self._clients.discard(websocket)
        log.debug("websocket client detached (%d total)", len(self._clients))

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Send ``message`` to every client, dropping any that fail."""
        async with self._lock:
            targets = list(self._clients)
        if not targets:
            return

        payload = json.dumps(message)
        results = await asyncio.gather(
            *(self._send(client, payload) for client in targets), return_exceptions=True
        )
        dead = [
            client for client, ok in zip(targets, results, strict=True) if ok is not True
        ]
        if dead:
            async with self._lock:
                self._clients.difference_update(dead)
            log.debug("dropped %d unresponsive websocket client(s)", len(dead))

    @staticmethod
    async def _send(client: Any, payload: str) -> bool:
        try:
            await asyncio.wait_for(client.send_text(payload), timeout=SEND_TIMEOUT)
        except Exception:
            # Any failure at all means this client is gone; the caller drops it.
            return False
        return True

    async def close(self) -> None:
        async with self._lock:
            targets, self._clients = list(self._clients), set()
        for client in targets:
            with contextlib.suppress(Exception):
                await client.close()
