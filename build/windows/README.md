# Windows Build Configuration

This directory contains the configuration and scripts for building the Windows executable.

## Prerequisites

- Windows 10 or 11
- Python 3.9, 3.10, 3.11, or 3.12
- Git (optional, for cloning repository)

## Building

### Option 1: Using PowerShell Script (Recommended)

```powershell
.\build.ps1
```

This will:
1. Check Python version
2. Install dependencies
3. Install PyInstaller
4. Build the executable

### Option 2: Manual Build

```powershell
# Install dependencies
cd ..\..\src
pip install -r requirements-gui.txt
pip install pyinstaller

# Build
cd ..\build\windows
pyinstaller build.spec --clean
```

## Output

The executable will be created at:
```
dist\MeshtasticBLEBridge.exe
```

Expected size: ~30-40 MB (single file, no installation required)

## Testing

Run the executable:
```powershell
.\dist\MeshtasticBLEBridge.exe
```

The application will:
1. Appear in the system tray (bottom-right corner)
2. Show gray icon (disconnected state)
3. Right-click the icon to access menu

## First-Time Setup

1. **Pair your Meshtastic device:**
   - Open Windows Settings → Bluetooth & devices
   - Click "Add device" → Bluetooth
   - Wait for your Meshtastic device
   - Click to pair (PIN: usually 123456 or no PIN)

2. **Configure bridge:**
   - Right-click tray icon → Settings
   - Enter BLE MAC address (e.g., 48:CA:43:59:4C:71)
   - Adjust other settings as needed
   - Click Save

3. **Connect:**
   - Right-click tray icon → Connect
   - Icon turns green when connected

4. **Use with MeshMonitor:**
   - Point MeshMonitor to `localhost:4403`
   - Or use the IP address of your PC

## Troubleshooting

### Build Fails

- Ensure Python 3.9+ is installed
- Try running PowerShell as Administrator
- Check that all dependencies installed successfully

### Executable Won't Run

- Check Windows Defender/antivirus (may flag unsigned .exe)
- Run from PowerShell to see error messages:
  ```powershell
  .\dist\MeshtasticBLEBridge.exe
  ```

### Can't Find Bluetooth Device

- Ensure Bluetooth is enabled in Windows
- Check Device Manager for Bluetooth adapter
- Try pairing device in Windows Settings first

### Connection Fails

- Verify device is paired in Windows Settings
- Check device is not connected to another app
- Try unpairing and re-pairing

## Logs

Application logs are stored at:
```
%USERPROFILE%\.meshtastic-bridge\bridge.log
```

View logs from tray menu: Right-click → View Logs

## Advanced

### Custom Icon

To use a custom icon:

1. Create `icon.ico` (64x64 pixels)
2. Place in `src/gui/resources/`
3. Uncomment the icon line in `build.spec`
4. Rebuild

### Debug Build

For console output during development:

Edit `build.spec` and change:
```python
console=False,  # Set to True for debug
```

Then rebuild.
