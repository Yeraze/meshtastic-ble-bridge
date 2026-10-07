# macOS GUI User Guide

The Meshtastic BLE Bridge is available as a native macOS application with a menu bar interface.

## Features

- **Menu Bar Application** - Runs in background, accessible from menu bar icon
- **Visual Status** - Green icon when connected, gray when disconnected, red on error
- **Easy Configuration** - GUI dialog for all settings
- **Device Scanning** - Find nearby Meshtastic devices
- **Real-time Stats** - View connection status, packet counts, uptime
- **Auto-connect** - Optional startup connection
- **Log Viewer** - Quick access to application logs

## Quick Start

### Download

1. Go to [Releases](https://github.com/Yeraze/meshtastic-ble-bridge/releases)
2. Download `MeshtasticBLEBridge-macOS-vX.X.X.dmg`
3. Open the DMG file
4. Drag `MeshtasticBLEBridge.app` to your Applications folder

### Pair Your Device (REQUIRED)

**IMPORTANT: You MUST pair your device in macOS first. The bridge will not work without pairing.**

1. Open **System Settings** (or System Preferences on older macOS)
2. Click **Bluetooth**
3. Turn on your Meshtastic device
4. Wait for it to appear in the list
5. Click **Connect** next to your device
6. Note the device address (e.g., `48:CA:43:59:4C:71`)

**Why pairing is required:** macOS BLE requires OS-level pairing for authenticated characteristic access. Without pairing, the bridge cannot read or write to the device.

### First Run

1. Launch `MeshtasticBLEBridge` from Applications
   - You may need to right-click and select "Open" the first time due to Gatekeeper
2. Look for the menu bar icon in the top-right corner (gray radio icon)
3. Click the icon → **Settings**
4. Enter your device's **BLE MAC Address**
5. Adjust other settings if needed
6. Click **Save**

### Bluetooth Permission

On first run, macOS will ask for Bluetooth permission:
- Click **Allow** when prompted
- If denied, go to **System Settings** → **Privacy & Security** → **Bluetooth** and enable for MeshtasticBLEBridge

### Connect

1. Click menu bar icon → **Connect**
2. Icon turns **green** when connected
3. A notification appears confirming connection

### Use with MeshMonitor

Point MeshMonitor to:
- **Host:** `localhost` (or your Mac's IP address)
- **Port:** `4403` (default)

## Menu Bar Options

Click the menu bar icon to access:

### Status (Default action)
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
Find nearby Meshtastic BLE devices (available in Settings dialog)

### View Logs
Open application log file in TextEdit

### Quit
Stop bridge and quit application

## Configuration

Settings are stored in:
```
~/.meshtastic-bridge/config.json
```

Logs are stored in:
```
~/.meshtastic-bridge/bridge.log
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

### Application Won't Open

**Gatekeeper blocking unsigned app:**
- Right-click the app → **Open** → Click **Open** in the dialog
- Or: System Settings → Privacy & Security → Click "Open Anyway"

**Missing Bluetooth permission:**
- System Settings → Privacy & Security → Bluetooth
- Enable MeshtasticBLEBridge

### No Bluetooth Adapter

1. Check Bluetooth is enabled in System Settings
2. Reset Bluetooth: Hold Shift+Option, click Bluetooth menu icon → Debug → Reset Bluetooth Module
3. Restart your Mac

### Can't Find Device

1. Ensure device is powered on
2. Check device is in range (BLE: ~10-30m)
3. Verify Bluetooth is enabled on Mac
4. Try **Scan** button in Settings

### Connection Fails

**Device not paired:**
- Pair in System Settings → Bluetooth first

**Device already connected:**
- Disconnect from other apps (e.g., Meshtastic Python CLI, mobile app)
- Turn Bluetooth off and on
- Restart device if needed

**Still failing:**
1. Forget device in System Settings → Bluetooth
2. Restart Bluetooth (Shift+Option+click Bluetooth icon → Debug → Reset)
3. Re-pair device
4. Try connecting again

### Bridge Connects but MeshMonitor Can't Connect

**Firewall blocking port 4403:**

Add firewall exception:
1. System Settings → Network → Firewall
2. Click **Options** or **Firewall Options**
3. Add MeshtasticBLEBridge and allow incoming connections

**Check bridge is listening:**
```bash
lsof -i :4403
```

Should show the bridge process.

### Stats Not Updating

- Click menu bar icon → Disconnect → Connect
- Check logs for errors (View Logs)

### High Memory Usage

Reduce cached nodes:
- Settings → Max Cached Nodes → 100-200
- Or disable caching entirely

### App Doesn't Appear in Menu Bar

- Check if the app is running: `ps aux | grep Meshtastic`
- Look for the icon - it may be hidden if you have many menu bar items
- Try using Bartender or similar to manage menu bar items

## Advanced Usage

### Running from Terminal

For debugging, run directly:

```bash
# Install dependencies
pip3 install -r src/requirements-macos.txt

# Run GUI
python3 -m src.gui.macos_main

# Or use CLI mode
python3 -m src.cli.main AA:BB:CC:DD:EE:FF --verbose
```

### Multiple Devices

Run separate instances for each device:
1. Create copies of the app with different names
2. Configure different ports (4403, 4404, etc.)

### Logs

View detailed logs:
```bash
cat ~/.meshtastic-bridge/bridge.log
```

Or from menu bar: Click icon → **View Logs**

### Building from Source

See `build/macos/README.md` for build instructions.

### Launch at Login

1. System Settings → General → Login Items
2. Click **+** under "Open at Login"
3. Navigate to Applications and select MeshtasticBLEBridge

## Code Signing & Notarization

The official release builds are:
- **Code signed** with a Developer ID certificate
- **Notarized** by Apple for Gatekeeper approval

If building from source without signing:
- You'll need to bypass Gatekeeper (right-click → Open)
- The app won't pass `spctl` verification

## Support

- **Issues:** https://github.com/Yeraze/meshtastic-ble-bridge/issues
- **Logs:** Always include logs when reporting issues
- **Platform:** Specify macOS version (e.g., 14.0 Sonoma)

## Comparison: macOS GUI vs. Windows GUI vs. Docker (Linux)

| Feature | macOS GUI | Windows GUI | Docker (Linux) |
|---------|-----------|-------------|----------------|
| Menu/Tray | Menu Bar | System Tray | N/A |
| GUI Settings | Yes | Yes | No (CLI args) |
| Visual Status | Yes | Yes | No |
| BLE Access | Native CoreBluetooth | Native WinRT | Via D-Bus |
| Install Method | DMG | Download .exe | Docker pull |
| Auto-start | Login Items | Registry | Docker compose |
| Platform | macOS only | Windows only | Linux only |
| Core Features | Identical | Identical | Identical |

All versions share the same core bridge logic and provide identical functionality.
