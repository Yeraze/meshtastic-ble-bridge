"""BLE connection handling for Meshtastic devices"""
import asyncio
import logging
from typing import Optional, Callable
from bleak import BleakClient, BleakScanner
from .stats import StatsCollector

logger = logging.getLogger(__name__)

# Meshtastic BLE UUIDs
MESHTASTIC_SERVICE_UUID = "6ba1b218-15a8-461f-9fa8-5dcae273eafd"
TORADIO_UUID = "f75c76d2-129e-4dad-a1dd-7866124401e7"  # Write to device
FROMRADIO_UUID = "2c55e69e-4993-11ed-b878-0242ac120002"  # Read from device


class BLEHandler:
    """Handles BLE connectivity and communication with Meshtastic device"""

    # Reconnection constants
    MAX_RECONNECT_ATTEMPTS = 10  # Increased from 5 - Meshtastic devices can take 2+ minutes to reboot
    INITIAL_RECONNECT_DELAY = 2.0  # seconds
    MAX_RECONNECT_DELAY = 60.0  # seconds
    RECONNECT_BACKOFF_FACTOR = 2.0

    def __init__(self, ble_address: str, stats: StatsCollector):
        self.ble_address = ble_address
        self.stats = stats

        self.client: Optional[BleakClient] = None
        self.poll_task: Optional[asyncio.Task] = None
        self.running = False

        # Reconnection state
        self.reconnect_attempts = 0
        self.is_reconnecting = False
        self.services_ready = False  # True only after service discovery completes
        self.disconnection_event = asyncio.Event()
        self.reconnect_lock = asyncio.Lock()  # Prevent concurrent reconnection

        # Callbacks
        self.on_packet_received: Optional[Callable] = None
        self.on_disconnected: Optional[Callable] = None

        # Packet deduplication
        self.last_packet_hash: Optional[int] = None
        self.last_packet_time: float = 0

        # Track initial vs reconnect
        self._initial_connect = True

    async def connect(self):
        """Connect to BLE device"""
        logger.info(f"Connecting to BLE device: {self.ble_address}")

        try:
            # Check if device is discoverable
            # During reconnection, scan to refresh Windows BLE cache
            # Use longer timeout for reconnection - Windows BLE can be slow to rediscover devices
            scan_timeout = 2.0 if self._initial_connect else 10.0
            discovered_device = None

            try:
                logger.debug(f"Scanning for device (timeout: {scan_timeout}s)...")
                devices = await BleakScanner.discover(timeout=scan_timeout, return_adv=True)
                device_found = False

                for device_addr, (device, adv_data) in devices.items():
                    if device.address.upper() == self.ble_address.upper():
                        device_found = True
                        discovered_device = device  # Save the device object
                        logger.info(f"✅ Device found in scan: {device.name}")
                        break

                if not device_found:
                    if self._initial_connect:
                        logger.debug("Device not found in scan, attempting direct connection...")
                    else:
                        # During reconnection, if device not found in scan, fail fast
                        logger.warning("⚠️  Device not found in scan during reconnection")
                        raise RuntimeError("Device not discoverable - may still be rebooting")

            except RuntimeError:
                # Re-raise our "not found" error
                raise
            except Exception as scan_err:
                logger.debug(f"Scan check failed (this is OK): {scan_err}")

            # Create BleakClient
            # Use discovered device object if available (fresher than cached MAC address)
            if discovered_device:
                logger.debug("Creating client from discovered device object")
                self.client = BleakClient(discovered_device, timeout=20.0)
            else:
                logger.debug("Creating client from MAC address")
                self.client = BleakClient(self.ble_address, timeout=20.0)

            # Connect (with timeout to fail fast during device reboot)
            try:
                # Only use disconnect-retry logic on initial connection
                # During reconnection, fail fast and let exponential backoff handle it
                if self._initial_connect:
                    # Initial connection - try disconnect-retry if needed
                    try:
                        await self.client.connect()
                    except Exception as conn_err:
                        logger.warning(f"Initial connection failed: {conn_err}")
                        logger.info("Attempting to disconnect any existing connection...")

                        try:
                            disconnect_client = BleakClient(self.ble_address, timeout=5.0)
                            if await disconnect_client.connect():
                                await disconnect_client.disconnect()
                                logger.info("Disconnected existing connection")
                                await asyncio.sleep(2)
                        except Exception as disc_err:
                            logger.debug(f"Disconnect attempt result: {disc_err}")

                        # Retry connection
                        logger.info("Retrying connection...")
                        await self.client.connect()
                else:
                    # Reconnection - fail fast with shorter timeout
                    await asyncio.wait_for(self.client.connect(), timeout=15.0)
            except asyncio.TimeoutError:
                raise RuntimeError(f"Connection timeout - device may still be rebooting")

            if not self.client.is_connected:
                raise RuntimeError("Failed to establish BLE connection")

            # Wait for service discovery
            logger.debug("Waiting for service discovery...")
            # Use shorter timeout during reconnection to fail fast
            max_wait = 10 if not self._initial_connect else 20
            wait_interval = 0.5
            waited = 0

            while waited < max_wait:
                try:
                    services = self.client.services
                    if services and any(
                        str(s.uuid).lower() == MESHTASTIC_SERVICE_UUID.lower()
                        for s in services
                    ):
                        logger.debug(f"Service discovery complete ({waited:.1f}s)")
                        self.services_ready = True
                        break
                except Exception:
                    pass

                await asyncio.sleep(wait_interval)
                waited += wait_interval
            else:
                error_msg = f"Service discovery timed out after {max_wait}s"
                logger.warning(f"⚠️  {error_msg}")
                self.services_ready = False
                # During reconnection, fail fast so next attempt can try
                if not self._initial_connect:
                    raise RuntimeError(error_msg)

            # Register disconnect callback
            self.client.set_disconnected_callback(self._on_ble_disconnect)

            # Reset reconnection state
            self.reconnect_attempts = 0
            self.is_reconnecting = False
            self.disconnection_event.clear()

            # Update stats
            await self.stats.on_ble_connected(self.ble_address)

            logger.info(f"✅ Connected to BLE device: {self.ble_address}")

            # Start polling task ONLY on initial connect, not on reconnect
            # (reconnect happens within the existing polling loop)
            if self._initial_connect:
                self.running = True
                self.poll_task = asyncio.create_task(self._poll_from_radio())
                logger.debug(f"✅ Started polling FromRadio characteristic")
                self._initial_connect = False

        except Exception as e:
            logger.error(f"❌ Failed to connect to BLE device: {e}")
            raise

    def _on_ble_disconnect(self, client: BleakClient):
        """Callback when BLE device disconnects"""
        logger.warning(f"⚠️  BLE device disconnected: {self.ble_address}")
        self.services_ready = False  # Services no longer available
        self.disconnection_event.set()

        # Notify bridge
        if self.on_disconnected:
            asyncio.create_task(self.on_disconnected())

    async def _poll_from_radio(self):
        """
        Poll FromRadio characteristic for incoming packets.
        Monitors connection health and triggers reconnection on disconnect.
        """
        logger.debug("Starting FromRadio polling loop")

        while self.running:
            try:
                # Check if still connected
                if not self.client or not self.client.is_connected:
                    logger.warning("⚠️  BLE connection lost during polling")

                    # Wait for bridge's disconnect handler to complete reconnection
                    # Don't call attempt_reconnection() ourselves - let the callback handle it
                    logger.debug("⏸️  Waiting for reconnection to complete...")
                    max_wait = 600  # Allow time for all 10 reconnection attempts (up to 10 minutes)
                    waited = 0

                    while waited < max_wait and self.running:
                        # Check if we're back online
                        if self.client and self.client.is_connected:
                            logger.info("✅ Reconnection completed, resuming polling")
                            break

                        # Still disconnected, wait a bit more
                        await asyncio.sleep(1)
                        waited += 1

                    # Check final state
                    if not self.client or not self.client.is_connected:
                        logger.error("💀 Reconnection failed after waiting, exiting polling loop")
                        self.running = False
                        break

                    # Reconnection succeeded, continue polling
                    continue

                # Read from FromRadio characteristic
                try:
                    data = await self.client.read_gatt_char(FROMRADIO_UUID)

                    if data and len(data) > 0:
                        # Deduplicate packets
                        import time
                        packet_hash = hash(bytes(data))
                        current_time = time.time()

                        if (packet_hash == self.last_packet_hash and
                            (current_time - self.last_packet_time) < 0.1):
                            logger.debug(f"⏭️  Skipping duplicate packet ({len(data)} bytes)")
                        else:
                            self.last_packet_hash = packet_hash
                            self.last_packet_time = current_time

                            logger.debug(f"📥 BLE packet received: {len(data)} bytes")
                            await self.stats.on_packet_from_ble(len(data))

                            # Notify callback
                            if self.on_packet_received:
                                await self.on_packet_received(bytes(data))

                except Exception as read_err:
                    if "not connected" in str(read_err).lower():
                        logger.warning("⚠️  Disconnection detected during read")
                        self.disconnection_event.set()
                        continue
                    else:
                        logger.debug(f"Read error (may be normal): {read_err}")

                await asyncio.sleep(0.1)  # 100ms polling interval

            except asyncio.CancelledError:
                logger.debug("Polling task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in polling loop: {e}")
                await asyncio.sleep(1)

        logger.debug("Polling loop ended")

    async def attempt_reconnection(self) -> bool:
        """
        Attempt to reconnect with exponential backoff.

        Returns:
            True if reconnected successfully, False if max attempts exceeded
        """
        # Quick check before acquiring lock to prevent redundant attempts
        if self.is_reconnecting:
            logger.debug("⏸️  Reconnection already in progress, waiting for it to complete...")
            # Wait for the other reconnection to finish
            # Max 600s to allow for all 10 reconnection attempts with exponential backoff
            max_wait = 600
            waited = 0
            while self.is_reconnecting and waited < max_wait:
                await asyncio.sleep(0.5)
                waited += 0.5

            if self.is_reconnecting:
                logger.warning(f"⚠️  Reconnection still in progress after {max_wait}s")
                return False

            # Reconnection finished, check result
            result = self.client and self.client.is_connected
            logger.debug(f"✅ Waited for reconnection to complete: {'success' if result else 'failed'}")
            return result

        async with self.reconnect_lock:
            # Check again after acquiring lock
            if self.is_reconnecting:
                logger.debug("⏸️  Reconnection already in progress (after lock)")
                return self.client and self.client.is_connected

            if self.reconnect_attempts >= self.MAX_RECONNECT_ATTEMPTS:
                logger.error(
                    f"💀 Maximum reconnection attempts ({self.MAX_RECONNECT_ATTEMPTS}) "
                    f"exceeded. Giving up."
                )
                return False

            self.is_reconnecting = True
            self.reconnect_attempts += 1

            # Calculate delay with exponential backoff
            delay = min(
                self.INITIAL_RECONNECT_DELAY * (
                    self.RECONNECT_BACKOFF_FACTOR ** (self.reconnect_attempts - 1)
                ),
                self.MAX_RECONNECT_DELAY
            )

            logger.info(
                f"🔄 Reconnection attempt {self.reconnect_attempts}/"
                f"{self.MAX_RECONNECT_ATTEMPTS} in {delay:.1f}s..."
            )

            await self.stats.on_reconnect_attempt()
            await asyncio.sleep(delay)

            try:
                # Fully clean up old client before creating new one
                if self.client:
                    logger.debug("Cleaning up old BLE client...")
                    try:
                        if self.client.is_connected:
                            await self.client.disconnect()
                            logger.debug("Disconnected old client")
                    except Exception as e:
                        logger.debug(f"Error disconnecting old client: {e}")

                    # Release the old client object
                    self.client = None

                    # Give Windows time to release BLE resources
                    await asyncio.sleep(1.0)
                    logger.debug("Released old client resources")

                # Reconnect with fresh client
                await self.connect()

                logger.info("✅ Reconnection successful")
                await self.stats.on_reconnect_success()
                self.reconnect_attempts = 0
                self.is_reconnecting = False
                return True

            except Exception as e:
                logger.error(f"❌ Reconnection attempt failed: {e}")
                self.is_reconnecting = False
                return False

    async def send(self, packet_bytes: bytes):
        """
        Send packet to BLE device via ToRadio characteristic.

        Args:
            packet_bytes: Raw protobuf bytes to send

        Raises:
            RuntimeError: If not connected or reconnecting
        """
        if not self.client or not self.client.is_connected:
            logger.warning("⚠️  Cannot send to BLE - not connected")
            raise RuntimeError("BLE client not connected")

        # Don't send during reconnection - characteristics may not be ready
        if self.is_reconnecting:
            logger.debug("⏸️  Skipping send during reconnection")
            raise RuntimeError("BLE client reconnecting, please retry")

        # Don't send if services aren't ready (after connect but before service discovery)
        if not self.services_ready:
            logger.debug("⏸️  Skipping send - BLE services not ready")
            raise RuntimeError("BLE services not ready, please retry")

        try:
            logger.debug(f"📤 Sending packet to BLE ({len(packet_bytes)} bytes)")

            await self.client.write_gatt_char(TORADIO_UUID, packet_bytes)
            await self.stats.on_packet_to_ble(len(packet_bytes))

            logger.debug(f"✅ Sent {len(packet_bytes)} bytes to BLE")

        except Exception as e:
            error_msg = str(e)

            # Handle characteristic not found (services not ready yet)
            if "characteristic" in error_msg.lower() and "not found" in error_msg.lower():
                logger.warning("⚠️  BLE characteristics not ready, triggering reconnection")
                self.disconnection_event.set()
                raise RuntimeError("BLE services not ready, reconnecting")

            logger.error(f"Failed to send to BLE: {e}")

            # Check if error indicates disconnection
            if "not connected" in error_msg.lower() or "disconnected" in error_msg.lower():
                logger.warning("⚠️  Detected disconnection during send")
                self.disconnection_event.set()

            raise

    async def disconnect(self):
        """Disconnect from BLE device"""
        self.running = False

        # Cancel polling task
        if self.poll_task:
            self.poll_task.cancel()
            try:
                await self.poll_task
            except asyncio.CancelledError:
                pass

        # Disconnect client
        if self.client and self.client.is_connected:
            try:
                await self.client.disconnect()
                logger.info("✅ Disconnected from BLE device")
            except Exception as e:
                logger.warning(f"Error during BLE disconnect: {e}")

        await self.stats.on_ble_disconnected()

        # Reset state for next connection
        self._initial_connect = True
        self.reconnect_attempts = 0
        self.is_reconnecting = False
        self.services_ready = False

    async def scan_devices(self) -> list:
        """
        Scan for nearby Meshtastic BLE devices.

        Returns:
            List of discovered Meshtastic devices
        """
        logger.info("Scanning for Meshtastic devices...")

        try:
            devices = await BleakScanner.discover(timeout=10.0)

            meshtastic_devices = []
            for device in devices:
                # Check by name
                if device.name and ("meshtastic" in device.name.lower() or
                                   "ble" in device.name.lower()):
                    meshtastic_devices.append(device)
                    logger.info(f"  Found: {device.name} ({device.address})")
                # Check by UUID
                elif device.metadata.get("uuids"):
                    if MESHTASTIC_SERVICE_UUID.lower() in [
                        u.lower() for u in device.metadata.get("uuids", [])
                    ]:
                        meshtastic_devices.append(device)
                        logger.info(f"  Found: {device.name or 'Unknown'} ({device.address})")

            if not meshtastic_devices:
                logger.warning("No Meshtastic devices found")
                logger.info("All devices found:")
                for device in devices:
                    logger.info(f"  {device.name or 'Unknown'} ({device.address})")

            return meshtastic_devices

        except Exception as e:
            logger.error(f"Scan failed: {e}")
            return []
