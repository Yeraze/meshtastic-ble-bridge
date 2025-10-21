# Quick Start Guide

Get your BLE bridge running in 5 minutes!

## Step 1: Prerequisites Check

```bash
# Check Docker is installed
docker --version

# Check Bluetooth is running
sudo systemctl status bluetooth

# If not running, start it
sudo systemctl start bluetooth
```

## Step 2: Build the Container

```bash
cd src
docker build -t meshmonitor-ble-bridge .
```

Expected output:
```
Successfully built <image-id>
Successfully tagged meshmonitor-ble-bridge:latest
```

## Step 3: Scan for Your Meshtastic Device

```bash
docker run --rm --privileged --network host \
  -v /var/run/dbus:/var/run/dbus \
  meshmonitor-ble-bridge --scan
```

Example output:
```
Scanning for Meshtastic devices...
  Found: Meshtastic_b431 (DA:AB:E8:9C:B4:31)
  Found: Yble_4c70 (48:CA:43:59:4C:71)
```

**Copy the MAC address** of your device!

## Step 4: Pair Device (If Required)

Some devices require pairing. If so:

```bash
bluetoothctl
```

In bluetoothctl:
```
power on
agent on
default-agent
scan on
# Wait for your device to appear
pair AA:BB:CC:DD:EE:FF    # Use your MAC address
# Enter PIN when prompted (often 123456)
trust AA:BB:CC:DD:EE:FF
exit
```

## Step 5: Start the Bridge

Replace `AA:BB:CC:DD:EE:FF` with your device's MAC address:

```bash
docker run -d --name ble-bridge \
  --privileged --network host \
  --restart unless-stopped \
  -v /var/run/dbus:/var/run/dbus \
  -v /var/lib/bluetooth:/var/lib/bluetooth:ro \
  meshmonitor-ble-bridge AA:BB:CC:DD:EE:FF
```

## Step 6: Verify It's Running

```bash
# Check container status
docker ps | grep ble-bridge

# View logs
docker logs -f ble-bridge
```

You should see:
```
✅ Connected to BLE device
✅ TCP server listening on 0.0.0.0:4403
```

## Step 7: Configure MeshMonitor

### If MeshMonitor is on the Same Machine:
```bash
MESHTASTIC_NODE_IP=localhost
MESHTASTIC_NODE_PORT=4403
```

### If MeshMonitor is on a Different Machine:
```bash
MESHTASTIC_NODE_IP=<bridge-machine-ip>
MESHTASTIC_NODE_PORT=4403
```

## Step 8: Test the Connection

```bash
# From the MeshMonitor machine
telnet <bridge-ip> 4403
```

If it connects, you're ready! Press Ctrl+] then type `quit` to exit telnet.

## Troubleshooting

### Container won't start?
```bash
# Check logs
docker logs ble-bridge

# Common issues:
# - BLE adapter not found: sudo systemctl start bluetooth
# - Permission denied: Ensure --privileged flag is used
# - Device not found: Check device is in range and BLE enabled
```

### Can't connect from MeshMonitor?
```bash
# Check bridge is listening
docker exec ble-bridge netstat -tln | grep 4403

# Should show: tcp 0 0 0.0.0.0:4403 0.0.0.0:* LISTEN

# Check firewall
sudo ufw status
sudo ufw allow 4403/tcp  # If using ufw
```

### Device keeps disconnecting?
```bash
# Restart the bridge with verbose logging
docker stop ble-bridge
docker rm ble-bridge

docker run -d --name ble-bridge \
  --privileged --network host \
  --restart unless-stopped \
  -v /var/run/dbus:/var/run/dbus \
  -v /var/lib/bluetooth:/var/lib/bluetooth:ro \
  meshmonitor-ble-bridge AA:BB:CC:DD:EE:FF --verbose

# Watch logs
docker logs -f ble-bridge
```

## Next Steps

- See `docs/README_BLE_BRIDGE.md` for detailed usage
- See `docs/DEPLOY_BLE_BRIDGE.md` for production deployment
- See `docs/CLAUDE_BLE_BRIDGE.md` for development/technical details

## Stopping the Bridge

```bash
docker stop ble-bridge
docker rm ble-bridge
```

## Need Help?

Check the full documentation in the `docs/` directory or visit:
- MeshMonitor: https://github.com/Yeraze/meshmonitor
- Meshtastic: https://meshtastic.org
