# MeshMonitor BLE Bridge - Claude Code Context

## Project Overview

The MeshMonitor BLE Bridge is a Python application that bridges Bluetooth Low Energy (BLE) Meshtastic devices to TCP, allowing MeshMonitor to connect to BLE-only devices.

## Architecture

```
┌──────────────┐  TCP (port 4403)      ┌───────────────┐
│ MeshMonitor  │ ←──────────────────→ │  BLE Bridge   │
└──────────────┘                        └───────┬───────┘
                                                │ BLE
                                        ┌───────▼───────┐
                                        │  Meshtastic   │
                                        │    Device     │
                                        └───────────────┘
```

### Protocol Translation

**BLE Side:**
- Service UUID: `6ba1b218-15a8-461f-9fa8-5dcae273eafd`
- ToRadio (write): `f75c76d2-129e-4dad-a1dd-7866124401e7`
- FromRadio (read/notify): `2c55e69e-4993-11ed-b878-0242ac120002`
- Raw protobuf bytes (no framing)

**TCP Side:**
- Frame: `[0x94][0xC3][LENGTH_MSB][LENGTH_LSB][PROTOBUF]`
- 4-byte header + protobuf payload
- Listens on `0.0.0.0:4403` (all interfaces)

## Key Components

### 1. ble_tcp_bridge.py
Main bridge application with:
- `MeshtasticBLEBridge` class handling both BLE and TCP
- Async TCP server accepting MeshMonitor connections
- Direct BleakClient writes to BLE characteristics
- Bidirectional packet forwarding

### 2. Dockerfile
Python 3.11-slim based container with:
- bluetooth/bluez system packages
- meshtastic==2.3.12 and bleak==0.21.1
- Exposes port 4403

### 3. docker-compose.ble.yml
Optional compose overlay requiring:
- `privileged: true` for BLE hardware access
- `network_mode: host` for localhost TCP
- Volume mounts:
  - `/var/run/dbus:/var/run/dbus` - D-Bus/Bluetooth daemon
  - `/var/lib/bluetooth:/var/lib/bluetooth:ro` - Pairing info

## Critical Implementation Details

### BLE Connection
```python
# BLEInterface requires address without colons for some versions
address_no_colons = ble_address.replace(':', '').replace('-', '')

# Create interface
self.ble_interface = BLEInterface(
    address=address_no_colons,
    noProto=False
)

# Store direct BleakClient reference for writing
self.ble_client = self.ble_interface.client
```

### Writing to BLE
```python
# Serialize protobuf to bytes
packet_bytes = packet.SerializeToString()

# Write directly to ToRadio characteristic
await self.ble_client.write_gatt_char(self.TORADIO_UUID, packet_bytes)
```

### TCP Framing
```python
# Create 4-byte header: [0x94][0xC3][LENGTH_MSB][LENGTH_LSB]
header = struct.pack('>BBH', START1, START2, length)
frame = header + protobuf_bytes
```

### Reading BLE Packets
```python
# Packets arrive via BLEInterface callback
def on_receive(packet, interface):
    asyncio.create_task(self.on_ble_packet(packet))

# Forward to TCP clients
async def on_ble_packet(self, packet):
    protobuf_bytes = packet.SerializeToString()
    tcp_frame = self.create_tcp_frame(protobuf_bytes)
    await self.broadcast_to_tcp(tcp_frame)
```

## Known Issues & Solutions

### Issue 1: Port Already in Use
**Problem:** Container can't bind to port 4403
**Solution:** Check for other containers/processes on port 4403

### Issue 2: BLE Pairing Required
**Problem:** Device requires PIN
**Solution:** Pair on host first with `bluetoothctl`:
```bash
bluetoothctl
pair AA:BB:CC:DD:EE:FF
trust AA:BB:CC:DD:EE:FF
```

### Issue 3: Method Name Confusion
**Problem:** BLEInterface API changed between versions
**Solution:** Use direct BleakClient access instead of interface methods

