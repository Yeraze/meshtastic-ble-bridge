# Claude Code Instructions for BLE Bridge

## Project Context

This is the MeshMonitor BLE Bridge - a Python/Docker application that bridges Bluetooth Low Energy Meshtastic devices to TCP for MeshMonitor compatibility.

## Key Instructions

- **Primary Reference:** Always consult `docs/CLAUDE_BLE_BRIDGE.md` first for technical context
- **Source Code:** Main application is `src/ble_tcp_bridge.py`
- **Docker Build:** Build context is `src/` directory
- **Testing:** Run locally with Python or via Docker container

## Critical Technical Details

### BLE Protocol
- Service UUID: `6ba1b218-15a8-461f-9fa8-5dcae273eafd`
- ToRadio (write): `f75c76d2-129e-4dad-a1dd-7866124401e7`
- FromRadio (read): `2c55e69e-4993-11ed-b878-0242ac120002`
- Uses raw protobuf bytes (no framing)

### TCP Protocol  
- Frame: `[0x94][0xC3][LENGTH_MSB][LENGTH_LSB][PROTOBUF]`
- Listen on `0.0.0.0:4403` (not localhost!)
- Big-endian 16-bit length field

### Critical Implementation Notes

1. **BLE Client Access:** Must get BleakClient from BLEInterface:
   ```python
   self.ble_client = self.ble_interface.client
   ```

2. **Writing to BLE:** Use direct GATT write:
   ```python
   await self.ble_client.write_gatt_char(self.TORADIO_UUID, packet_bytes)
   ```

3. **Docker Requirements:**
   - `privileged: true` - for BLE hardware access
   - `network_mode: host` - for localhost TCP
   - Volume: `/var/run/dbus:/var/run/dbus` - D-Bus access
   - Volume: `/var/lib/bluetooth:/var/lib/bluetooth:ro` - Pairing info

## Known Issues

- **Method names:** BLEInterface API varies, use BleakClient directly
- **Address format:** May need to strip colons from MAC address
- **Localhost binding:** Always use `0.0.0.0` not `localhost`
- **Pairing:** May need host-level pairing via bluetoothctl first

## Testing Workflow

```bash
# Local testing
python src/ble_tcp_bridge.py --scan
python src/ble_tcp_bridge.py AA:BB:CC:DD:EE:FF --verbose

# Docker testing
docker build -t meshmonitor-ble-bridge src/
docker run --rm --privileged --network host \
  -v /var/run/dbus:/var/run/dbus \
  meshmonitor-ble-bridge AA:BB:CC:DD:EE:FF --verbose
```

## Documentation Hierarchy

1. **Quick answers:** `docs/CLAUDE_BLE_BRIDGE.md`
2. **Deep dive:** `docs/BLE_TCP_BRIDGE_ANALYSIS.md`  
3. **User guide:** `docs/README_BLE_BRIDGE.md`
4. **Deployment:** `docs/DEPLOY_BLE_BRIDGE.md`

## Development Priorities

1. **Stability:** Connection reliability over features
2. **Logging:** Verbose debugging output essential
3. **Error handling:** Graceful failures with clear messages
4. **Documentation:** Keep docs updated with code changes

## When Stuck

- Check `docs/CLAUDE_BLE_BRIDGE.md` troubleshooting section
- Review BLE connection logs with `--verbose`
- Test BLE directly with `bluetoothctl`
- Verify TCP connectivity with `telnet <ip> 4403`
