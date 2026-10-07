"""
Regression tests for issue #15 on the core/ path (Docker image, cli.main):
the bridge must reconnect even when Bleak's disconnect callback never fires,
and must exit (serve_forever raises) instead of wedging when BLE is gone for good.
"""

import asyncio
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
import pytest_asyncio
from meshtastic import mesh_pb2

from core.ble_handler import MESHTASTIC_SERVICE_UUID
from core.bridge import MeshtasticBridge

ADDRESS = "AA:BB:CC:DD:EE:FF"


class FakeBLEDevice:
    """Simulated BLE peripheral shared by all FakeBleakClient instances."""

    def __init__(self):
        self.available = True
        self.supports_fromnum = False  # start_notify fails -> bridge falls back to polling
        self.clients = []
        self.to_radio = []  # payloads written by the bridge
        self.from_radio = []  # queued FromRadio payloads
        self.read_errors = []  # exceptions raised by the next reads, in order

    def drop(self):
        """Link goes down. Like an abnormal BlueZ disconnect, no callback fires."""
        self.available = False
        for client in self.clients:
            client._connected = False

    def make_zombie(self):
        """Link is dead but Bleak still reports is_connected=True."""
        for client in self.clients:
            client.zombie = True

    def make_client(self, *args, **kwargs):
        client = FakeBleakClient(self)
        self.clients.append(client)
        return client

    async def discover(self, *args, **kwargs):
        if not self.available:
            return {}
        device = SimpleNamespace(address=ADDRESS, name="Meshtastic_test")
        return {ADDRESS: (device, None)}


class FakeBleakClient:
    def __init__(self, device):
        self._device = device
        self._connected = False
        self.zombie = False
        self.services = [Mock(uuid=MESHTASTIC_SERVICE_UUID)]

    @property
    def is_connected(self):
        return self.zombie or (self._connected and self._device.available)

    async def connect(self):
        await asyncio.sleep(0)
        if not self._device.available:
            raise RuntimeError("Device not found")
        self._connected = True
        return True

    async def disconnect(self):
        self._connected = False
        self.zombie = False

    def set_disconnected_callback(self, callback):
        pass  # never called: the bridge must not depend on it

    async def read_gatt_char(self, uuid):
        await asyncio.sleep(0)
        if self.zombie or not self.is_connected:
            raise RuntimeError("Not connected")
        if self._device.read_errors:
            raise self._device.read_errors.pop(0)
        return self._device.from_radio.pop(0) if self._device.from_radio else b""

    async def write_gatt_char(self, uuid, data, response=False):
        if self.zombie or not self.is_connected:
            raise RuntimeError("Not connected")
        self._device.to_radio.append(bytes(data))

    async def start_notify(self, uuid, callback):
        if not self._device.supports_fromnum:
            raise RuntimeError("Characteristic not found")
        self.notify_callback = callback


