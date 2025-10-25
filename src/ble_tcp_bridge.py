#!/usr/bin/env python3
"""
Meshtastic BLE-to-TCP Bridge

Connects to a Meshtastic device via Bluetooth Low Energy (BLE) and exposes
a TCP server that speaks the Meshtastic TCP framing protocol, allowing
MeshMonitor to connect to BLE-only devices.

Usage:
    python ble_tcp_bridge.py <BLE_ADDRESS> [--port 4403] [--verbose]

Example:
    python ble_tcp_bridge.py AA:BB:CC:DD:EE:FF --port 4403 --verbose

Requirements:
    pip install meshtastic bleak
"""

import asyncio
import socket
import struct
import logging
import argparse
import sys
import os
import signal
import time
from typing import List, Optional
from bleak import BleakClient, BleakScanner
from meshtastic import mesh_pb2, telemetry_pb2

# Version
__version__ = "1.4.0"

# IMPORTANT: Config caching behavior
# When --cache-nodes is enabled, the bridge caches the ENTIRE config response
# including radio settings, channels, modules, and node database. This dramatically
# improves reconnection speed but has limitations:
#
# ✅ WORKS WELL FOR: Messaging, monitoring, read-only usage
# ⚠️  LIMITATIONS: Reconfiguration via the app may not work as expected since the
#    cached config doesn't reflect real-time changes. If you need to reconfigure
#    your device, restart the bridge or disable caching with: --cache-nodes=false

