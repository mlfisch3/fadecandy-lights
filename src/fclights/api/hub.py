"""WebSocket broadcast hub.

Several phones will be pointed at this rig at once.  Without a push channel they
would each poll, show each other's changes late, and fight over the last write.
So every state change is broadcast to every connected client immediately, and
the phones stay in agreement.

Each client gets its own bounded outbound queue drained by its own writer task,
rather than being written to inline by the broadcaster.  Two reasons, both of
which we hit for real on the rig:

*Broadcasting must not block the caller.*  ``Controller.commit`` awaits the
broadcast, so writing inline puts every connected phone's network on the path of
every REST request.  One stalled phone made a brightness change take seconds.
Enqueuing is synchronous and cannot block, so a slow client now costs the API
nothing.

*A slow client is not a dead client.*  The tolerance for falling behind has to be
measured in how far behind the client is, not in how long one individual send
took.  A phone on domestic WiFi, or one the OS has just thawed, routinely takes
seconds to acknowledge a frame and then catches up fine; dropping it for that
defeats the point of having a socket.  A client is dropped only when its queue
overflows, which means it has stopped draining altogether.

When a client *is* dropped, the connection is ended - with a close frame if one
can still be delivered, and by forcing the socket down if it cannot.  Never by
quietly forgetting it: a client that is silently unsubscribed cannot tell "the
server dropped me" from "nothing has changed", so it sits there showing stale
state forever.  ``docs/api.md`` promises clients exactly this.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

log = logging.getLogger(__name__)

QUEUE_DEPTH = 64
"""Messages a client may fall behind by before it is dropped.

