"""WebSocket broadcast hub lifecycle.

These pin the behaviour ``docs/api.md`` promises a client: a phone that falls
behind is closed rather than silently unsubscribed, and a phone that is merely
slow is not dropped at all.
"""

from __future__ import annotations

import asyncio

import pytest

from fclights.api.hub import CLOSE_CODE_TOO_SLOW, QUEUE_DEPTH, Broadcaster


class FakeSocket:
    """A websocket whose send speed and failure mode the test controls."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed = False
        self.close_code: int | None = None
        self.gate: asyncio.Event | None = None
        self.fail = False

    async def send_text(self, payload: str) -> None:
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise ConnectionResetError("peer went away")
        self.sent.append(payload)

    async def close(self, code: int = 1000) -> None:
        self.closed = True
        self.close_code = code

    def stall(self) -> None:
        self.gate = asyncio.Event()

    def resume(self) -> None:
        assert self.gate is not None
        self.gate.set()
        self.gate = None


async def settle() -> None:
    """Let the writer tasks and any in-flight closes run to quiescence."""
    for _ in range(10):
        await asyncio.sleep(0)


async def flood(hub: Broadcaster, count: int) -> None:
    """Broadcast ``count`` messages, letting the writers run between each.

    The yield matters: without it nothing drains, so even a healthy client would
    back up and the test would not distinguish the two.
    """
    for i in range(count):
        await hub.broadcast({"type": "state", "n": i})
        await settle()


@pytest.fixture
async def hub():
    broadcaster = Broadcaster()
    yield broadcaster
    await broadcaster.close()


class TestDelivery:
    async def test_every_client_receives_the_message(self, hub):
        first, second = FakeSocket(), FakeSocket()
        await hub.add(first)
        await hub.add(second)

        await hub.broadcast({"type": "state", "brightness": 0.5})
        await settle()

        assert first.sent == second.sent
        assert '"brightness": 0.5' in first.sent[0]

    async def test_a_stalled_client_does_not_delay_the_broadcast(self, hub):
        # Controller.commit awaits broadcast(), so this is the REST latency a
        # second phone's bad WiFi would otherwise add to every command.
        stalled, healthy = FakeSocket(), FakeSocket()
        stalled.stall()
        await hub.add(stalled)
        await hub.add(healthy)

        loop = asyncio.get_running_loop()
        started = loop.time()
        await hub.broadcast({"type": "state"})
        assert loop.time() - started < 0.5

        await settle()
        assert healthy.sent, "the healthy client is served while the other stalls"
        stalled.resume()


class TestSlowClients:
    async def test_a_client_that_recovers_within_tolerance_is_not_dropped(self, hub):
        socket = FakeSocket()
        socket.stall()
        await hub.add(socket)

        # Stall for most of the backlog it is allowed, then come back.
        await flood(hub, QUEUE_DEPTH - 1)
        await settle()
        assert hub.client_count == 1
        assert not socket.closed

        socket.resume()
        await settle()

        assert hub.client_count == 1
        assert len(socket.sent) == QUEUE_DEPTH - 1

        # Still subscribed: the next change reaches it.
        await hub.broadcast({"type": "state", "n": "after"})
        await settle()
        assert '"after"' in socket.sent[-1]

    async def test_a_client_that_falls_too_far_behind_gets_a_close_frame(self, hub):
        # The defect this pins: it used to be dropped from the set with no close
        # frame, so it could not tell being unsubscribed from nothing happening,
        # and the half-open socket was never closed by anyone.
        socket = FakeSocket()
        socket.stall()
        await hub.add(socket)

        await flood(hub, QUEUE_DEPTH + 5)
        await settle()

        assert hub.client_count == 0
        assert socket.closed, "a dropped client must be told, not left in silence"
        assert socket.close_code == CLOSE_CODE_TOO_SLOW
        socket.resume()

    async def test_a_dropped_client_does_not_hold_up_the_others(self, hub):
        stalled, healthy = FakeSocket(), FakeSocket()
        stalled.stall()
        await hub.add(stalled)
        await hub.add(healthy)

        await flood(hub, QUEUE_DEPTH + 5)
        await settle()

        assert hub.client_count == 1
        assert len(healthy.sent) == QUEUE_DEPTH + 5
        stalled.resume()


class TestBrokenClients:
    async def test_a_failing_send_unsubscribes_and_closes(self, hub):
        socket = FakeSocket()
        socket.fail = True
        await hub.add(socket)

        await hub.broadcast({"type": "state"})
        await settle()

        assert hub.client_count == 0
        assert socket.closed

    async def test_a_broken_client_does_not_break_later_broadcasts(self, hub):
        broken, healthy = FakeSocket(), FakeSocket()
        broken.fail = True
        await hub.add(broken)
        await hub.add(healthy)

        await hub.broadcast({"type": "state", "n": 1})
        await settle()
        await hub.broadcast({"type": "state", "n": 2})
        await settle()

        assert hub.client_count == 1
        assert len(healthy.sent) == 2


class TestShutdown:
    async def test_close_closes_every_client_and_stops_its_writer(self, hub):
        first, second = FakeSocket(), FakeSocket()
        await hub.add(first)
        await hub.add(second)

        await hub.close()

        assert hub.client_count == 0
        assert first.closed and second.closed
        assert not [t for t in asyncio.all_tasks() if "_drain" in repr(t)]

    async def test_remove_stops_the_writer_task(self, hub):
        socket = FakeSocket()
        await hub.add(socket)

        await hub.remove(socket)
        await hub.broadcast({"type": "state"})
        await settle()

        assert hub.client_count == 0
        assert socket.sent == []


class UnclosableSocket(FakeSocket):
    """A peer that has stopped reading: not even the close frame can go out.

    This is what a frozen phone looks like from the server. Nothing can be
    written to it, so no polite goodbye is possible - the connection has to be
    forced down instead, or it stays half-open forever.
    """

    async def close(self, code: int = 1000) -> None:
        self.close_code = code
        await asyncio.Event().wait()  # never completes


class TestUndeliverableClose:
    async def test_the_serving_task_is_cancelled_so_the_connection_is_released(
        self, hub, monkeypatch
    ):
        monkeypatch.setattr("fclights.api.hub.CLOSE_TIMEOUT", 0.05)
        socket = UnclosableSocket()
        socket.stall()

        parked = asyncio.Event()

        async def endpoint():
            # Stands in for the websocket endpoint sitting in receive_text().
            await hub.add(socket, owner=asyncio.current_task())
            parked.set()
            await asyncio.Event().wait()

        owner = asyncio.create_task(endpoint())
        await parked.wait()

        await flood(hub, QUEUE_DEPTH + 5)
        await hub.wait_for_closures()

        assert hub.client_count == 0
        assert owner.cancelled() or owner.done()
        socket.resume()

    async def test_it_gives_up_gracefully_when_there_is_no_one_to_cancel(self, hub, monkeypatch):
        # No owner and no reachable transport: we cannot force anything, and
        # must not blow up trying.
        monkeypatch.setattr("fclights.api.hub.CLOSE_TIMEOUT", 0.05)
        socket = UnclosableSocket()
        socket.stall()
        await hub.add(socket)

        await flood(hub, QUEUE_DEPTH + 5)
        await hub.wait_for_closures()

        assert hub.client_count == 0
        socket.resume()
