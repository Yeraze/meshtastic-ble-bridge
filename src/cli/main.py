#!/usr/bin/env python3
"""
Meshtastic BLE-to-TCP Bridge - CLI Entry Point

Connects to a Meshtastic device via Bluetooth Low Energy (BLE) and exposes
a TCP server that speaks the Meshtastic TCP framing protocol.

Usage:
    python -m cli.main <BLE_ADDRESS> [--port 4403] [--cache-nodes] [--verbose]
    python -m cli.main --scan

Example:
    python -m cli.main AA:BB:CC:DD:EE:FF --port 4403 --verbose
"""
import asyncio
import argparse
import logging
import signal
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.bridge import MeshtasticBridge
from core.ble_handler import BLEHandler
from core.stats import StatsCollector
from core import __version__

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False):
    """Configure logging"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )


async def scan_for_devices():
    """Scan for nearby Meshtastic BLE devices"""
    stats = StatsCollector()
    ble_handler = BLEHandler("", stats)  # Empty address for scan
    devices = await ble_handler.scan_devices()

    if devices:
        print(f"\nFound {len(devices)} Meshtastic device(s):")
        for device in devices:
            print(f"  {device.name or 'Unknown'} - {device.address}")
        print("\nUse the MAC address with this script to connect")
    else:
        print("No Meshtastic devices found")

    return devices


async def run_bridge(args):
    """Run the bridge with given arguments"""
    # Create bridge
    bridge = MeshtasticBridge(
        ble_address=args.address,
        tcp_port=args.port,
        cache_enabled=args.cache_nodes,
        max_cache_nodes=args.max_cache_nodes
    )

    # Setup signal handlers for graceful shutdown
    loop = asyncio.get_running_loop()

    def signal_handler():
        logger.info("Received shutdown signal")
        asyncio.create_task(bridge.stop())
        loop.stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    try:
        # Start bridge
        await bridge.start()

        # Run forever
        await bridge.serve_forever()

    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    except Exception as e:
        logger.error(f"Bridge error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        await bridge.stop()


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Meshtastic BLE-to-TCP Bridge',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Scan for devices
  %(prog)s --scan

  # Connect to device
  %(prog)s AA:BB:CC:DD:EE:FF

  # With caching enabled
  %(prog)s AA:BB:CC:DD:EE:FF --cache-nodes

  # Custom port and verbose logging
  %(prog)s AA:BB:CC:DD:EE:FF --port 4404 --verbose
        """
    )

    parser.add_argument(
        'address',
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
        help='Scan for nearby Meshtastic devices and exit'
    )

    parser.add_argument(
        '--cache-nodes',
        action='store_true',
        help='Enable config caching for faster reconnections'
    )

    parser.add_argument(
        '--max-cache-nodes',
        type=int,
        default=500,
        help='Maximum nodes to cache (default: 500)'
    )

    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose (DEBUG) logging'
    )

    parser.add_argument(
        '--version',
        action='version',
        version=f'%(prog)s {__version__}'
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging(args.verbose)

    logger.info(f"Meshtastic BLE Bridge v{__version__}")

    # Handle scan mode
    if args.scan:
        asyncio.run(scan_for_devices())
        return

    # Validate address
    if not args.address:
        parser.error("BLE address required (use --scan to find devices)")

    # Check environment variables for config
    if 'MAX_CACHE_NODES' in os.environ:
        try:
            args.max_cache_nodes = int(os.environ['MAX_CACHE_NODES'])
            logger.info(f"Using MAX_CACHE_NODES from environment: {args.max_cache_nodes}")
        except ValueError:
            logger.warning(f"Invalid MAX_CACHE_NODES value: {os.environ['MAX_CACHE_NODES']}")

    # Run bridge
    try:
        asyncio.run(run_bridge(args))
    except KeyboardInterrupt:
        logger.info("Exiting...")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
