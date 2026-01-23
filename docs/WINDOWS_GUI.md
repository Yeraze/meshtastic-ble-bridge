# Windows GUI User Guide

The Meshtastic BLE Bridge is now available as a native Windows application with a system tray interface.

## Features

✅ **System Tray Application** - Runs in background, accessible from tray icon
✅ **Visual Status** - Green icon when connected, gray when disconnected
✅ **Easy Configuration** - GUI dialog for all settings
✅ **Device Scanning** - Find nearby Meshtastic devices
✅ **Real-time Stats** - View connection status, packet counts, uptime
✅ **Auto-connect** - Optional startup connection
✅ **Log Viewer** - Quick access to application logs

## Quick Start

### Download

1. Go to [Releases](https://github.com/Yeraze/meshtastic-ble-bridge/releases)
2. Download `MeshtasticBLEBridge-Windows-vX.X.X.zip`
3. Extract `MeshtasticBLEBridge.exe` to a folder of your choice

### Pair Your Device (REQUIRED)

**⚠️ IMPORTANT: You MUST pair your device in Windows first. The bridge will not work without pairing.**

1. Open **Settings** → **Bluetooth & devices**
2. Click **Add device** → **Bluetooth**
3. Wait for your Meshtastic device to appear
4. Click to pair
   - PIN: Usually `123456` or no PIN required
5. Note the MAC address (e.g., `48:CA:43:59:4C:71`)

**Why pairing is required:** Windows BLE requires OS-level pairing for authenticated characteristic access. Without pairing, the bridge cannot read or write to the device.

### First Run

1. Run `MeshtasticBLEBridge.exe`
2. Look for the tray icon in the bottom-right corner (gray radio icon)
3. Right-click the icon → **Settings**
4. Enter your device's **BLE MAC Address**
5. Adjust other settings if needed
6. Click **Save**

### Connect

1. Right-click tray icon → **Connect**
2. Icon turns **green** when connected
3. Status notification appears

### Use with MeshMonitor

Point MeshMonitor to:
- **Host:** `localhost` (or your PC's IP address)
- **Port:** `4403` (default)

## Tray Menu

Right-click the tray icon to access:

### Status (Double-click)
Shows connection information:
- Connected device address
- Uptime
- Packet statistics
- TCP client count
- Cache status

### Settings
Configure:
- **BLE MAC Address** - Your device's address
- **TCP Port** - Port for clients (default: 4403)
- **Config Caching** - Enable for faster reconnections
- **Max Cached Nodes** - Memory limit (default: 500)
- **Auto-connect** - Connect on startup

### Connect / Disconnect
Toggle bridge connection

### Scan for Devices
Find nearby Meshtastic BLE devices

### View Logs
Open application log file in Notepad

### Exit
Stop bridge and quit application

## Configuration

Settings are stored in:
```
%USERPROFILE%\.meshtastic-bridge\config.json
```

Logs are stored in:
```
%USERPROFILE%\.meshtastic-bridge\bridge.log
```

### Config Caching

When enabled:
- **Faster reconnections** - ~100ms vs 30s+ without cache
- **Cached data** - Node database, radio settings, channels
- **Auto-updates** - Position, telemetry, user info updated in real-time

**Limitations:**
- Device reconfiguration via app may not work properly
- Best for read-only usage (messaging, monitoring)
- Restart bridge after device reconfiguration

## Troubleshooting

### Application Won't Start

**Check Windows Defender:**
- Windows may block unsigned executables
- Right-click .exe → Properties → Check "Unblock"
- Or add exception in Windows Defender

**Run from PowerShell to see errors:**
```powershell
.\MeshtasticBLEBridge.exe
```

### No Bluetooth Adapter Found

1. Open **Device Manager**
2. Check for Bluetooth adapter
3. Update drivers if needed
4. Enable Bluetooth in **Settings**

### Can't Find Device

1. Ensure device is powered on
2. Check device is in range
3. Verify Bluetooth is enabled
4. Try **Scan for Devices** from tray menu

### Connection Fails

**Device not paired:**
- Pair in Windows Settings first

**Device already connected:**
- Disconnect from other apps (e.g., Meshtastic Python CLI)
- Restart device if needed

**Still failing:**
1. Unpair device in Windows Settings
2. Restart Bluetooth:
   ```powershell
   Restart-Service bthserv
   ```
3. Re-pair device
4. Try connecting again

### Bridge Connects but MeshMonitor Can't Connect

**Firewall blocking port 4403:**

Add firewall rule:
```powershell
New-NetFirewallRule -DisplayName "Meshtastic Bridge" -Direction Inbound -Protocol TCP -LocalPort 4403 -Action Allow
```

**Check bridge is listening:**
```powershell
netstat -an | findstr 4403
```

Should show: `0.0.0.0:4403` or `127.0.0.1:4403`

### Stats Not Updating

- Right-click → Disconnect → Connect
- Check logs for errors

### High Memory Usage

Reduce cached nodes:
- Settings → Max Cached Nodes → 100-200
- Or disable caching entirely

## Advanced Usage

### Command Line Mode

For automation or debugging, use Python directly:

```powershell
# Install dependencies
pip install -r src\requirements-gui.txt

# Run GUI
python -m src.gui.main

# Or use CLI mode
python -m src.cli.main AA:BB:CC:DD:EE:FF --verbose
```

### Multiple Devices

Run separate instances for each device:
1. Create separate folders
2. Copy .exe to each folder
3. Configure different ports (4403, 4404, etc.)

### Logs

View detailed logs:
```powershell
notepad %USERPROFILE%\.meshtastic-bridge\bridge.log
```

Or from tray menu: **View Logs**

### Building from Source

See `build/windows/README.md` for build instructions.

## Support

- **Issues:** https://github.com/Yeraze/meshtastic-ble-bridge/issues
- **Logs:** Always include logs when reporting issues
- **Platform:** Specify Windows version (10/11)

## Comparison: Windows GUI vs. Docker (Linux)

| Feature | Windows GUI | Docker (Linux) |
|---------|-------------|----------------|
| System Tray | ✅ Yes | ❌ No |
| GUI Settings | ✅ Yes | ❌ No (CLI args) |
| Visual Status | ✅ Yes | ❌ No |
| BLE Access | ✅ Native | ✅ Via D-Bus |
| Install Method | Download .exe | Docker pull |
| Auto-start | ✅ Optional | ✅ Via compose |
| Platform | Windows only | Linux only |
| Core Features | Identical | Identical |

Both versions share the same core bridge logic and provide identical functionality.
