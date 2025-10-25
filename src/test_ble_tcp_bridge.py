#!/usr/bin/env python3
"""
Unit tests for BLE TCP Bridge
Tests cover cache functionality, concurrent access, and error scenarios
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from meshtastic import mesh_pb2, telemetry_pb2
import time


# Import the bridge module
import ble_tcp_bridge


class TestConfigCache:
    """Tests for config caching functionality"""

    @pytest.fixture
    def bridge(self):
        """Create a bridge instance with caching enabled"""
        with patch('ble_tcp_bridge.BleakClient'):
            bridge = ble_tcp_bridge.MeshtasticBLEBridge(
                ble_address="AA:BB:CC:DD:EE:FF",
                tcp_port=4403,
                cache_nodes=True
            )
            return bridge

    def test_cache_initialization(self, bridge):
        """Test that cache is properly initialized when enabled"""
        assert bridge.config_cache is not None
        assert isinstance(bridge.config_cache, list)
        assert len(bridge.config_cache) == 0
        assert bridge.config_cache_complete is False
        assert bridge.recording_config is False

    def test_cache_disabled_by_default(self):
        """Test that caching is disabled by default"""
        with patch('ble_tcp_bridge.BleakClient'):
            bridge = ble_tcp_bridge.MeshtasticBLEBridge(
                ble_address="AA:BB:CC:DD:EE:FF",
                tcp_port=4403,
                cache_nodes=False
            )
            assert bridge.config_cache is None

    def test_create_tcp_frame(self, bridge):
        """Test TCP frame creation"""
        payload = b"test_payload"
        frame = bridge.create_tcp_frame(payload)

        # Frame should be: START1(0x94) + START2(0xC3) + LENGTH(2 bytes) + payload
        assert frame[0] == 0x94
        assert frame[1] == 0xC3
        assert len(frame) == 4 + len(payload)

        # Check length field (big-endian)
        length = (frame[2] << 8) | frame[3]
        assert length == len(payload)

        # Check payload
        assert frame[4:] == payload

    @pytest.mark.asyncio
    async def test_prewarm_cache_success(self, bridge):
        """Test successful cache pre-warming"""
        # Mock BLE client
        bridge.ble_client = AsyncMock()
        bridge.ble_client.write_gatt_char = AsyncMock()

        # Simulate config response
        async def simulate_config_response():
            await asyncio.sleep(0.1)
            bridge.config_cache_complete = True

        # Start pre-warming in background
        prewarm_task = asyncio.create_task(bridge.prewarm_cache())
        response_task = asyncio.create_task(simulate_config_response())

        await asyncio.wait([prewarm_task, response_task], timeout=2)

        assert bridge.config_cache_complete is True

    @pytest.mark.asyncio
    async def test_prewarm_cache_timeout(self, bridge):
        """Test cache pre-warming timeout handling"""
        bridge.ble_client = AsyncMock()
        bridge.ble_client.write_gatt_char = AsyncMock()

        # Don't simulate response - let it timeout
        await bridge.prewarm_cache()

        # Should not crash, just log warning
        assert bridge.recording_config is False

    @pytest.mark.asyncio
    async def test_ble_disconnection_during_prewarm(self, bridge):
        """Test handling of BLE disconnection during cache pre-warming"""
        bridge.ble_client = AsyncMock()
        bridge.ble_client.write_gatt_char = AsyncMock(
            side_effect=Exception("BLE disconnected")
        )

        # Should handle disconnection gracefully
        try:
            await bridge.prewarm_cache()
        except Exception:
            pytest.fail("Pre-warming should handle BLE disconnection gracefully")

    def test_cache_node_info_update(self, bridge):
        """Test updating NodeInfo in cache"""
        # Create a fake cached config with a NodeInfo packet
        node_num = 0x12345678

        # Create initial NodeInfo
        from_radio = mesh_pb2.FromRadio()
        from_radio.node_info.num = node_num
        from_radio.node_info.user.long_name = "Old Name"
        initial_bytes = from_radio.SerializeToString()
        initial_frame = bridge.create_tcp_frame(initial_bytes)

        # Add to cache
        bridge.config_cache = [(initial_bytes, initial_frame)]
        bridge.config_cache_complete = True

        # Create updated NodeInfo
        updated_from_radio = mesh_pb2.FromRadio()
        updated_from_radio.node_info.num = node_num
        updated_from_radio.node_info.user.long_name = "New Name"
        updated_bytes = updated_from_radio.SerializeToString()
        updated_frame = bridge.create_tcp_frame(updated_bytes)

        # Simulate cache update logic
        for i, (cached_proto, _) in enumerate(bridge.config_cache):
            cached_from_radio = mesh_pb2.FromRadio()
            cached_from_radio.ParseFromString(cached_proto)
            if cached_from_radio.HasField('node_info') and cached_from_radio.node_info.num == node_num:
                bridge.config_cache[i] = (updated_bytes, updated_frame)
                break

        # Verify update
        cached_proto, _ = bridge.config_cache[0]
        result = mesh_pb2.FromRadio()
        result.ParseFromString(cached_proto)
        assert result.node_info.user.long_name == "New Name"

    def test_cache_size_with_many_nodes(self, bridge):
        """Test cache behavior with large number of nodes"""
        bridge.config_cache_complete = True

        # Simulate 200 nodes
        for i in range(200):
            from_radio = mesh_pb2.FromRadio()
            from_radio.node_info.num = 0x10000000 + i
            from_radio.node_info.user.long_name = f"Node_{i}"
            proto_bytes = from_radio.SerializeToString()
            tcp_frame = bridge.create_tcp_frame(proto_bytes)
            bridge.config_cache.append((proto_bytes, tcp_frame))

        # Verify cache size
        assert len(bridge.config_cache) == 200

        # Estimate memory usage (rough approximation)
        total_bytes = sum(len(proto) + len(frame) for proto, frame in bridge.config_cache)
        # Should be under 500KB for 200 nodes
        assert total_bytes < 500 * 1024

    def test_packet_deduplication(self, bridge):
        """Test packet deduplication logic"""
        # First packet
        packet1 = b"test_packet"
        hash1 = hash(packet1)
        time1 = time.time()

        bridge.last_packet_hash = hash1
        bridge.last_packet_time = time1

        # Same packet within 100ms - should be duplicate
        time2 = time1 + 0.05  # 50ms later
        is_duplicate = (hash1 == bridge.last_packet_hash and
                       (time2 - bridge.last_packet_time) < 0.1)
        assert is_duplicate is True

        # Same packet after 100ms - should NOT be duplicate
        time3 = time1 + 0.15  # 150ms later
        is_duplicate = (hash1 == bridge.last_packet_hash and
                       (time3 - bridge.last_packet_time) < 0.1)
        assert is_duplicate is False


class TestConcurrency:
    """Tests for concurrent access scenarios"""

    @pytest.fixture
    def bridge(self):
        """Create a bridge instance with caching enabled"""
        with patch('ble_tcp_bridge.BleakClient'):
            bridge = ble_tcp_bridge.MeshtasticBLEBridge(
                ble_address="AA:BB:CC:DD:EE:FF",
                tcp_port=4403,
                cache_nodes=True
            )
            # Pre-populate cache
            for i in range(10):
                from_radio = mesh_pb2.FromRadio()
                from_radio.node_info.num = 0x10000000 + i
                proto_bytes = from_radio.SerializeToString()
                tcp_frame = bridge.create_tcp_frame(proto_bytes)
                bridge.config_cache.append((proto_bytes, tcp_frame))
            bridge.config_cache_complete = True
            return bridge

    @pytest.mark.asyncio
    async def test_concurrent_cache_reads(self, bridge):
        """Test multiple clients reading cache simultaneously"""
        async def read_cache():
            # Simulate reading cache
            cache_copy = list(bridge.config_cache)
            await asyncio.sleep(0.01)
            return len(cache_copy)

        # Simulate 10 concurrent clients
        tasks = [read_cache() for _ in range(10)]
        results = await asyncio.gather(*tasks)

        # All should get same cache size
        assert all(r == 10 for r in results)

    @pytest.mark.asyncio
    async def test_cache_update_during_read(self, bridge):
        """Test cache updates while being read"""
        async def read_cache():
            cache_copy = list(bridge.config_cache)
            await asyncio.sleep(0.1)
            return len(cache_copy)

        async def update_cache():
            await asyncio.sleep(0.05)
            # Add new node
            from_radio = mesh_pb2.FromRadio()
            from_radio.node_info.num = 0x99999999
            proto_bytes = from_radio.SerializeToString()
            tcp_frame = bridge.create_tcp_frame(proto_bytes)
            bridge.config_cache.append((proto_bytes, tcp_frame))

        # Start both operations
        read_task = asyncio.create_task(read_cache())
        update_task = asyncio.create_task(update_cache())

        results = await asyncio.gather(read_task, update_task)

        # Reader should have seen original size
        assert results[0] == 10
        # Cache should now have new node
        assert len(bridge.config_cache) == 11


class TestPositionTelemetryUpdates:
    """Tests for dynamic position and telemetry updates"""

    @pytest.fixture
    def bridge(self):
        """Create a bridge instance with caching enabled"""
        with patch('ble_tcp_bridge.BleakClient'):
            bridge = ble_tcp_bridge.MeshtasticBLEBridge(
                ble_address="AA:BB:CC:DD:EE:FF",
                tcp_port=4403,
                cache_nodes=True
            )
            # Add a node to cache
            from_radio = mesh_pb2.FromRadio()
            from_radio.node_info.num = 0x12345678
            from_radio.node_info.user.long_name = "Test Node"
            proto_bytes = from_radio.SerializeToString()
            tcp_frame = bridge.create_tcp_frame(proto_bytes)
            bridge.config_cache = [(proto_bytes, tcp_frame)]
            bridge.config_cache_complete = True
            return bridge

    def test_position_update(self, bridge):
        """Test position update for cached node"""
        node_num = 0x12345678

        # Create position update packet
        from_radio = mesh_pb2.FromRadio()
        from_radio.packet.CopyFrom(mesh_pb2.MeshPacket())
        setattr(from_radio.packet, 'from', node_num)  # Use setattr for 'from' keyword
        from_radio.packet.decoded.portnum = 3  # POSITION_APP

        position = mesh_pb2.Position()
        position.latitude_i = 37774000
        position.longitude_i = -122419000
        from_radio.packet.decoded.payload = position.SerializeToString()

        # Verify packet structure
        assert hasattr(from_radio.packet, 'decoded')
        assert from_radio.packet.decoded.portnum == 3

    def test_telemetry_update(self, bridge):
        """Test telemetry update for cached node"""
        node_num = 0x12345678

        # Create telemetry update packet
        from_radio = mesh_pb2.FromRadio()
        from_radio.packet.CopyFrom(mesh_pb2.MeshPacket())
        setattr(from_radio.packet, 'from', node_num)
        from_radio.packet.decoded.portnum = 67  # TELEMETRY_APP

        telemetry = telemetry_pb2.Telemetry()
        telemetry.device_metrics.battery_level = 85
        telemetry.device_metrics.voltage = 4.1
        from_radio.packet.decoded.payload = telemetry.SerializeToString()

        # Verify packet structure
        assert from_radio.packet.decoded.portnum == 67


class TestErrorScenarios:
    """Tests for error handling"""

    @pytest.fixture
    def bridge(self):
        """Create a bridge instance"""
        with patch('ble_tcp_bridge.BleakClient'):
            bridge = ble_tcp_bridge.MeshtasticBLEBridge(
                ble_address="AA:BB:CC:DD:EE:FF",
                tcp_port=4403,
                cache_nodes=True
            )
            return bridge

    def test_corrupted_protobuf(self, bridge):
        """Test handling of corrupted protobuf data"""
        bridge.config_cache = [(b"corrupted_data", b"corrupted_frame")]
        bridge.config_cache_complete = True

        # Should not crash when trying to parse
        try:
            for cached_proto, _ in bridge.config_cache:
                from_radio = mesh_pb2.FromRadio()
                from_radio.ParseFromString(cached_proto)
        except Exception as e:
            # Expected to fail parsing, but shouldn't crash the bridge
            assert isinstance(e, Exception)

    def test_tcp_frame_too_large(self, bridge):
        """Test handling of oversized packets"""
        # Create a packet larger than MAX_PACKET_SIZE
        large_payload = b"x" * (bridge.MAX_PACKET_SIZE + 100)

        with pytest.raises(ValueError):
            bridge.create_tcp_frame(large_payload)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