### Issue 4: Localhost-only Binding
**Problem:** Bridge only accepts connections from same machine
**Solution:** Listen on `0.0.0.0` instead of `localhost`

### Issue 5: D-Bus Access
**Problem:** Container can't see Bluetooth adapter
**Solution:** Mount `/var/run/dbus` volume

## Development Workflow

### Testing Locally
```bash
# Scan for devices
python ble_tcp_bridge.py --scan

# Start bridge
python ble_tcp_bridge.py AA:BB:CC:DD:EE:FF --verbose
```

### Building Container
```bash
docker build -t meshmonitor-ble-bridge ./tools
```

### Exporting for Transfer
```bash
docker save meshmonitor-ble-bridge -o meshmonitor-ble-bridge.tar
```

### Running Container
```bash
docker run -d --name ble-bridge \
  --privileged --network host \
  --restart unless-stopped \
  -v /var/run/dbus:/var/run/dbus \
  -v /var/lib/bluetooth:/var/lib/bluetooth:ro \
  meshmonitor-ble-bridge AA:BB:CC:DD:EE:FF
```

## Testing with MeshMonitor

### Environment Variables
```bash
MESHTASTIC_NODE_IP=<bridge-host-ip>
MESHTASTIC_NODE_PORT=4403
```

### Expected Behavior
1. Bridge starts and connects to BLE device
2. TCP server listens on 0.0.0.0:4403
3. MeshMonitor connects via TCP
4. Bidirectional packet flow:
   - BLE → TCP: FromRadio protobufs framed for TCP
   - TCP → BLE: ToRadio protobufs written to characteristic

### Debugging
```bash
# View bridge logs
docker logs -f ble-bridge

# Check TCP connectivity
netstat -tln | grep 4403
telnet <bridge-host> 4403

# Check BLE connection
bluetoothctl info AA:BB:CC:DD:EE:FF
```

## Performance Considerations

- **MTU**: BLE MTU typically 255 bytes, may fragment larger packets
- **Latency**: BLE adds 20-100ms latency vs direct TCP
- **Range**: BLE limited to ~10-30m line of sight
- **Reconnection**: Currently manual; future enhancement needed

## Future Enhancements

1. **Automatic Reconnection**
   - Detect BLE disconnects
   - Auto-retry with exponential backoff
   - Health monitoring

2. **Multiple Devices**
   - Support bridging multiple BLE devices
   - Different TCP ports per device
   - Device discovery/selection

3. **Statistics/Monitoring**
   - Packet counts, errors, latency
   - Prometheus metrics
   - Health check endpoints

4. **Configuration File**
   - YAML/JSON config instead of CLI args
   - Persistent settings
   - Multiple device profiles

## Troubleshooting Guide

### No BLE Adapter Found
```bash
sudo systemctl status bluetooth
sudo systemctl start bluetooth
hciconfig  # Check adapter status
```

### Permission Denied
```bash
# Grant BLE permissions to Python
sudo setcap 'cap_net_raw,cap_net_admin+eip' $(which python3)

# Or use privileged container (recommended)
docker run --privileged ...
```

### Device Not Found
- Ensure BLE enabled on Meshtastic device
- Check device is in range (< 10m)
- Verify device not connected to another app
- Try scanning: `docker run ... --scan`

### Connection Timeout
- Device may be paired to another system
- Try unpairing and re-pairing
- Reset device Bluetooth
- Update device firmware

### MeshMonitor Can't Connect
- Check bridge listening on 0.0.0.0 not localhost
- Verify firewall allows port 4403
- Test with: `telnet <bridge-ip> 4403`
- Check docker logs for errors

## Reference Material

### Meshtastic Protocol
- Official docs: https://meshtastic.org/docs/developers/device/ble-api
- Protobufs: https://github.com/meshtastic/protobufs/

### BLE Libraries
- Bleak: https://github.com/hbldh/bleak
- Meshtastic Python: https://github.com/meshtastic/python

### MeshMonitor
- TCP protocol defined in: src/utils/meshtasticClient.ts
- Framing constants: START1=0x94, START2=0xC3