# TCP Protocol constants
START1 = 0x94
START2 = 0xC3
MAX_PACKET_SIZE = 512

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class MeshtasticBLEBridge:
    """
    Bridges Meshtastic BLE to TCP protocol for MeshMonitor compatibility.
    """

    # Reconnection behavior constants
    MAX_RECONNECT_ATTEMPTS = 5
    INITIAL_RECONNECT_DELAY = 2.0  # seconds
    MAX_RECONNECT_DELAY = 60.0  # seconds
    RECONNECT_BACKOFF_FACTOR = 2.0

    def __init__(self, ble_address: str, tcp_port: int = 4403, cache_nodes: bool = False, max_cache_nodes: int = 500):
        self.ble_address = ble_address
        self.tcp_port = tcp_port
        self.cache_nodes = cache_nodes
        self.max_cache_nodes = max_cache_nodes
        self.ble_client: Optional[BleakClient] = None

        # Meshtastic BLE characteristic UUIDs
        self.MESHTASTIC_SERVICE_UUID = "6ba1b218-15a8-461f-9fa8-5dcae273eafd"
        self.TORADIO_UUID = "f75c76d2-129e-4dad-a1dd-7866124401e7"  # Write to device
        self.FROMRADIO_UUID = "2c55e69e-4993-11ed-b878-0242ac120002"  # Read/notify from device

        self.tcp_clients: List[asyncio.StreamWriter] = []
        self.running = False
        self.poll_task: Optional[asyncio.Task] = None
        self.tcp_server = None

        # Reconnection state
        self.reconnect_attempts = 0
        self.is_reconnecting = False
        self.disconnection_event = asyncio.Event()

        # Avahi service file path
        self.avahi_service_file = None

        # Full config cache for replaying entire want_config response
        # WARNING: This caches the entire config response including radio settings,
        # channels, modules, etc. Only suitable for messaging use cases where the
        # device config doesn't change. Reconfiguration features may not work correctly.
        self.config_cache: list = [] if cache_nodes else None  # List of (protobuf_bytes, tcp_frame)
        self.config_cache_complete: bool = False
        self.recording_config: bool = False
        self.current_config_id: Optional[int] = None

        # Packet deduplication (to handle BLE notification + polling duplicates)
        self.last_packet_hash: Optional[int] = None
        self.last_packet_time: float = 0

    def on_ble_disconnect(self, client: BleakClient):
        """
        Callback invoked when BLE device disconnects.
        Triggers reconnection logic.
        """
        logger.warning(f"⚠️  BLE device disconnected: {self.ble_address}")
        self.disconnection_event.set()

    async def start(self):
        """Start the BLE-TCP bridge."""
        logger.info(f"Starting BLE-TCP Bridge")
        logger.info(f"BLE Address: {self.ble_address}")
        logger.info(f"TCP Port: {self.tcp_port}")
        if self.cache_nodes:
            logger.info(f"Node Caching: Enabled (max {self.max_cache_nodes} nodes)")
        else:
            logger.info(f"Node Caching: Disabled (use --cache-nodes to enable)")

        self.running = True

        # Connect to BLE device
        await self.connect_ble()

        # Register mDNS service for autodiscovery
        await self.register_mdns_service()

        # Pre-warm cache if caching is enabled
        if self.cache_nodes:
            await self.prewarm_cache()

        # Start TCP server
        await self.start_tcp_server()

    async def prewarm_cache(self):
        """
        Pre-warm the config cache by requesting config from the device.
        This ensures the cache is hot before the first client connects.
        """
        logger.info("🔥 Pre-warming config cache...")

        try:
            # Generate a unique config ID
            import random
            config_id = random.randint(1, 2**32 - 1)

            # Start recording the config response
            self.recording_config = True
            self.current_config_id = config_id
            self.config_cache.clear()
            self.config_cache_complete = False

            # Create want_config_id request
            to_radio = mesh_pb2.ToRadio()
            to_radio.want_config_id = config_id

            # Send to BLE device
            await self.send_to_ble(to_radio)

            logger.info(f"📨 Sent want_config_id request to device (id={config_id})")

            # Wait for config_complete_id
            max_wait = 30  # seconds
            check_interval = 0.5  # seconds
            waited = 0

            while waited < max_wait:
                await asyncio.sleep(check_interval)
                waited += check_interval

                # Check if config is complete
                if self.config_cache_complete:
                    logger.info(f"✅ Config cache pre-warmed with {len(self.config_cache)} packets")
                    self.recording_config = False
                    return

            logger.warning(f"⚠️  Config cache pre-warming timed out after {max_wait}s (cache size: {len(self.config_cache)})")
            self.recording_config = False

        except Exception as e:
            logger.warning(f"⚠️  Cache pre-warming failed: {e} (bridge will continue without pre-warmed cache)")
            self.recording_config = False

    async def connect_ble(self):
        """Connect to Meshtastic device via BLE using Bleak directly."""
        logger.info(f"Connecting to BLE device: {self.ble_address}")

        try:
            # First, check if device is already connected and disconnect if so
            # This handles the case where the device is connected from a previous session
            try:
                logger.debug("Checking for existing connection...")
                temp_client = BleakClient(self.ble_address, timeout=5.0)

                # Try to check connection status
                devices = await BleakScanner.discover(timeout=2.0, return_adv=True)
                device_found = False

                for device_addr, (device, adv_data) in devices.items():
                    if device.address.upper() == self.ble_address.upper():
                        device_found = True
                        logger.debug(f"Device found during scan: {device.name}")
                        break

                if not device_found:
                    logger.debug("Device not found in scan, attempting direct connection...")

            except Exception as scan_err:
                logger.debug(f"Scan check failed (this is OK): {scan_err}")

            # Create BleakClient with timeout
            self.ble_client = BleakClient(self.ble_address, timeout=20.0)

            # Connect (if already connected, this should complete quickly)
            try:
                await self.ble_client.connect()
            except Exception as conn_err:
                # If connection fails, it might be because device is already connected
                # Try disconnecting first, then reconnect
                logger.warning(f"Initial connection failed: {conn_err}")
                logger.info("Attempting to disconnect any existing connection...")

                try:
                    # Try to disconnect using a temporary client
                    disconnect_client = BleakClient(self.ble_address, timeout=5.0)
                    if await disconnect_client.connect():
                        await disconnect_client.disconnect()
                        logger.info("Disconnected existing connection")
                        await asyncio.sleep(2)  # Wait for cleanup
                except Exception as disc_err:
                    logger.debug(f"Disconnect attempt result: {disc_err}")

                # Retry connection
                logger.info("Retrying connection...")
                await self.ble_client.connect()

            if not self.ble_client.is_connected:
                raise RuntimeError("Failed to establish BLE connection")

            # Wait for service discovery to complete
            logger.debug("Waiting for service discovery...")
            max_wait = 10  # seconds
            wait_interval = 0.5  # seconds
            waited = 0

            while waited < max_wait:
                # Try to get the services - if they're not ready, this will be empty or incomplete
                try:
                    services = self.ble_client.services
                    # Check if our Meshtastic service UUID is present
                    if services and any(str(s.uuid).lower() == self.MESHTASTIC_SERVICE_UUID.lower() for s in services):
                        logger.debug(f"Service discovery complete ({waited:.1f}s)")
                        break
                except Exception:
                    pass

                await asyncio.sleep(wait_interval)
                waited += wait_interval
            else:
                logger.warning(f"Service discovery may be incomplete after {max_wait}s")

            # Register disconnection callback
            self.ble_client.set_disconnected_callback(self.on_ble_disconnect)

            # Reset reconnection state on successful connection
            self.reconnect_attempts = 0
            self.is_reconnecting = False
            self.disconnection_event.clear()

            logger.info(f"✅ Connected to BLE device: {self.ble_address}")

            # Start polling task for FromRadio characteristic (it doesn't support notifications)
            self.poll_task = asyncio.create_task(self.poll_from_radio())
            logger.debug(f"✅ Started polling FromRadio characteristic")

        except Exception as e:
            logger.error(f"❌ Failed to connect to BLE device: {e}")
            raise

    async def attempt_reconnection(self):
        """
        Attempt to reconnect to BLE device with exponential backoff.
        Returns True if reconnected successfully, False if max attempts exceeded.
        """
        if self.is_reconnecting:
            logger.debug("Reconnection already in progress")
            return True

        self.is_reconnecting = True

        while self.reconnect_attempts < self.MAX_RECONNECT_ATTEMPTS and self.running:
            self.reconnect_attempts += 1
            current_attempt = self.reconnect_attempts  # Save for logging after successful connect

            # Calculate backoff delay with exponential increase
            delay = min(
                self.INITIAL_RECONNECT_DELAY * (self.RECONNECT_BACKOFF_FACTOR ** (self.reconnect_attempts - 1)),
                self.MAX_RECONNECT_DELAY
            )

            logger.info(f"🔄 Reconnection attempt {self.reconnect_attempts}/{self.MAX_RECONNECT_ATTEMPTS} in {delay:.1f}s...")
            await asyncio.sleep(delay)

            try:
                # Disconnect old client if still exists
                if self.ble_client:
                    try:
                        if self.ble_client.is_connected:
                            await self.ble_client.disconnect()
                    except Exception as disc_err:
                        logger.debug(f"Error disconnecting old client: {disc_err}")
                    self.ble_client = None

                # Attempt reconnection (this will reset reconnect_attempts to 0 on success)
                await self.connect_ble()

                # Re-warm cache if caching is enabled (device needs want_config to start sending data)
                if self.cache_nodes:
                    await self.prewarm_cache()

                logger.info(f"✅ Reconnected successfully after {current_attempt} attempt(s)")
                self.is_reconnecting = False
                return True

            except Exception as e:
                logger.warning(f"❌ Reconnection attempt {self.reconnect_attempts} failed: {e}")

                if self.reconnect_attempts >= self.MAX_RECONNECT_ATTEMPTS:
                    logger.error(f"💀 Max reconnection attempts ({self.MAX_RECONNECT_ATTEMPTS}) exceeded")
                    self.is_reconnecting = False
                    return False

        self.is_reconnecting = False
        return False

    async def register_mdns_service(self):
        """Register mDNS service via Avahi service file for autodiscovery.

        Writes a service file to /etc/avahi/services/ which the host's Avahi
        daemon will automatically detect and publish on the network.

        Requires: -v /etc/avahi/services:/etc/avahi/services
        """
        import os

        try:
            # Create a sanitized service name from BLE address
            sanitized_addr = self.ble_address.replace(':', '').lower()
            service_name = f"Meshtastic BLE Bridge ({sanitized_addr[-6:]})"

            # Create Avahi service XML
            service_xml = f'''<?xml version="1.0" standalone="no"?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name>{service_name}</name>
  <service>
    <type>_meshtastic._tcp</type>
    <port>{self.tcp_port}</port>
    <txt-record>bridge=ble</txt-record>
    <txt-record>port={self.tcp_port}</txt-record>
    <txt-record>ble_address={self.ble_address}</txt-record>
    <txt-record>version={__version__}</txt-record>
  </service>
</service-group>
'''

            service_file_path = f"/etc/avahi/services/meshtastic-ble-bridge-{sanitized_addr}.service"

            try:
                # Write service file for host's Avahi daemon
                with open(service_file_path, 'w') as f:
                    f.write(service_xml)
                self.avahi_service_file = service_file_path

                logger.info(f"✅ mDNS service registered: {service_name}")
                logger.info(f"   Service type: _meshtastic._tcp.local.")
                logger.info(f"   Port: {self.tcp_port}")
                logger.info(f"   Host Avahi will publish this service automatically")
                logger.info(f"   Test with: avahi-browse -rt _meshtastic._tcp")

            except PermissionError:
                logger.warning(f"Cannot write to {service_file_path}")
                logger.warning(f"mDNS autodiscovery will not work (TCP bridge still functional)")
                logger.info(f"Mount /etc/avahi/services with: -v /etc/avahi/services:/etc/avahi/services")

        except Exception as e:
            logger.warning(f"Failed to register mDNS service (bridge will still work): {e}")
            logger.debug(f"mDNS registration error details:", exc_info=True)

    async def poll_from_radio(self):
        """
        Poll the FromRadio characteristic for incoming data.
        The Meshtastic BLE API uses read-based polling, not notifications.
        Monitors connection health and triggers reconnection on disconnect.
        """
        logger.debug("Starting FromRadio polling loop")

        while self.running:
            try:
                # Check if we're connected
                if not self.ble_client or not self.ble_client.is_connected:
                    logger.warning("⚠️  BLE connection lost in poll loop")

                    # Attempt reconnection
                    reconnected = await self.attempt_reconnection()

                    if not reconnected:
                        logger.error("💀 Failed to reconnect to BLE device - exiting for container restart")
                        # Exit with error code so Docker can restart the container
                        self.running = False
                        sys.exit(1)

                    # Successfully reconnected, continue polling
                    continue

                # Read from FromRadio characteristic
                data = await self.ble_client.read_gatt_char(self.FROMRADIO_UUID)

                if data and len(data) > 0:
                    logger.debug(f"📥 Polled {len(data)} bytes from FromRadio")
                    await self.on_ble_packet(bytes(data))

                # Poll every 100ms (10Hz)
                await asyncio.sleep(0.1)

            except Exception as e:
                if self.running:
                    logger.error(f"Error polling FromRadio: {e}")

                    # If error suggests disconnection, trigger reconnection
                    if "not connected" in str(e).lower() or "disconnected" in str(e).lower():
                        logger.warning("⚠️  Detected disconnection via error message")
                        reconnected = await self.attempt_reconnection()

                        if not reconnected:
                            logger.error("💀 Failed to reconnect after error - exiting for container restart")
                            self.running = False
                            sys.exit(1)
                    else:
                        # Other error, back off and retry
                        await asyncio.sleep(1.0)
                else:
                    break

        logger.debug("FromRadio polling loop ended")

    async def on_ble_packet(self, protobuf_bytes: bytes):
        """
        Handle incoming packet from BLE.
        Convert to TCP frame and broadcast to all TCP clients.
        Optionally cache NodeInfo packets for replay to new clients.

        Note: Deduplicates packets since BLE may send duplicates via
        both notifications and polling within a short time window.
        """
        try:
            # Deduplicate packets (BLE may send same packet via notification + poll)
            packet_hash = hash(protobuf_bytes)
            current_time = time.time()

            # If same packet within 100ms, skip it
            if (packet_hash == self.last_packet_hash and
                (current_time - self.last_packet_time) < 0.1):
                logger.debug(f"⏭️  Skipping duplicate packet ({len(protobuf_bytes)} bytes)")
                return

            self.last_packet_hash = packet_hash
            self.last_packet_time = current_time

            logger.debug(f"📥 BLE packet received: {len(protobuf_bytes)} bytes")

            # Create TCP frame
            tcp_frame = self.create_tcp_frame(protobuf_bytes)

            # If config caching is enabled, record the config response stream
            if self.config_cache is not None:
                try:
                    from_radio = mesh_pb2.FromRadio()
                    from_radio.ParseFromString(protobuf_bytes)

                    # Check if this is a config_complete_id - end of config stream
                    if from_radio.HasField('config_complete_id'):
                        if self.recording_config and from_radio.config_complete_id == self.current_config_id:
                            # Add the config_complete packet to cache
                            self.config_cache.append((protobuf_bytes, tcp_frame))
                            self.config_cache_complete = True
                            self.recording_config = False

                            # Enforce cache size limit by counting node_info packets
                            node_count = 0
                            for proto, _ in self.config_cache:
                                try:
                                    temp_radio = mesh_pb2.FromRadio()
                                    temp_radio.ParseFromString(proto)
                                    if temp_radio.HasField('node_info'):
                                        node_count += 1
                                except Exception:
                                    pass

                            if node_count > self.max_cache_nodes:
                                # Remove oldest node_info entries until we're under the limit
                                nodes_to_remove = node_count - self.max_cache_nodes
                                new_cache = []
                                nodes_removed = 0

                                for proto, frame in self.config_cache:
                                    try:
                                        temp_radio = mesh_pb2.FromRadio()
                                        temp_radio.ParseFromString(proto)
                                        if temp_radio.HasField('node_info') and nodes_removed < nodes_to_remove:
                                            nodes_removed += 1
                                            continue  # Skip this node
                                    except Exception:
                                        pass  # Keep non-parseable packets
                                    new_cache.append((proto, frame))

                                self.config_cache = new_cache
                                logger.warning(f"⚠️  Cache size limit reached: removed {nodes_removed} oldest nodes (limit: {self.max_cache_nodes})")

                            logger.info(f"✅ Config cache complete with {len(self.config_cache)} packets")

                    # If we're recording, cache everything
                    elif self.recording_config:
                        self.config_cache.append((protobuf_bytes, tcp_frame))

                        # Log what we're caching
                        if from_radio.HasField('node_info'):
                            logger.debug(f"💾 Cached NodeInfo for node {from_radio.node_info.num:#x}")
                        elif from_radio.HasField('my_info'):
                            logger.debug(f"💾 Cached MyNodeInfo")
                        elif from_radio.HasField('config'):
                            logger.debug(f"💾 Cached Config")
                        elif from_radio.HasField('moduleConfig'):
                            logger.debug(f"💾 Cached ModuleConfig")
                        elif from_radio.HasField('channel'):
                            logger.debug(f"💾 Cached Channel")

                    # If cache is complete and this is a NodeInfo packet during runtime,
                    # update the cache to keep it fresh
                    elif self.config_cache_complete and from_radio.HasField('node_info'):
                        node_num = from_radio.node_info.num

                        # Find and replace existing NodeInfo for this node, or add if new
                        node_found = False
                        for i, (cached_proto, _) in enumerate(self.config_cache[:-1]):  # Skip last (config_complete)
                            try:
                                cached_from_radio = mesh_pb2.FromRadio()
                                cached_from_radio.ParseFromString(cached_proto)
                                if cached_from_radio.HasField('node_info') and cached_from_radio.node_info.num == node_num:
                                    # Replace with updated NodeInfo
                                    self.config_cache[i] = (protobuf_bytes, tcp_frame)
                                    logger.debug(f"🔄 Updated cache for node {node_num:#x}")
                                    node_found = True
                                    break
                            except Exception as e:
                                logger.debug(f"Failed to check cached node: {e}")
                                continue

                        # If node not found in cache, add it before config_complete
                        if not node_found:
                            # Insert before the last element (config_complete_id)
                            self.config_cache.insert(-1, (protobuf_bytes, tcp_frame))
                            logger.debug(f"➕ Added new node {node_num:#x} to cache (size: {len(self.config_cache)})")

                    # If cache is complete and this is a MeshPacket with Position/Telemetry/User data,
                    # update the corresponding NodeInfo in the cache
                    elif self.config_cache_complete and from_radio.HasField('packet'):
                        packet = from_radio.packet
                        node_num = getattr(packet, 'from')  # 'from' is a reserved keyword, use getattr()

                        # Check if packet has decoded data we care about
                        if packet.HasField('decoded'):
                            decoded = packet.decoded
                            update_type = None

                            # Determine what type of update this is
                            if decoded.portnum == 3:  # POSITION_APP
                                update_type = "position"
                            elif decoded.portnum == 67:  # TELEMETRY_APP
                                update_type = "telemetry"
                            elif decoded.portnum == 4:  # NODEINFO_APP
                                update_type = "user"

                            # If this is an update we care about, find and update the cached NodeInfo
                            if update_type and node_num:
                                for i, (cached_proto, _) in enumerate(self.config_cache[:-1]):
                                    try:
                                        cached_from_radio = mesh_pb2.FromRadio()
                                        cached_from_radio.ParseFromString(cached_proto)

                                        if cached_from_radio.HasField('node_info') and cached_from_radio.node_info.num == node_num:
                                            # Update the specific field in the cached NodeInfo
                                            if update_type == "position":
                                                # Parse position from packet payload
                                                position = mesh_pb2.Position()
                                                position.ParseFromString(decoded.payload)
                                                cached_from_radio.node_info.position.CopyFrom(position)
                                                logger.debug(f"📍 Updated position for node {node_num:#x}")

                                            elif update_type == "telemetry":
                                                # Parse telemetry from packet payload
                                                telemetry = telemetry_pb2.Telemetry()
                                                telemetry.ParseFromString(decoded.payload)
                                                if telemetry.HasField('device_metrics'):
                                                    cached_from_radio.node_info.device_metrics.CopyFrom(telemetry.device_metrics)
                                                    logger.debug(f"🔋 Updated telemetry for node {node_num:#x}")

                                            elif update_type == "user":
                                                # Parse user from packet payload
                                                user = mesh_pb2.User()
                                                user.ParseFromString(decoded.payload)
                                                cached_from_radio.node_info.user.CopyFrom(user)
                                                logger.debug(f"👤 Updated user for node {node_num:#x}")

                                            # Update last_heard timestamp (current time)
                                            cached_from_radio.node_info.last_heard = int(time.time())

                                            # Re-serialize and update cache
                                            updated_bytes = cached_from_radio.SerializeToString()
                                            updated_frame = self.create_tcp_frame(updated_bytes)
                                            self.config_cache[i] = (updated_bytes, updated_frame)
                                            break

                                    except Exception as e:
                                        logger.debug(f"Failed to update cache with {update_type}: {e}")
                                        continue

                except Exception as parse_err:
                    # Don't fail if parsing fails - just log and continue
                    logger.debug(f"Failed to parse packet for caching (non-critical): {parse_err}")

            # Broadcast to all TCP clients
            await self.broadcast_to_tcp(tcp_frame)

        except Exception as e:
            logger.error(f"Error handling BLE packet: {e}")

    def create_tcp_frame(self, protobuf_bytes: bytes) -> bytes:
        """
        Create TCP frame from protobuf bytes.

        Frame format:
        [START1][START2][LENGTH_MSB][LENGTH_LSB][PROTOBUF_PAYLOAD]
        """
        length = len(protobuf_bytes)

        if length > MAX_PACKET_SIZE:
            raise ValueError(f"Packet too large: {length} > {MAX_PACKET_SIZE}")

        # Create 4-byte header
        header = struct.pack('>BBH', START1, START2, length)

        # Combine header and payload
        return header + protobuf_bytes

    async def broadcast_to_tcp(self, frame: bytes):
        """Broadcast frame to all connected TCP clients."""
        if not self.tcp_clients:
            logger.debug("No TCP clients connected, dropping packet")
            return

        logger.debug(f"📤 Broadcasting to {len(self.tcp_clients)} TCP client(s)")

        disconnected = []
        for writer in self.tcp_clients:
            try:
                writer.write(frame)
                await writer.drain()
            except Exception as e:
                logger.warning(f"Failed to send to TCP client: {e}")
                disconnected.append(writer)

        # Remove disconnected clients
        for writer in disconnected:
            self.tcp_clients.remove(writer)
            logger.info(f"TCP client disconnected ({len(self.tcp_clients)} remaining)")

    async def start_tcp_server(self):
        """Start TCP server to accept connections from MeshMonitor."""
        self.tcp_server = await asyncio.start_server(
            self.handle_tcp_client,
            '0.0.0.0',  # Listen on all interfaces
            self.tcp_port
        )

        addr = self.tcp_server.sockets[0].getsockname()
        logger.info(f"✅ TCP server listening on {addr[0]}:{addr[1]}")
        logger.info(f"MeshMonitor can now connect to <bridge-ip>:{self.tcp_port}")

        self.running = True

        async with self.tcp_server:
            await self.tcp_server.serve_forever()

    async def handle_tcp_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle a new TCP client connection."""
        addr = writer.get_extra_info('peername')
        logger.info(f"🔌 TCP client connected from {addr}")

        self.tcp_clients.append(writer)

        # Config cache status
        if self.config_cache is not None and self.config_cache_complete:
            logger.info(f"📋 Config cache ready with {len(self.config_cache)} packets (will serve on want_config_id request)")

        try:
            while self.running:
                # Read TCP frame header (4 bytes)
                header = await reader.readexactly(4)

                # Validate frame start
                if header[0] != START1 or header[1] != START2:
                    logger.warning(f"Invalid frame start: {header[0]:02x} {header[1]:02x}")
                    continue

                # Parse length (big-endian 16-bit)
                length = struct.unpack('>H', header[2:4])[0]

                logger.debug(f"📥 TCP frame received: {length} bytes")

                # Read protobuf payload
                protobuf_bytes = await reader.readexactly(length)

                # Parse protobuf
                try:
                    to_radio = mesh_pb2.ToRadio()
                    to_radio.ParseFromString(protobuf_bytes)

                    # Check if this is a want_config_id request and we have a complete cache
                    if (self.config_cache is not None and
                        self.config_cache_complete and
                        to_radio.HasField('want_config_id')):

                        requested_id = to_radio.want_config_id
                        logger.info(f"🚀 Intercepting want_config_id={requested_id} - serving {len(self.config_cache)} packets from cache!")

                        # Replay entire cached config response, but update the config_complete_id
                        # to match what the client requested
                        for i, (cached_protobuf, cached_frame) in enumerate(self.config_cache):
                            try:
                                # If this is the last packet (config_complete_id), update the ID
                                if i == len(self.config_cache) - 1:
                                    # Parse and update the config_complete_id to match the request
                                    from_radio = mesh_pb2.FromRadio()
                                    from_radio.ParseFromString(cached_protobuf)
                                    from_radio.config_complete_id = requested_id

                                    # Re-serialize and create new frame
                                    updated_bytes = from_radio.SerializeToString()
                                    updated_frame = self.create_tcp_frame(updated_bytes)
                                    writer.write(updated_frame)
                                else:
                                    # Use cached frame as-is for all other packets
                                    writer.write(cached_frame)

                                await writer.drain()
                            except Exception as cache_err:
                                logger.warning(f"Failed to send cached config packet: {cache_err}")
                                break

                        logger.info(f"✅ Served complete config from cache with matching ID (skipped BLE request)")
                        continue  # Don't send to BLE

                    # Send via BLE
                    await self.send_to_ble(to_radio)

                except Exception as e:
                    logger.error(f"Failed to parse/send ToRadio packet: {e}")

        except asyncio.IncompleteReadError:
            logger.info(f"TCP client {addr} disconnected")
        except ConnectionResetError:
            logger.info(f"TCP client {addr} connection reset by peer")
        except Exception as e:
            logger.error(f"Error handling TCP client: {e}")
        finally:
            if writer in self.tcp_clients:
                self.tcp_clients.remove(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionResetError, ConnectionError, OSError):
                # Connection already closed by peer - this is fine
                pass
            logger.info(f"TCP client {addr} closed ({len(self.tcp_clients)} remaining)")

    async def send_to_ble(self, packet: mesh_pb2.ToRadio):
        """Send ToRadio packet to BLE device."""
        if not self.ble_client or not self.ble_client.is_connected:
            logger.warning("⚠️  Cannot send to BLE - not connected (will be dropped)")
            raise RuntimeError("BLE client not connected")

        try:
            logger.debug(f"📤 Sending packet to BLE")

            # Serialize the protobuf to bytes
            packet_bytes = packet.SerializeToString()

            # Write directly to the ToRadio characteristic using BleakClient
            await self.ble_client.write_gatt_char(self.TORADIO_UUID, packet_bytes)

            logger.debug(f"✅ Sent {len(packet_bytes)} bytes to BLE")

        except Exception as e:
            logger.error(f"Failed to send to BLE: {e}")

            # If error indicates disconnection, trigger the disconnection event
            if "not connected" in str(e).lower() or "disconnected" in str(e).lower():
                logger.warning("⚠️  Detected disconnection during send")
                self.disconnection_event.set()

            raise

    async def stop(self):
        """Stop the bridge."""
        import os
        logger.info("Stopping BLE-TCP bridge...")
        self.running = False

        # Close TCP server
        if self.tcp_server:
            logger.info("Closing TCP server...")
            self.tcp_server.close()
            await self.tcp_server.wait_closed()
            logger.info("✅ TCP server closed")

        # Remove Avahi service file
        if hasattr(self, 'avahi_service_file') and self.avahi_service_file:
            try:
                logger.info("Removing mDNS service file...")
                if os.path.exists(self.avahi_service_file):
                    os.remove(self.avahi_service_file)
                logger.info("✅ mDNS service file removed")
            except Exception as e:
                logger.warning(f"Failed to remove mDNS service file: {e}")

        # Cancel polling task
        if self.poll_task:
            self.poll_task.cancel()
            try:
                await self.poll_task
            except asyncio.CancelledError:
                pass

        # Disconnect BLE device
        if self.ble_client and self.ble_client.is_connected:
            try:
                logger.info("Disconnecting from BLE device...")
                await self.ble_client.disconnect()
                logger.info("✅ BLE device disconnected")
            except Exception as e:
                logger.warning(f"Failed to disconnect BLE device: {e}")


async def scan_for_meshtastic():
    """Scan for nearby Meshtastic BLE devices."""
    logger.info("Scanning for Meshtastic devices...")

    try:
        # Meshtastic service UUID
        MESHTASTIC_SERVICE_UUID = "6ba1b218-15a8-461f-9fa8-5dcae273eafd"

        devices = await BleakScanner.discover(timeout=10.0)

        meshtastic_devices = []
        for device in devices:
            # Check if device advertises Meshtastic service or has Meshtastic in name
            if device.name and ("meshtastic" in device.name.lower() or "ble" in device.name.lower()):
                meshtastic_devices.append(device)
                logger.info(f"  Found: {device.name} ({device.address})")
            # Also check UUIDs if available
            elif device.metadata.get("uuids"):
                if MESHTASTIC_SERVICE_UUID.lower() in [u.lower() for u in device.metadata.get("uuids", [])]:
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


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Meshtastic BLE-to-TCP Bridge for MeshMonitor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Connect to specific device
  %(prog)s AA:BB:CC:DD:EE:FF

  # Use custom TCP port
  %(prog)s AA:BB:CC:DD:EE:FF --port 14403

  # Scan for devices
  %(prog)s --scan

  # Verbose logging
  %(prog)s AA:BB:CC:DD:EE:FF --verbose
        """
    )

    parser.add_argument(
        'ble_address',
        nargs='?',
        help='BLE MAC address of Meshtastic device (e.g., AA:BB:CC:DD:EE:FF)'
    )
    parser.add_argument(
        '--port',
        type=int,
        default=4403,
        help='TCP port to listen on (default: 4403)'
    )
    parser.add_argument(
        '--scan',
        action='store_true',
        help='Scan for Meshtastic BLE devices and exit'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )
    parser.add_argument(
        '--cache-nodes',
        action='store_true',
        help='Cache NodeInfo packets and replay to new TCP clients (improves reconnection performance)'
    )
    parser.add_argument(
        '--max-cache-nodes',
        type=int,
        default=500,
        help='Maximum number of nodes to cache (default: 500, prevents unbounded memory growth)'
    )

    args = parser.parse_args()

    # Set logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Handle scan mode
    if args.scan:
        asyncio.run(scan_for_meshtastic())
        return

    # Validate BLE address - check argument first, then environment variable
    ble_address = args.ble_address or os.environ.get('BLE_ADDRESS')

    if not ble_address:
        parser.error("BLE address is required. Provide as argument or set BLE_ADDRESS environment variable (use --scan to find devices)")

    # Get max_cache_nodes from env var if set (for Docker compatibility)
    max_cache_nodes = args.max_cache_nodes
    if 'MAX_CACHE_NODES' in os.environ:
        try:
            max_cache_nodes = int(os.environ['MAX_CACHE_NODES'])
        except ValueError:
            logger.warning(f"Invalid MAX_CACHE_NODES value: {os.environ['MAX_CACHE_NODES']}, using default: {args.max_cache_nodes}")

    # Create and run bridge
    bridge = MeshtasticBLEBridge(ble_address, args.port, cache_nodes=args.cache_nodes, max_cache_nodes=max_cache_nodes)

    async def run_bridge():
        """Run bridge with proper shutdown handling."""
        loop = asyncio.get_running_loop()

        # Signal handler for graceful shutdown
        def handle_signal():
            logger.info("\n🛑 Received shutdown signal...")
            # Close the TCP server to trigger shutdown
            if bridge.tcp_server:
                bridge.tcp_server.close()

        # Register signal handlers with the event loop
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, handle_signal)

        try:
            await bridge.start()
        except KeyboardInterrupt:
            logger.info("\n🛑 Interrupted by user")
        except Exception as e:
            logger.error(f"❌ Bridge failed: {e}")
            # Exit with error code for container restart on fatal errors
            sys.exit(1)
        finally:
            # Remove signal handlers
            for sig in (signal.SIGTERM, signal.SIGINT):
                try:
                    loop.remove_signal_handler(sig)
                except Exception:
                    pass  # Ignore errors during cleanup
            await bridge.stop()

    try:
        asyncio.run(run_bridge())
    except KeyboardInterrupt:
        # Already handled in run_bridge
        pass
    except Exception as e:
        logger.error(f"❌ Bridge failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
