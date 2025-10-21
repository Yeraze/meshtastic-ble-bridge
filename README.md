# MeshMonitor BLE Bridge

A Docker-based bridge that connects Bluetooth Low Energy (BLE) Meshtastic devices to MeshMonitor via TCP.

## What's Included

```
meshmonitor-ble-bridge/
├── README.md                           # This file
├── QUICK_START.md                      # Get up and running fast
├── docker-compose.ble.yml              # Docker Compose configuration
├── src/
│   ├── ble_tcp_bridge.py              # Main bridge application
│   ├── Dockerfile                      # Container build instructions
│   └── .dockerignore                   # Docker build exclusions
└── docs/
    ├── CLAUDE_BLE_BRIDGE.md           # Claude Code context & technical details
    ├── BLE_TCP_BRIDGE_ANALYSIS.md     # Comprehensive technical analysis
    ├── README_BLE_BRIDGE.md           # User documentation
    └── DEPLOY_BLE_BRIDGE.md           # Deployment guide
```

## Quick Start

### Prerequisites
- Docker installed
- Bluetooth adapter (built-in or USB)
- Meshtastic device with BLE enabled

### 1. Build the Container
```bash
cd src
docker build -t meshmonitor-ble-bridge .
```

### 2. Find Your Device
```bash
docker run --rm --privileged --network host \
  -v /var/run/dbus:/var/run/dbus \
  meshmonitor-ble-bridge --scan
```

### 3. Pair Your Device (if required)
```bash
bluetoothctl
pair AA:BB:CC:DD:EE:FF  # Replace with your device MAC
trust AA:BB:CC:DD:EE:FF
exit
```

### 4. Start the Bridge
```bash
docker run -d --name ble-bridge \
  --privileged --network host \
  --restart unless-stopped \
  -v /var/run/dbus:/var/run/dbus \
  -v /var/lib/bluetooth:/var/lib/bluetooth:ro \
  meshmonitor-ble-bridge AA:BB:CC:DD:EE:FF
```

### 5. Connect MeshMonitor
Point MeshMonitor to:
- **IP:** `<bridge-host-ip>`
- **Port:** `4403`

## Documentation

- **Quick Start:** See `QUICK_START.md` for step-by-step setup
- **Deployment:** See `docs/DEPLOY_BLE_BRIDGE.md` for production deployment
- **User Guide:** See `docs/README_BLE_BRIDGE.md` for usage and troubleshooting
- **Technical Details:** See `docs/CLAUDE_BLE_BRIDGE.md` for architecture and development
- **Analysis:** See `docs/BLE_TCP_BRIDGE_ANALYSIS.md` for comprehensive protocol analysis

## Using with Claude Code

This package includes `docs/CLAUDE_BLE_BRIDGE.md` which provides complete context for working on the BLE bridge with Claude Code. Simply:

1. Extract this tarball on your target machine
2. Open the directory in Claude Code
3. Reference `docs/CLAUDE_BLE_BRIDGE.md` for full technical context

## Architecture

```
┌──────────────┐  TCP 4403         ┌───────────────┐
│ MeshMonitor  │ ←────────────────→│  BLE Bridge   │
└──────────────┘                    └───────┬───────┘
                                            │ BLE
                                    ┌───────▼───────┐
                                    │  Meshtastic   │
                                    └───────────────┘
```

The bridge translates between:
- **BLE:** Raw protobuf bytes on Meshtastic GATT characteristics
- **TCP:** Framed protocol `[0x94][0xC3][LEN][PROTOBUF]`

## Docker Compose Integration

For MeshMonitor users, use the included `docker-compose.ble.yml` as an overlay:

```bash
# Copy to MeshMonitor directory
cp docker-compose.ble.yml /path/to/meshmonitor/

# Create .env file
echo "BLE_ADDRESS=AA:BB:CC:DD:EE:FF" > /path/to/meshmonitor/.env

# Start both services
cd /path/to/meshmonitor
docker compose -f docker-compose.yml -f docker-compose.ble.yml up -d
```

## Common Issues

### "No BLE adapter found"
```bash
sudo systemctl start bluetooth
```

### "Permission denied"
Container needs `--privileged` flag for BLE access

### "Device not found"
- Ensure device BLE is enabled
- Move closer (BLE range ~10-30m)
- Check device not connected to another app

### "Connection refused" from MeshMonitor
- Verify bridge listening on `0.0.0.0:4403`
- Check firewall allows port 4403
- Test with: `telnet <bridge-ip> 4403`

## Support & Development

For issues, questions, or contributions:
- MeshMonitor: https://github.com/Yeraze/meshmonitor
- Meshtastic: https://meshtastic.org

## License

BSD-3-Clause (same as MeshMonitor)
