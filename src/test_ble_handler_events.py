"""Tests for event-driven Meshtastic BLE receive handling."""

import asyncio

import pytest

from core.ble_handler import (
    BLEHandler,
    FROMNUM_UUID,
    FROMRADIO_UUID,
    TORADIO_UUID,
)


class FakeStats:
    def __init__(self):
        self.from_ble = []
        self.to_ble = []

    async def on_packet_from_ble(self, size):
        self.from_ble.append(size)

    async def on_packet_to_ble(self, size):
        self.to_ble.append(size)


class FakeClient:
    def __init__(self, reads=None):
        self.is_connected = True
        self.reads = list(reads or [])
        self.writes = []

    async def read_gatt_char(self, uuid):
        assert uuid == FROMRADIO_UUID

        if not self.reads:
            raise RuntimeError("done")

        value = self.reads.pop(0)

        if isinstance(value, Exception):
            raise value

        return value

    async def write_gatt_char(self, uuid, data, response=False):
        self.writes.append((uuid, data, response))


def make_handler(client=None):
    stats = FakeStats()
    handler = BLEHandler("AA:BB:CC:DD:EE:FF", stats)
    handler.client = client or FakeClient()
    handler.running = True
    handler.services_ready = True
    return handler, stats


def test_fromnum_notification_wakes_receiver():
    handler, _ = make_handler()

    assert not handler.read_event.is_set()

    handler._on_from_num(FROMNUM_UUID, (123).to_bytes(4, "little"))

    assert handler.last_from_num == 123
    assert handler.read_event.is_set()


def test_fromnum_empty_notification_still_wakes_receiver():
    handler, _ = make_handler()

    handler._on_from_num(FROMNUM_UUID, b"")

    assert handler.last_from_num is None
    assert handler.read_event.is_set()


@pytest.mark.asyncio
async def test_drain_delivers_packets():
    client = FakeClient([
        b"first",
        b"second",
        RuntimeError("done"),
    ])
    handler, stats = make_handler(client)

    received = []

    async def on_packet(data):
        received.append(data)

    handler.on_packet_received = on_packet

    await handler._drain_from_radio()

    assert received == [b"first", b"second"]
    assert stats.from_ble == [5, 6]


@pytest.mark.asyncio
async def test_drain_preserves_duplicate_protection():
    client = FakeClient([
        b"same",
        b"same",
        RuntimeError("done"),
    ])
    handler, stats = make_handler(client)

    received = []

    async def on_packet(data):
        received.append(data)

    handler.on_packet_received = on_packet

    await handler._drain_from_radio()

    assert received == [b"same"]
    assert stats.from_ble == [4]


@pytest.mark.asyncio
async def test_disconnection_during_read_sets_event():
    client = FakeClient([
        RuntimeError("Not connected"),
    ])
    handler, _ = make_handler(client)

    assert not handler.disconnection_event.is_set()

    await handler._drain_from_radio()

    assert handler.disconnection_event.is_set()


@pytest.mark.asyncio
async def test_send_uses_write_response_and_wakes_receiver():
    client = FakeClient()
    handler, stats = make_handler(client)

    assert not handler.read_event.is_set()

    await handler.send(b"hello")

    assert client.writes == [
        (TORADIO_UUID, b"hello", True)
    ]
    assert stats.to_ble == [5]
    assert handler.read_event.is_set()


@pytest.mark.asyncio
async def test_send_waits_for_gatt_lock():
    client = FakeClient()
    handler, _ = make_handler(client)

    await handler.gatt_lock.acquire()

    task = asyncio.create_task(handler.send(b"hello"))

    await asyncio.sleep(0)

    assert client.writes == []

    handler.gatt_lock.release()

    await task

    assert client.writes == [
        (TORADIO_UUID, b"hello", True)
    ]
