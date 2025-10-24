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
import signal
from typing import List, Optional
from bleak import BleakClient, BleakScanner
from meshtastic import mesh_pb2

# Version
__version__ = "1.1"

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

    def __init__(self, ble_address: str, tcp_port: int = 4403):
        self.ble_address = ble_address
        self.tcp_port = tcp_port
        self.ble_client: Optional[BleakClient] = None

        # Meshtastic BLE characteristic UUIDs
        self.MESHTASTIC_SERVICE_UUID = "6ba1b218-15a8-461f-9fa8-5dcae273eafd"
        self.TORADIO_UUID = "f75c76d2-129e-4dad-a1dd-7866124401e7"  # Write to device
        self.FROMRADIO_UUID = "2c55e69e-4993-11ed-b878-0242ac120002"  # Read/notify from device

        self.tcp_clients: List[asyncio.StreamWriter] = []
        self.running = False
        self.poll_task: Optional[asyncio.Task] = None
        self.tcp_server = None

        # Avahi service file path
        self.avahi_service_file = None

    async def start(self):
        """Start the BLE-TCP bridge."""
        logger.info(f"Starting BLE-TCP Bridge")
        logger.info(f"BLE Address: {self.ble_address}")
        logger.info(f"TCP Port: {self.tcp_port}")

        self.running = True

        # Connect to BLE device
        await self.connect_ble()

        # Register mDNS service for autodiscovery
        await self.register_mdns_service()

        # Start TCP server
        await self.start_tcp_server()

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

            logger.info(f"✅ Connected to BLE device: {self.ble_address}")

            # Start polling task for FromRadio characteristic (it doesn't support notifications)
            self.poll_task = asyncio.create_task(self.poll_from_radio())
            logger.debug(f"✅ Started polling FromRadio characteristic")

        except Exception as e:
            logger.error(f"❌ Failed to connect to BLE device: {e}")
            raise

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
    <txt-record>version=1.0</txt-record>
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
        """
        logger.debug("Starting FromRadio polling loop")
        while self.running and self.ble_client and self.ble_client.is_connected:
            try:
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
                    await asyncio.sleep(1.0)  # Back off on error
                else:
                    break

        logger.debug("FromRadio polling loop ended")

    async def on_ble_packet(self, protobuf_bytes: bytes):
        """
        Handle incoming packet from BLE.
        Convert to TCP frame and broadcast to all TCP clients.
        """
        try:
            logger.debug(f"📥 BLE packet received: {len(protobuf_bytes)} bytes")

            # Create TCP frame
            tcp_frame = self.create_tcp_frame(protobuf_bytes)

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

                    # Send via BLE
                    await self.send_to_ble(to_radio)

                except Exception as e:
                    logger.error(f"Failed to parse/send ToRadio packet: {e}")

        except asyncio.IncompleteReadError:
            logger.info(f"TCP client {addr} disconnected")
        except Exception as e:
            logger.error(f"Error handling TCP client: {e}")
        finally:
            if writer in self.tcp_clients:
                self.tcp_clients.remove(writer)
            writer.close()
            await writer.wait_closed()
            logger.info(f"TCP client {addr} closed ({len(self.tcp_clients)} remaining)")

    async def send_to_ble(self, packet: mesh_pb2.ToRadio):
        """Send ToRadio packet to BLE device."""
        if not self.ble_client or not self.ble_client.is_connected:
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

    args = parser.parse_args()

    # Set logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Handle scan mode
    if args.scan:
        asyncio.run(scan_for_meshtastic())
        return

    # Validate BLE address
    if not args.ble_address:
        parser.error("BLE address is required (use --scan to find devices)")

    # Create and run bridge
    bridge = MeshtasticBLEBridge(args.ble_address, args.port)

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
            raise
        finally:
            # Remove signal handlers
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(sig)
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
