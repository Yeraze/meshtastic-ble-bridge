"""Tests for Meshtastic TCP stream framing."""

import asyncio

import pytest

from core.protocol import MAX_PACKET_SIZE, ProtocolHandler


def make_reader(data: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader


@pytest.mark.asyncio
async def test_reads_normal_frame():
    payload = b"hello"
    frame = ProtocolHandler.create_tcp_frame(payload)

    result = await ProtocolHandler.read_tcp_frame(make_reader(frame))

    assert result == payload


@pytest.mark.asyncio
async def test_resyncs_after_wake_byte():
    payload = b"hello"
    frame = ProtocolHandler.create_tcp_frame(payload)

    result = await ProtocolHandler.read_tcp_frame(
        make_reader(b"\xc3" + frame)
    )

    assert result == payload


@pytest.mark.asyncio
async def test_resyncs_after_arbitrary_bytes():
    payload = b"hello"
    frame = ProtocolHandler.create_tcp_frame(payload)

    result = await ProtocolHandler.read_tcp_frame(
        make_reader(b"\x00\x01\x02\xff\xc3\x42" + frame)
    )

    assert result == payload


@pytest.mark.asyncio
async def test_preserves_repeated_start_byte():
    payload = b"hello"
    frame = ProtocolHandler.create_tcp_frame(payload)

    result = await ProtocolHandler.read_tcp_frame(
        make_reader(b"\x94" + frame)
    )

    assert result == payload


@pytest.mark.asyncio
async def test_resyncs_after_oversized_frame_candidate():
    payload = b"valid"
    valid_frame = ProtocolHandler.create_tcp_frame(payload)

    invalid_header = (
        b"\x94\xc3"
        + (MAX_PACKET_SIZE + 1).to_bytes(2, "big")
    )

    result = await ProtocolHandler.read_tcp_frame(
        make_reader(invalid_header + valid_frame)
    )

    assert result == payload


@pytest.mark.asyncio
async def test_eof_while_waiting_for_frame():
    reader = make_reader(b"\x00\xc3\x94")

    with pytest.raises(asyncio.IncompleteReadError):
        await ProtocolHandler.read_tcp_frame(reader)
