"""Main bridge orchestrator - platform agnostic core"""
import asyncio
import logging
from meshtastic import mesh_pb2
from .ble_handler import BLEHandler
from .tcp_handler import TCPHandler
from .cache_manager import CacheManager
from .protocol import ProtocolHandler
from .stats import StatsCollector

logger = logging.getLogger(__name__)


class MeshtasticBridge:
    """
    Platform-agnostic Meshtastic BLE-to-TCP bridge.
    Orchestrates BLE, TCP, caching, and statistics components.
    """

    def __init__(self, ble_address: str, tcp_port: int = 4403,
                 cache_enabled: bool = False, max_cache_nodes: int = 500):
        self.ble_address = ble_address
        self.tcp_port = tcp_port

        # Initialize components
        self.stats = StatsCollector()
        self.ble = BLEHandler(ble_address, self.stats)
        self.tcp = TCPHandler(tcp_port, self.stats)
        self.cache = CacheManager(cache_enabled, max_cache_nodes, self.stats)

        self.running = False

        # Wire up event handlers
        self.ble.on_packet_received = self._handle_ble_packet
        self.ble.on_disconnected = self._handle_ble_disconnect
        self.tcp.on_packet_received = self._handle_tcp_packet

    async def start(self):
        """Start the bridge"""
        logger.info(f"Starting Meshtastic BLE Bridge")
        logger.info(f"BLE Address: {self.ble_address}")
        logger.info(f"TCP Port: {self.tcp_port}")

        if self.cache.enabled:
            logger.info(f"Config Caching: Enabled (max {self.cache.max_nodes} nodes)")
        else:
            logger.info(f"Config Caching: Disabled")

        self.running = True

        # Connect to BLE device
        await self.ble.connect()

        # Pre-warm cache if enabled
        if self.cache.enabled:
            await self.cache.prewarm(self._send_to_ble_raw)

        # Start TCP server
        await self.tcp.start()

        logger.info("✅ Bridge started successfully")

    async def serve_forever(self):
        """Run bridge until stopped"""
        await self.tcp.serve_forever()

    async def stop(self):
        """Stop the bridge"""
        logger.info("Stopping bridge...")
        self.running = False

        # Stop TCP server
        await self.tcp.stop()

        # Disconnect BLE
        await self.ble.disconnect()

        logger.info("✅ Bridge stopped")

    async def _handle_ble_packet(self, protobuf_bytes: bytes):
        """
        Handle packet received from BLE device.

        Args:
            protobuf_bytes: Raw protobuf FromRadio message
        """
        try:
            # Create TCP frame
            tcp_frame = ProtocolHandler.create_tcp_frame(protobuf_bytes)

            # Update cache if enabled
            await self.cache.process_packet(protobuf_bytes, tcp_frame)

            # Broadcast to all TCP clients
            await self.tcp.broadcast(tcp_frame)

        except Exception as e:
            logger.error(f"Error handling BLE packet: {e}")

    async def _handle_tcp_packet(self, protobuf_bytes: bytes, client_addr):
        """
        Handle packet received from TCP client.

        Args:
            protobuf_bytes: Raw protobuf ToRadio message
            client_addr: Client address tuple (ip, port)
        """
        try:
            # Check if cache can serve this request
            if self.cache.can_serve(protobuf_bytes):
                frames = await self.cache.serve(protobuf_bytes)
                if frames:
                    await self.tcp.send_to_client(client_addr, frames)
                    logger.info(f"✅ Served config from cache (skipped BLE request)")
                    return

            # Forward to BLE device
            await self._send_to_ble_raw(protobuf_bytes)

        except Exception as e:
            logger.error(f"Error handling TCP packet: {e}")

    async def _send_to_ble_raw(self, packet_bytes: bytes):
        """
        Send raw protobuf bytes to BLE device.

        Args:
            packet_bytes: Raw protobuf bytes (ToRadio message)
        """
        await self.ble.send(packet_bytes)

    async def _handle_ble_disconnect(self):
        """Handle BLE disconnection event"""
        logger.warning("BLE disconnected, attempting reconnection...")

        # Attempt reconnection
        reconnected = await self.ble.attempt_reconnection()

        if reconnected:
            # Re-warm cache if enabled
            if self.cache.enabled:
                await self.cache.prewarm(self._send_to_ble_raw)
        else:
            logger.error("💀 Failed to reconnect to BLE device")
            # Let container orchestration handle restart

    def get_statistics(self):
        """Get current bridge statistics"""
        return self.stats.get_stats()

    def register_stats_callback(self, callback):
        """
        Register callback for statistics updates.

        Args:
            callback: Callable that takes BridgeStatistics as argument
        """
        self.stats.register_callback(callback)

    def unregister_stats_callback(self, callback):
        """
        Unregister statistics callback.

        Args:
            callback: Previously registered callback
        """
        self.stats.unregister_callback(callback)
