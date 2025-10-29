# Deploy BLE Bridge Container

This guide shows how to deploy the MeshMonitor BLE-to-TCP bridge using the prebuilt Docker image.

## Requirements

- Docker installed
- Bluetooth adapter (built-in or USB)
- Meshtastic device with BLE enabled

## Installation

### 1. Pull the Docker Image

```bash
docker pull ghcr.io/yeraze/meshtastic-ble-bridge:latest
```

You should see:
```
latest: Pulling from yeraze/meshtastic-ble-bridge
...
Status: Downloaded newer image for ghcr.io/yeraze/meshtastic-ble-bridge:latest
```

### 2. Find Your Meshtastic Device

```bash
docker run --rm --privileged \
  -v /var/run/dbus:/var/run/dbus \
  -v /var/lib/bluetooth:/var/lib/bluetooth:ro \
  ghcr.io/yeraze/meshtastic-ble-bridge:latest --scan
```

Output will show devices like:
```
Scanning for Meshtastic devices...
  Found: Meshtastic_1a2b (AA:BB:CC:DD:EE:FF)
```

### 2a. Pair Device (If Required)

If your Meshtastic device requires pairing/PIN, pair it on the host first:

```bash
# Start bluetoothctl
bluetoothctl

# In bluetoothctl:
power on
agent on
default-agent
scan on

# Wait for your device to appear, note the MAC address
# Then pair (replace AA:BB:CC:DD:EE:FF with your device's MAC):
pair AA:BB:CC:DD:EE:FF

# Enter PIN when prompted (default is often 123456)
# After successful pairing:
trust AA:BB:CC:DD:EE:FF
exit
```

Once paired, the container will reuse this pairing information via D-Bus.

### 3. Start the Bridge

```bash
docker run -d --name ble-bridge \
  --privileged \
  -p 4403:4403 \
  --restart unless-stopped \
  -v /var/run/dbus:/var/run/dbus \
  -v /var/lib/bluetooth:/var/lib/bluetooth:ro \
  -v /etc/avahi/services:/etc/avahi/services \
  ghcr.io/yeraze/meshtastic-ble-bridge:latest AA:BB:CC:DD:EE:FF
```

Replace `AA:BB:CC:DD:EE:FF` with your device's MAC address from step 2.

**Volume Mounts Explained:**
- `/var/run/dbus` - Required for Bluetooth D-Bus communication
- `/var/lib/bluetooth` - Pairing information (read-only)
- `/etc/avahi/services` - mDNS service registration for autodiscovery

**Port Exposure:**
- `-p 4403:4403` - Exposes the TCP bridge port to the host

### 4. Verify It's Running

```bash
# Check container status
docker ps | grep ble-bridge

# View logs
docker logs -f ble-bridge

# Check TCP port is listening
netstat -tln | grep 4403
```

You should see output like:
```
INFO:root:Connected to Meshtastic device AA:BB:CC:DD:EE:FF
INFO:root:✅ mDNS service registered: Meshtastic BLE Bridge (aabbcc)
INFO:root:   Service type: _meshtastic._tcp.local.
INFO:root:   Port: 4403
INFO:root:TCP server listening on 0.0.0.0:4403
```

### 4a. Test mDNS Autodiscovery (Optional)

Verify the bridge is discoverable on your network:

```bash
# Browse for Meshtastic services
avahi-browse -rt _meshtastic._tcp

# Or check the service file was created
ls -l /etc/avahi/services/meshtastic-ble-bridge-*.service
cat /etc/avahi/services/meshtastic-ble-bridge-*.service
```

The bridge automatically registers itself with Avahi, allowing clients to discover it without knowing the IP address.

### 5. Configure MeshMonitor

Point your MeshMonitor instance to the bridge:

```bash
# If on same machine
MESHTASTIC_NODE_IP=localhost
MESHTASTIC_NODE_PORT=4403

# If on different machine
MESHTASTIC_NODE_IP=<bridge-machine-ip>
MESHTASTIC_NODE_PORT=4403
```

## Troubleshooting

### "No BLE adapter found"

```bash
# Check Bluetooth status
sudo systemctl status bluetooth

# Start Bluetooth if needed
sudo systemctl start bluetooth
```

### "Permission denied" on Linux

The container needs privileged mode for BLE access. Make sure you're using `--privileged` flag.

### Pairing Issues

**Common Meshtastic PINs:**
- `123456` (most common default)
- `000000`
- Check your device's screen if it has one
- Check Meshtastic app settings

**If pairing fails:**
```bash
# Remove old pairing and try again
bluetoothctl
remove AA:BB:CC:DD:EE:FF
scan on
pair AA:BB:CC:DD:EE:FF
```

**Check if already paired:**
```bash
bluetoothctl paired-devices
```

### Connection Issues

```bash
# Stop the bridge
docker stop ble-bridge
docker rm ble-bridge

# Restart Bluetooth
sudo systemctl restart bluetooth

# Try again
docker run -d --name ble-bridge \
  --privileged \
  -p 4403:4403 \
  --restart unless-stopped \
  -v /var/run/dbus:/var/run/dbus \
  -v /var/lib/bluetooth:/var/lib/bluetooth:ro \
  -v /etc/avahi/services:/etc/avahi/services \
  ghcr.io/yeraze/meshtastic-ble-bridge:latest AA:BB:CC:DD:EE:FF --verbose
```

### View Detailed Logs

```bash
docker logs -f ble-bridge
```

## Stopping the Bridge

```bash
docker stop ble-bridge
docker rm ble-bridge
```

## Custom TCP Port

If port 4403 is already in use on the host:

```bash
docker run -d --name ble-bridge \
  --privileged \
  -p 14403:4403 \
  --restart unless-stopped \
  -v /var/run/dbus:/var/run/dbus \
  -v /var/lib/bluetooth:/var/lib/bluetooth:ro \
  -v /etc/avahi/services:/etc/avahi/services \
  ghcr.io/yeraze/meshtastic-ble-bridge:latest AA:BB:CC:DD:EE:FF
```

This maps host port 14403 to container port 4403. Then configure MeshMonitor with `MESHTASTIC_NODE_PORT=14403`

**Note:** The mDNS service will automatically advertise the custom port in its TXT records.

## Support

For issues, see:
- tools/README_BLE_BRIDGE.md (in the MeshMonitor repository)
- https://github.com/Yeraze/meshmonitor/issues