Everything sent here is a full snapshot - ``state`` and ``telemetry`` both carry
the whole picture - so a backlog is pure lag, never lost information, and the
`hello` on reconnect resyncs anyway.  Telemetry alone ticks every 2 seconds, so
an idle client may stall for roughly two minutes and still catch up.  A burst of
state changes (a slider being dragged) uses the budget faster, but a phone being
dragged is by definition awake.
"""

CLOSE_TIMEOUT = 10.0
"""Cap on waiting for the close handshake of a client that is already gone."""

CLOSE_CODE_TOO_SLOW = 1013
"""RFC 6455 "Try Again Later": the server is fine, this client fell behind."""


class _Client:
    """One connected socket, its backlog, and the task draining it."""

    __slots__ = ("owner", "queue", "task", "websocket")

    def __init__(self, websocket: Any, owner: asyncio.Task[Any] | None) -> None:
        self.websocket = websocket
        self.owner = owner
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=QUEUE_DEPTH)
        self.task: asyncio.Task[None] | None = None


class Broadcaster:
    """Fan-out of JSON messages to connected WebSocket clients."""

    def __init__(self) -> None:
        self._clients: dict[Any, _Client] = {}
        self._lock = asyncio.Lock()
        self._closing: set[asyncio.Task[None]] = set()

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def add(self, websocket: Any, *, owner: asyncio.Task[Any] | None = None) -> None:
        """Register ``websocket``; ``owner`` is the endpoint task serving it.

        The owner matters only when a client has to be dropped and the close
        handshake cannot be delivered.  See :meth:`_eject`.
        """
        client = _Client(websocket, owner)
        client.task = asyncio.create_task(self._drain(client))
        async with self._lock:
            self._clients[websocket] = client
        log.debug("websocket client attached (%d total)", len(self._clients))

    async def remove(self, websocket: Any) -> None:
        async with self._lock:
            client = self._clients.pop(websocket, None)
        if client is not None:
            await self._stop(client)
        log.debug("websocket client detached (%d total)", len(self._clients))

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Queue ``message`` for every client, dropping any that has stalled.

        Never blocks on the network: the slowest client on the rig cannot delay
        the request that triggered the broadcast.
        """
        async with self._lock:
            targets = list(self._clients.values())
        if not targets:
            return

        payload = json.dumps(message)
        for client in targets:
            try:
                client.queue.put_nowait(payload)
            except asyncio.QueueFull:
                # It has not drained a single message in QUEUE_DEPTH broadcasts;
                # it is not slow, it is gone.  Close it so it knows to reconnect.
                log.info("dropping websocket client that fell %d messages behind", QUEUE_DEPTH)
                self._close_later(client, CLOSE_CODE_TOO_SLOW)

    async def close(self) -> None:
        async with self._lock:
            targets, self._clients = list(self._clients.values()), {}
        for client in targets:
            await self._stop(client)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(client.websocket.close(), CLOSE_TIMEOUT)
        await self.wait_for_closures()

    async def wait_for_closures(self) -> None:
        """Await the closes that :meth:`broadcast` started in the background."""
        while self._closing:
            await asyncio.gather(*list(self._closing), return_exceptions=True)

    # -- internals -----------------------------------------------------

    async def _drain(self, client: _Client) -> None:
        """Write this client's backlog to its socket, one message at a time."""
        while True:
            payload = await client.queue.get()
            try:
                await client.websocket.send_text(payload)
            except asyncio.CancelledError:
                raise
            except Exception:
                # The socket is broken rather than merely slow.  Nothing to tell
                # it, but still unsubscribe so we stop queueing for a dead peer.
                log.debug("websocket send failed; dropping client", exc_info=True)
                self._close_later(client, None)
                return

    def _close_later(self, client: _Client, code: int | None) -> None:
        """Unsubscribe ``client`` and close its socket, off the caller's path."""
        task = asyncio.create_task(self._eject(client, code))
        self._closing.add(task)
        task.add_done_callback(self._closing.discard)

    async def _eject(self, client: _Client, code: int | None) -> None:
        async with self._lock:
            if self._clients.get(client.websocket) is not client:
                return  # Already gone by another route.
            del self._clients[client.websocket]
        await self._stop(client)

        # Try to say goodbye properly.  For a client that is merely slow this
        # succeeds and it gets a real close frame with a reason.
        closed_cleanly = False
        with contextlib.suppress(Exception):
            closing = (
                client.websocket.close() if code is None else client.websocket.close(code=code)
            )
            await asyncio.wait_for(closing, CLOSE_TIMEOUT)
            closed_cleanly = True

        if closed_cleanly:
            return

        # Nothing can be written to this peer at all, a close frame included, and
        # the endpoint is parked in receive_text() waiting for a socket that will
        # never speak again.  Something has to force the connection down, or it
        # stays half-open - which is the whole defect.
        log.info("close handshake undeliverable; forcing the connection down")
        if _abort_transport(client.websocket):
            # The endpoint sees a disconnect and unwinds on its own.
            return
        if client.owner is not None and not client.owner.done():
            client.owner.cancel()

    @staticmethod
    async def _stop(client: _Client) -> None:
        task, client.task = client.task, None
        if task is None or task is asyncio.current_task():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

def _abort_transport(websocket: Any) -> bool:
    """Force the TCP connection under ``websocket`` down.  True if it worked.

    Every polite way of ending a WebSocket needs to *write*, which is exactly
    what a peer that has stopped reading will not allow: even asyncio's
    ``transport.close()`` waits for the send buffer to drain before it sends a
    FIN, so a socket with a backlog behind a shut window stays up until TCP's
    own retransmission timeout gives up, a quarter of an hour later.  Only
    ``abort()`` ends it now, and ASGI has no way to ask for that.

    So this walks from the ASGI send callable to the server's transport, which
    is server-specific by nature.  It is deliberately best-effort: if a server
    hides its transport we return False and the caller falls back to cancelling
    the endpoint task.
    """
    seen: set[int] = set()
    queue: list[Any] = [getattr(websocket, "_send", None)]
    while queue:
        candidate = queue.pop(0)
        if candidate is None or id(candidate) in seen:
            continue
        seen.add(id(candidate))
        transport = getattr(getattr(candidate, "__self__", None), "transport", None)
        abort = getattr(transport, "abort", None)
        if callable(abort):
            with contextlib.suppress(Exception):
                abort()
                return True
        # Starlette wraps the raw send in a chain of middleware closures.
        queue.extend(
            cell.cell_contents
            for cell in getattr(candidate, "__closure__", None) or ()
            if callable(cell.cell_contents)
        )
    return False
