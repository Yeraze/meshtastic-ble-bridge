# Meshtastic BLE-to-TCP Bridge

A lightweight bridge application that allows MeshMonitor to connect to Meshtastic devices via Bluetooth Low Energy (BLE).

## Quick Start

```bash
# Install dependencies
pip install meshtastic bleak

# Scan for Meshtastic devices
python ble_tcp_bridge.py --scan

# Start bridge
python ble_tcp_bridge.py AA:BB:CC:DD:EE:FF

# In another terminal, start MeshMonitor configured for localhost:4403
docker-compose up
```

## What It Does

```
┌──────────────┐  TCP (localhost:4403)  ┌───────────────┐
│ MeshMonitor  │ ←────────────────────→ │  BLE Bridge   │
└──────────────┘                         └───────┬───────┘
                                                 │ BLE
                                         ┌───────▼───────┐
                                         │  Meshtastic   │
                                         │    Device     │
                                         └───────────────┘
```

The bridge:
1. Connects to a Meshtastic device via BLE
2. Listens for TCP connections on localhost:4403
3. Translates between BLE and TCP protocols
4. Registers mDNS service for network autodiscovery
5. Allows MeshMonitor to work with BLE-only devices

## Usage

### Scan for Devices

```bash
python ble_tcp_bridge.py --scan
```

Output:
```
Scanning for Meshtastic devices...
  Found: Meshtastic_1a2b (AA:BB:CC:DD:EE:FF)
  Found: Meshtastic_3c4d (11:22:33:44:55:66)
```

### Start Bridge

```bash
# Basic usage
python ble_tcp_bridge.py AA:BB:CC:DD:EE:FF

# Custom TCP port
python ble_tcp_bridge.py AA:BB:CC:DD:EE:FF --port 14403

# Verbose logging
python ble_tcp_bridge.py AA:BB:CC:DD:EE:FF --verbose
```

### Configure MeshMonitor

Update your `docker-compose.yml` or environment:

```yaml
environment:
  - MESHTASTIC_NODE_IP=localhost  # or host.docker.internal for Docker
  - MESHTASTIC_NODE_PORT=4403
```

## Requirements

- Python 3.8+
- Bluetooth adapter (built-in or USB)
- Meshtastic device with BLE enabled

## Supported Platforms

- **Linux** ✅ (BlueZ)
- **macOS** ✅ (Core Bluetooth)
- **Windows** ✅ (Windows BLE stack)
- **Raspberry Pi** ✅

## Troubleshooting

### "No BLE adapter found"
- Ensure Bluetooth is enabled
- On Linux: `sudo systemctl start bluetooth`
- Check `hciconfig` or `bluetoothctl` shows adapter

### "Permission denied"
On Linux, you may need to grant BLE permissions:
```bash
sudo setcap 'cap_net_raw,cap_net_admin+eip' $(which python3)
```

Or run with sudo (not recommended):
```bash
sudo python3 ble_tcp_bridge.py AA:BB:CC:DD:EE:FF
```

### "Device not found"
- Ensure Meshtastic device BLE is enabled
- Move closer to device (BLE range ~10-30m)
- Try scanning again
- Check device isn't already connected to another app

### "Connection timeout"
- Device may be paired to another client
- Try resetting device Bluetooth
- Ensure device firmware is up to date

## Docker Deployment (Recommended)

MeshMonitor includes an optional Docker Compose overlay for the BLE bridge. This is the easiest way to deploy both services together.

### Step 1: Find Your Meshtastic Device

```bash
# Scan for BLE devices
docker compose -f docker-compose.ble.yml run --rm ble-bridge --scan
```

Output:
```
Scanning for Meshtastic devices...
  Found: Meshtastic_1a2b (AA:BB:CC:DD:EE:FF)
```

### Step 2: Configure Environment

Create a `.env` file in the MeshMonitor root directory:

```bash
BLE_ADDRESS=AA:BB:CC:DD:EE:FF
```

### Step 3: Start Both Services

```bash
# Start MeshMonitor + BLE Bridge
docker compose -f docker-compose.yml -f docker-compose.ble.yml up -d

# View logs
docker compose -f docker-compose.yml -f docker-compose.ble.yml logs -f

# Stop services
docker compose -f docker-compose.yml -f docker-compose.ble.yml down
```

### How It Works

The `docker-compose.ble.yml` overlay:
- Adds the `ble-bridge` service (privileged mode for BLE access)
- Uses host networking for localhost TCP communication
- Configures MeshMonitor to connect to `localhost:4403`
- Includes health checks to ensure proper startup order

### Standalone Bridge Container

You can also run just the BLE bridge:

```bash
# Build the image
docker build -t meshmonitor-ble-bridge ./src

# Run the bridge
docker run --rm --privileged --network host \
  -v /var/run/dbus:/var/run/dbus \
  -v /var/lib/bluetooth:/var/lib/bluetooth:ro \
  -v /etc/avahi/services:/etc/avahi/services \
  meshmonitor-ble-bridge AA:BB:CC:DD:EE:FF

# With verbose logging
docker run --rm --privileged --network host \
  -v /var/run/dbus:/var/run/dbus \
  -v /var/lib/bluetooth:/var/lib/bluetooth:ro \
  -v /etc/avahi/services:/etc/avahi/services \
  meshmonitor-ble-bridge AA:BB:CC:DD:EE:FF --verbose
```

### Testing mDNS Autodiscovery

Once the bridge is running, you can verify it's advertising on the network:

```bash
# Browse for Meshtastic services
avahi-browse -rt _meshtastic._tcp

# You should see output like:
# +   eth0 IPv4 Meshtastic BLE Bridge (594c71)    _meshtastic._tcp     local
# =   eth0 IPv4 Meshtastic BLE Bridge (594c71)    _meshtastic._tcp     local
#    hostname = [hostname.local]
#    address = [192.168.1.100]
#    port = [4403]
#    txt = ["version=1.2" "ble_address=48:CA:43:59:4C:71" "port=4403" "bridge=ble"]
```

The mDNS service allows clients to automatically discover the bridge on the local network without needing to know the IP address.

## Development Status

**Status:** Proof of Concept / Beta

This is a functional proof-of-concept that demonstrates BLE-to-TCP bridging. It has been tested with real Meshtastic devices but may have edge cases.

**Known Limitations:**
- Single BLE device at a time
- No automatic reconnection yet
- Limited error recovery

**Implemented Features (v1.2):**
- ✅ mDNS/Avahi autodiscovery
- ✅ Graceful shutdown with proper cleanup
- ✅ Automatic service registration/deregistration
- ✅ BLE_ADDRESS environment variable support

**Future Enhancements:**
- Automatic BLE reconnection
- Multiple device support
- Better connection monitoring
- systemd service configuration
- Windows service wrapper

## Contributing

Contributions welcome! Particularly:
- Testing on different platforms
- Connection stability improvements
- Docker packaging
- Documentation

## License

BSD-3-Clause (same as MeshMonitor)
