"""OPC encoding and client behaviour, exercised against the test-double sink."""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from fclights.opc import (
    OPC_BROADCAST_CHANNEL,
    OPC_HEADER_BYTES,
    OPC_SET_PIXELS,
    NullSink,
    OPCClient,
    encode_frame,
)
from opc_sink import RecordingOPCServer


class TestEncoding:
    def test_header_matches_the_protocol(self):
        message = encode_frame(1, np.zeros((512, 3), dtype=np.float32))
        assert message[0] == 1
        assert message[1] == OPC_SET_PIXELS
        assert (message[2] << 8 | message[3]) == 512 * 3
        assert len(message) == OPC_HEADER_BYTES + 512 * 3

    def test_full_scale_maps_to_255(self):
        message = encode_frame(0, np.ones((1, 3), dtype=np.float32))
        assert message[OPC_HEADER_BYTES:] == bytes([255, 255, 255])

    def test_zero_maps_to_zero(self):
        message = encode_frame(0, np.zeros((1, 3), dtype=np.float32))
        assert message[OPC_HEADER_BYTES:] == bytes([0, 0, 0])

    def test_midpoint_rounds_rather_than_truncating(self):
        # int() would turn 0.5 into 127; rounding gives the nearer value and
        # keeps a symmetric ramp symmetric.
        message = encode_frame(0, np.full((1, 3), 0.5, dtype=np.float32))
        assert message[OPC_HEADER_BYTES:] == bytes([128, 128, 128])

    def test_channel_order_is_r_g_b(self):
        pixel = np.array([[1.0, 0.0, 0.0]], dtype=np.float32)
        assert encode_frame(0, pixel)[OPC_HEADER_BYTES:] == bytes([255, 0, 0])

    def test_out_of_range_values_are_clipped_not_wrapped(self):
        # A wrap would turn an overbright pixel into a dark one, which on a real
        # strip looks like a dead LED rather than a bug.
        pixels = np.array([[-1.0, 2.0, 0.5]], dtype=np.float32)
        assert encode_frame(0, pixels)[OPC_HEADER_BYTES:] == bytes([0, 255, 128])

    def test_no_gamma_is_applied_here(self):
        # Gamma lives in fcserver. Applying it here too would double-correct.
        message = encode_frame(0, np.full((1, 3), 0.5, dtype=np.float32))
        assert message[OPC_HEADER_BYTES] == 128

    def test_empty_frame_encodes_to_a_bare_header(self):
        assert encode_frame(0, np.zeros((0, 3), dtype=np.float32)) == bytes([0, 0, 0, 0])

    @pytest.mark.parametrize("channel", [-1, 256])
    def test_channel_range_is_enforced(self, channel):
        with pytest.raises(ValueError, match=r"0\.\.255"):
            encode_frame(channel, np.zeros((1, 3), dtype=np.float32))

    def test_wrong_shape_is_rejected(self):
        with pytest.raises(ValueError, match=r"\(N, 3\)"):
            encode_frame(0, np.zeros((4, 4), dtype=np.float32))

    def test_oversized_frame_is_rejected(self):
        with pytest.raises(ValueError, match="OPC message limit"):
            encode_frame(0, np.zeros((30000, 3), dtype=np.float32))

    def test_broadcast_channel_constant_is_zero(self):
        assert OPC_BROADCAST_CHANNEL == 0


class TestClientAgainstTheSink:
    async def test_frames_arrive_intact(self):
        async with RecordingOPCServer() as sink:
            client = OPCClient("127.0.0.1", sink.port)
            pixels = np.linspace(0, 1, 64 * 3, dtype=np.float32).reshape(64, 3)
            await client.send([encode_frame(0, pixels)])
            received = await sink.wait_for_frames(1)
            await client.close()

        sink.assert_clean()
        assert received[0].channel == 0
        assert received[0].pixel_count == 64
        np.testing.assert_array_equal(
            received[0].pixels, np.rint(pixels * 255).astype(np.uint8)
        )

    async def test_several_channels_in_one_send_are_framed_correctly(self):
        # Multi-device rigs send one message per channel back to back; the
        # receiver has to split them on the length headers alone.
        async with RecordingOPCServer() as sink:
            client = OPCClient("127.0.0.1", sink.port)
            await client.send(
                [
                    encode_frame(1, np.ones((10, 3), dtype=np.float32)),
                    encode_frame(2, np.zeros((5, 3), dtype=np.float32)),
                ]
            )
            received = await sink.wait_for_frames(2)
            await client.close()

        sink.assert_clean()
        assert [(f.channel, f.pixel_count) for f in received] == [(1, 10), (2, 5)]

    async def test_a_dead_server_does_not_raise_and_does_not_block(self):
        # The caller is a real-time render loop; a refused connection must cost
        # it a dropped frame, not an exception or a stall.
        client = OPCClient("127.0.0.1", 1, connect_timeout=0.2)
        await asyncio.wait_for(client.send([encode_frame(0, np.zeros((4, 3), np.float32))]), 3.0)
        assert client.connected is False
        assert client.frames_dropped == 1
        await client.close()

    async def test_it_reconnects_after_the_server_comes_back(self):
        # fcserver restarts, or the Fadecandy is unplugged and replugged. The
        # rig has to recover without anyone restarting our service.
        sink = await RecordingOPCServer().start()
        port = sink.port
        client = OPCClient("127.0.0.1", port, min_retry=0.01, max_retry=0.05)

        await client.send([encode_frame(0, np.ones((4, 3), dtype=np.float32))])
        await sink.wait_for_frames(1)
        await sink.stop()

        # Writes into the closed socket fail; the client drops the connection.
        for _ in range(5):
            await client.send([encode_frame(0, np.ones((4, 3), dtype=np.float32))])
            await asyncio.sleep(0.02)

        revived = RecordingOPCServer(port=port)
        await revived.start()
        try:
            for _ in range(40):
                await client.send([encode_frame(0, np.ones((4, 3), dtype=np.float32))])
                if revived.frames:
                    break
                await asyncio.sleep(0.05)
            assert revived.frames, "client never reconnected"
        finally:
            await client.close()
            await revived.stop()

    async def test_it_notices_the_peer_closing_without_being_told(self):
        # The field failure: fcserver restarts while we are mid-scene. The
        # kernel buffers our writes, so nothing raises, and without watching
        # for EOF the lights would stay frozen until someone restarted us.
        sink = await RecordingOPCServer().start()
        client = OPCClient("127.0.0.1", sink.port, min_retry=0.01)
        await client.send([encode_frame(0, np.ones((4, 3), dtype=np.float32))])
        assert client.connected is True

        await sink.stop()
        for _ in range(50):
            if not client.connected:
                break
            await asyncio.sleep(0.02)

        assert client.connected is False, "client did not notice the server going away"
        await client.close()

    async def test_retry_backs_off_rather_than_spinning(self):
        client = OPCClient("127.0.0.1", 1, connect_timeout=0.05, min_retry=0.05, max_retry=1.0)
        message = [encode_frame(0, np.zeros((1, 3), dtype=np.float32))]
        for _ in range(4):
            await client.send(message)
        assert client._retry_delay > 0.05
        await client.close()


class TestNullSink:
    async def test_it_accepts_frames_and_reports_connected(self):
        sink = NullSink()
        assert sink.connected is True
        await sink.send([encode_frame(0, np.zeros((4, 3), dtype=np.float32))])
        assert sink.frames_sent == 1
        assert sink.last_messages
        await sink.close()