async def wait_until(predicate, timeout=10.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met before timeout")
        await asyncio.sleep(0.01)


def want_config_requests(device):
    requests = []
    for payload in device.to_radio:
        to_radio = mesh_pb2.ToRadio()
        to_radio.ParseFromString(payload)
        if to_radio.HasField("want_config_id"):
            requests.append(to_radio.want_config_id)
    return requests


@pytest.fixture
def device():
    device = FakeBLEDevice()
    with patch("core.ble_handler.BleakClient", side_effect=device.make_client), \
         patch("core.ble_handler.BleakScanner.discover", side_effect=device.discover), \
         patch("core.ble_handler.asyncio.sleep", side_effect=_fast_sleep):
        yield device


_real_sleep = asyncio.sleep


async def _fast_sleep(delay, *args, **kwargs):
    # Shrink reconnect backoff and settle delays so the tests run in well under a second
    await _real_sleep(min(delay, 0.01), *args, **kwargs)


@pytest_asyncio.fixture
async def bridge(device, tmp_path):
    # Build inside the test's event loop, as cli.main and the GUI do: on Python 3.9
    # asyncio.Event/Lock bind to the loop current at construction time.
    bridge = MeshtasticBridge(ADDRESS, tcp_port=0, health_file=str(tmp_path / "health"))
    bridge.HEALTH_INTERVAL = 0.01
    bridge.ble.fallback_poll_interval = 0.01
    bridge.ble.KEEPALIVE_INTERVAL = 0.05
    return bridge


async def start(bridge):
    await bridge.start()
    return asyncio.create_task(bridge.serve_forever())


async def shutdown(bridge, serve_task):
    bridge.request_shutdown()
    await asyncio.wait_for(serve_task, timeout=5)
    await asyncio.wait_for(bridge.stop(), timeout=5)


class TestCoreReconnect:

    @pytest.mark.asyncio
    async def test_reconnects_without_disconnect_callback(self, bridge, device):
        serve_task = await start(bridge)
        assert not bridge.ble.fromnum_enabled  # fake rejects start_notify -> fallback polling
        first_client = bridge.ble.client

        device.drop()
        await wait_until(lambda: bridge.ble.reconnect_attempts > 0)
        device.available = True

        await wait_until(lambda: bridge.ble.client is not first_client and bridge.is_healthy())
        # Device is re-initialized after reconnecting
        await wait_until(lambda: len(want_config_requests(device)) == 1)
        assert not serve_task.done()

        await shutdown(bridge, serve_task)

    @pytest.mark.asyncio
    async def test_stale_is_connected_still_triggers_reconnect(self, bridge, device):
        serve_task = await start(bridge)
        first_client = bridge.ble.client

        # Reads fail with "Not connected" while is_connected keeps saying True
        device.make_zombie()

        await wait_until(lambda: bridge.ble.client is not first_client and bridge.is_healthy())
        assert first_client.zombie is False  # old client was torn down

        await shutdown(bridge, serve_task)

    @pytest.mark.asyncio
    async def test_hung_read_triggers_reconnect(self, bridge, device):
        bridge.ble.READ_TIMEOUT = 0.05
        serve_task = await start(bridge)
        first_client = bridge.ble.client

        async def hang(uuid):
            await _real_sleep(3600)
        first_client.read_gatt_char = hang

        await wait_until(lambda: bridge.ble.client is not first_client and bridge.is_healthy())
        assert not serve_task.done()

        await shutdown(bridge, serve_task)

    @pytest.mark.asyncio
    async def test_reconnect_failure_makes_serve_forever_raise(self, bridge, device):
        bridge.ble.MAX_RECONNECT_ATTEMPTS = 2
        failure_callback = Mock()
        bridge.register_failure_callback(failure_callback)
        serve_task = await start(bridge)

        device.drop()

        with pytest.raises(RuntimeError, match="reconnection failed"):
            await asyncio.wait_for(serve_task, timeout=10)

        failure_callback.assert_called_once()
        assert bridge.ble.poll_task.done()
        await asyncio.wait_for(bridge.stop(), timeout=5)

    @pytest.mark.asyncio
    async def test_each_outage_gets_full_set_of_attempts(self, bridge, device):
        serve_task = await start(bridge)
        # Left over from an earlier outage: used to make every later reconnect give up at once
        bridge.ble.reconnect_attempts = bridge.ble.MAX_RECONNECT_ATTEMPTS
        first_client = bridge.ble.client

        device.drop()
        await wait_until(lambda: bridge._reconnect_task is not None)
        device.available = True

        await wait_until(lambda: bridge.ble.client is not first_client and bridge.is_healthy())
        assert not serve_task.done()

        await shutdown(bridge, serve_task)

    @pytest.mark.asyncio
    async def test_concurrent_disconnect_handlers_share_one_cycle(self, bridge, device):
        bridge.running = True
        bridge.ble.client = None

        async def slow_reconnect():
            await _real_sleep(0.05)
            bridge.ble.client = device.make_client()
            bridge.ble.client._connected = True
            return True

        bridge.ble.attempt_reconnection = AsyncMock(side_effect=slow_reconnect)

        results = await asyncio.gather(*(bridge._handle_ble_disconnect() for _ in range(5)))

        assert results == [True] * 5
        assert bridge.ble.attempt_reconnection.await_count == 1
        await asyncio.wait_for(bridge._reinit_task, timeout=5)

    @pytest.mark.asyncio
    async def test_shutdown_with_connected_tcp_client(self, bridge, device):
        serve_task = await start(bridge)
        port = bridge.tcp.server.sockets[0].getsockname()[1]
        _, writer = await asyncio.open_connection("127.0.0.1", port)
        await wait_until(lambda: bridge.tcp.get_client_count() == 1)

        await shutdown(bridge, serve_task)
        writer.close()

    @pytest.mark.asyncio
    async def test_shutdown_during_reconnect(self, bridge, device):
        serve_task = await start(bridge)
        device.drop()
        await wait_until(lambda: bridge.ble.reconnect_attempts > 0)

        await shutdown(bridge, serve_task)

        assert bridge.ble.poll_task.done()
        assert bridge._reconnect_task.done()


class TestHealthFile:

    @pytest.mark.asyncio
    async def test_health_file_tracks_ble_state(self, bridge, device):
        stale = bridge.health_file
        with open(stale, "w") as f:
            f.write("left over from a previous run\n")
        os.utime(stale, (0, 0))

        serve_task = await start(bridge)
        await wait_until(lambda: os.path.exists(stale) and os.path.getmtime(stale) > time.time() - 5)

        # While BLE is down the file stops being refreshed
        bridge.ble.MAX_RECONNECT_ATTEMPTS = 1000
        device.drop()
        await wait_until(lambda: not bridge.is_healthy())
        os.utime(stale, (0, 0))
        await _real_sleep(0.1)
        assert os.path.getmtime(stale) == 0

        await shutdown(bridge, serve_task)
        assert not os.path.exists(stale)

    def test_is_healthy_requires_running_poll_task(self, device):
        handler_bridge = MeshtasticBridge(ADDRESS, tcp_port=0)
        assert handler_bridge.is_healthy() is False

        handler_bridge.ble.client = Mock(is_connected=True)
        handler_bridge.ble.poll_task = Mock(done=Mock(return_value=True))
        assert handler_bridge.is_healthy() is False

        handler_bridge.ble.poll_task = Mock(done=Mock(return_value=False))
        assert handler_bridge.is_healthy() is False  # no read completed yet

        handler_bridge.ble.last_poll_ok = time.monotonic()
        assert handler_bridge.is_healthy() is True

        # Poll loop stuck (no read completed recently)
        handler_bridge.ble.last_poll_ok = time.monotonic() - handler_bridge.POLL_STALE_AFTER - 1
        assert handler_bridge.is_healthy() is False
        handler_bridge.ble.last_poll_ok = time.monotonic()

        handler_bridge.ble.link_lost = True
        assert handler_bridge.is_healthy() is False


@pytest.mark.asyncio
async def test_disconnect_during_stop_does_not_report_failure(bridge, device):
    failure_callback = Mock()
    bridge.register_failure_callback(failure_callback)
    serve_task = await start(bridge)

    await shutdown(bridge, serve_task)
    # Bleak fires the disconnect callback for our own disconnect() during stop
    assert await bridge._handle_ble_disconnect() is False

    failure_callback.assert_not_called()


@pytest.mark.asyncio
async def test_new_outage_cancels_previous_reinit(bridge, device):
    bridge.running = True
    stale_reinit = asyncio.create_task(_real_sleep(60))
    bridge._reinit_task = stale_reinit
    bridge.ble.client = None
    bridge.ble.attempt_reconnection = AsyncMock(return_value=True)

    assert await bridge._handle_ble_disconnect() is True
    await asyncio.sleep(0)

    assert stale_reinit.cancelled()
    assert bridge._reinit_task is not stale_reinit
    bridge._reinit_task.cancel()


@pytest.mark.asyncio
async def test_fromnum_keepalive_detects_silent_link_loss(bridge, device):
    # With FROMNUM the loop sleeps until notified; the keepalive read must still
    # notice a link that died without a disconnect callback or notification.
    device.supports_fromnum = True
    serve_task = await start(bridge)
    assert bridge.ble.fromnum_enabled
    first_client = bridge.ble.client
    # Let the post-connect drain finish so the loop is idle, waiting on FROMNUM
    await wait_until(lambda: bridge.ble.last_poll_ok is not None)
    await _real_sleep(0.2)
    assert not bridge.ble.read_event.is_set()

    device.make_zombie()

    await wait_until(lambda: bridge.ble.client is not first_client and bridge.is_healthy())
    await shutdown(bridge, serve_task)


@pytest.mark.asyncio
async def test_fromnum_quiet_mesh_stays_healthy(bridge, device):
    device.supports_fromnum = True
    bridge.POLL_STALE_AFTER = 0.5
    serve_task = await start(bridge)

    # No traffic and no notifications for longer than POLL_STALE_AFTER
    await _real_sleep(1.0)
    assert bridge.is_healthy()

    await shutdown(bridge, serve_task)


@pytest.mark.asyncio
async def test_fromnum_retries_soon_after_transient_read_error(bridge, device):
    # FROMNUM only fires for new data: after a non-fatal read error, queued
    # packets must not wait for the (long) keepalive.
    device.supports_fromnum = True
    bridge.ble.KEEPALIVE_INTERVAL = 30.0
    bridge.ble.fallback_poll_interval = 0.05
    received = []
    original_handler = bridge.ble.on_packet_received

    async def record(data):
        received.append(data)
        await original_handler(data)
    bridge.ble.on_packet_received = record

    serve_task = await start(bridge)
    await wait_until(lambda: bridge.ble.last_poll_ok is not None)
    await _real_sleep(0.2)  # post-connect drain finished, loop idle on FROMNUM

    device.read_errors.append(RuntimeError("Operation already in progress"))
    device.from_radio.append(b"queued-packet")
    bridge.ble.client.notify_callback(None, (1).to_bytes(4, "little"))

    await wait_until(lambda: received == [b"queued-packet"], timeout=3.0)
    assert not bridge.ble.link_lost  # transient error, not a dead link

    await shutdown(bridge, serve_task)


@pytest.mark.asyncio
async def test_write_timeout_marks_link_lost(bridge, device):
    bridge.ble.WRITE_TIMEOUT = 0.05
    serve_task = await start(bridge)
    first_client = bridge.ble.client

    async def hang(uuid, data, response=False):
        await _real_sleep(3600)
    first_client.write_gatt_char = hang

    with pytest.raises(RuntimeError, match="write timed out"):
        await bridge.ble.send(b"\x01")
    assert not bridge.ble.gatt_lock.locked()

    # The poll loop treats it as link loss and reconnects
    await wait_until(lambda: bridge.ble.client is not first_client and bridge.is_healthy())
    await shutdown(bridge, serve_task)


@pytest.mark.asyncio
async def test_persistent_read_errors_trigger_reconnect(bridge, device):
    serve_task = await start(bridge)
    first_client = bridge.ble.client

    device.read_errors.extend(
        RuntimeError("Operation failed") for _ in range(bridge.ble.MAX_DRAIN_FAILURES)
    )

    await wait_until(lambda: bridge.ble.client is not first_client and bridge.is_healthy())
    assert bridge.ble.drain_failures == 0
    await shutdown(bridge, serve_task)


@pytest.mark.asyncio
async def test_packet_handler_error_is_not_a_read_failure(bridge, device):
    serve_task = await start(bridge)
    first_client = bridge.ble.client

    async def broken_handler(data):
        raise RuntimeError("TCP client disconnected")
    bridge.ble.on_packet_received = broken_handler

    device.from_radio.extend(b"pkt%d" % i for i in range(bridge.ble.MAX_DRAIN_FAILURES + 2))
    await wait_until(lambda: not device.from_radio)
    await _real_sleep(0.1)

    assert bridge.ble.client is first_client
    assert not bridge.ble.link_lost
    assert bridge.ble.drain_failures == 0
    await shutdown(bridge, serve_task)
