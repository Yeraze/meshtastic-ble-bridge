"""Main bridge orchestrator - platform agnostic core"""
import asyncio
import logging
import os
import time
from typing import Optional
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

    HEALTH_INTERVAL = 10.0  # seconds between health file updates
    POLL_STALE_AFTER = 30.0  # unhealthy if no FromRadio read completed for this long

    def __init__(self, ble_address: str, tcp_port: int = 4403,
                 cache_enabled: bool = False, max_cache_nodes: int = 500,
                 health_file: Optional[str] = None):
        self.ble_address = ble_address
        self.tcp_port = tcp_port
        # Touched periodically while BLE is connected, for container healthchecks
        self.health_file = health_file

        # Initialize components
        self.stats = StatsCollector()
        self.ble = BLEHandler(ble_address, self.stats)
        self.tcp = TCPHandler(tcp_port, self.stats)
        self.cache = CacheManager(cache_enabled, max_cache_nodes, self.stats)

        self.running = False
        self._shutdown_event: Optional[asyncio.Event] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._reinit_task: Optional[asyncio.Task] = None
        self._health_task: Optional[asyncio.Task] = None

        # Callback for reconnection failure
        self.on_reconnection_failed = None

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
        self._shutdown_event = asyncio.Event()
        self._remove_health_file()  # don't let a previous run's file report us healthy

        # Connect to BLE device
        await self.ble.connect()

        # Pre-warm cache if enabled
        if self.cache.enabled:
            await self.cache.prewarm(self._send_to_ble_raw)

        # Start TCP server
        await self.tcp.start()

        if self.health_file:
            self._health_task = asyncio.create_task(self._health_loop())

        logger.info("✅ Bridge started successfully")

    async def serve_forever(self):
        """
        Run bridge until request_shutdown() is called.

        Raises:
            RuntimeError: If the BLE polling loop stops (reconnection failed), so the
                process can exit non-zero and be restarted by Docker/systemd instead
                of serving TCP with no BLE link behind it.
        """
        server_task = asyncio.create_task(self.tcp.serve_forever())
        shutdown_task = asyncio.create_task(self._shutdown_event.wait())
        watched = {server_task, shutdown_task}
        if self.ble.poll_task:
            watched.add(self.ble.poll_task)

        try:
            await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)

            if self._shutdown_event.is_set():
                return
            if self.ble.poll_task and self.ble.poll_task.done():
                raise RuntimeError("BLE polling stopped (reconnection failed)")
            server_task.result()  # surface TCP server errors
            raise RuntimeError("TCP server stopped unexpectedly")
        finally:
            for task in (server_task, shutdown_task):
                task.cancel()
            await asyncio.gather(server_task, shutdown_task, return_exceptions=True)

    def request_shutdown(self):
        """Ask serve_forever() to return (safe to call from a signal handler)."""
        if self._shutdown_event:
            self._shutdown_event.set()

    def is_healthy(self) -> bool:
        """True while BLE is connected and the polling loop is running and making progress."""
        client = self.ble.client
        poll_task = self.ble.poll_task
        last_poll_ok = self.ble.last_poll_ok
        return bool(
            client and client.is_connected and not self.ble.link_lost
            and poll_task and not poll_task.done()
            and last_poll_ok is not None
            and time.monotonic() - last_poll_ok < self.POLL_STALE_AFTER
        )

    async def stop(self):
        """Stop the bridge"""
        logger.info("Stopping bridge...")
        self.running = False
        self.request_shutdown()

        # Cancel background work before tearing down BLE
        tasks = [t for t in (self._reconnect_task, self._reinit_task, self._health_task) if t]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._remove_health_file()

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

        except RuntimeError as e:
            error_msg = str(e)
            # Don't spam errors for expected reconnection states
            if "reconnecting" in error_msg.lower() or "not connected" in error_msg.lower():
                logger.debug(f"Dropping TCP packet during reconnection: {e}")
            else:
                logger.error(f"Error handling TCP packet: {e}")
        except Exception as e:
            logger.error(f"Error handling TCP packet: {e}")

    async def _send_to_ble_raw(self, packet_bytes: bytes):
        """
        Send raw protobuf bytes to BLE device.

        Args:
            packet_bytes: Raw protobuf bytes (ToRadio message)
        """
        await self.ble.send(packet_bytes)

    async def _handle_ble_disconnect(self) -> bool:
        """
        Handle BLE disconnection event.

        Called from both the Bleak disconnect callback and the polling loop; all
        callers share a single reconnect cycle and get its result.

        Returns:
            True if the BLE link is back, False if all attempts failed
        """
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(self._reconnect_cycle())
        # Shield so one cancelled caller doesn't abort the reconnect for the others
        return await asyncio.shield(self._reconnect_task)

    async def _reconnect_cycle(self) -> bool:
        """Run up to MAX_RECONNECT_ATTEMPTS reconnects, then re-initialize the device."""
        if not self.running:
            # Disconnect callback fired by our own shutdown
            return False

        client = self.ble.client
        if client and client.is_connected and not self.ble.link_lost:
            # Late callback for a link that is already back
            return True

        logger.warning("BLE disconnected, attempting reconnection...")

        # Each outage gets the full set of attempts
        self.ble.reconnect_attempts = 0

        # Attempt reconnection with multiple retries
        max_attempts = self.ble.MAX_RECONNECT_ATTEMPTS
        for attempt in range(1, max_attempts + 1):
            if not self.running:
                return False

            reconnected = await self.ble.attempt_reconnection()

            if reconnected:
                logger.info("✅ Reconnected to BLE device, re-initializing connection...")
                # Run in the background: the device's config response is read by the
                # polling loop, which is waiting on this reconnect to finish.
                if self._reinit_task and not self._reinit_task.done():
                    self._reinit_task.cancel()  # left over from the previous outage
                self._reinit_task = asyncio.create_task(self._reinitialize_device())
                return True

            # If not the last attempt, continue to next retry
            if attempt < max_attempts:
                logger.warning(f"Reconnection attempt {attempt}/{max_attempts} failed, will retry...")

        # All attempts exhausted
        logger.error("💀 Failed to reconnect to BLE device after all attempts")

        # Notify GUI/callback if registered
        if self.on_reconnection_failed:
            self.on_reconnection_failed()

        return False

    async def _reinitialize_device(self):
        """Send want_config_id after a reconnect so the device starts sending data."""
        # Re-warm cache if enabled
        if self.cache.enabled:
            await self.cache.prewarm(self._send_to_ble_raw)
            return

        # Even without cache, send want_config_id to initialize connection
        # This triggers the device to send its config, nodes, channels, etc.
        try:
            import random

            to_radio = mesh_pb2.ToRadio()
            to_radio.want_config_id = random.randint(1, 2**32 - 1)

            await self._send_to_ble_raw(to_radio.SerializeToString())
            logger.info("📨 Sent want_config_id to re-initialize device connection")
        except Exception as e:
            logger.warning(f"⚠️  Failed to send want_config_id: {e}")

    async def _health_loop(self):
        """Touch health_file while healthy; a stale file means BLE is down."""
        while True:
            if self.is_healthy():
                try:
                    with open(self.health_file, 'w') as f:
                        f.write(f"{time.time():.0f}\n")
                except OSError as e:
                    logger.warning(f"⚠️  Failed to update health file {self.health_file}: {e}")
            await asyncio.sleep(self.HEALTH_INTERVAL)

    def _remove_health_file(self):
        if self.health_file:
            try:
                os.remove(self.health_file)
            except FileNotFoundError:
                pass
            except OSError as e:
                logger.warning(f"⚠️  Failed to remove health file {self.health_file}: {e}")

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

    def register_failure_callback(self, callback):
        """
        Register callback for reconnection failure.

        Args:
            callback: Callable with no arguments, called when all reconnection attempts fail
        """
        self.on_reconnection_failed = callback
