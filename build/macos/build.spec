# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for macOS GUI build
"""
import os
import sys

block_cipher = None

# Get the absolute path to the spec file directory
spec_dir = os.path.dirname(os.path.abspath(SPEC))

# Analysis - determine what to include
a = Analysis(
    [os.path.join(spec_dir, '../../src/gui/macos_main.py')],
    pathex=[os.path.join(spec_dir, '../../src')],
    binaries=[],
    datas=[],
    hiddenimports=[
        # Core modules
        'core',
        'core.bridge',
        'core.stats',
        'core.ble_handler',
        'core.tcp_handler',
        'core.cache_manager',
        'core.protocol',
        # GUI modules
        'gui',
        'gui.macos_settings_dialog',
        'gui.macos_tray_app',
        # Third-party dependencies
        'bleak',
        'bleak.backends.corebluetooth',
        'meshtastic',
        'meshtastic.mesh_pb2',
        'meshtastic.protobuf',
        'pystray',
        'PIL',
        'PIL._tkinter_finder',
        # macOS-specific
        'objc',
        'Foundation',
        'AppKit',
        'asyncio',
        'tkinter',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib',
        'numpy',
        'scipy',
        'pandas',
        'pytest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Bundle everything into the PYZ
pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=block_cipher
)

# Create the executable
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MeshtasticBLEBridge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # No console window for GUI app
    disable_windowed_traceback=False,
    argv_emulation=True,  # Enable argv emulation for macOS
    target_arch=None,
    codesign_identity=None,
    entitlements_file=os.path.join(spec_dir, 'entitlements.plist'),
)

# Collect all files for the app bundle
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MeshtasticBLEBridge',
)

# Create the macOS app bundle
app = BUNDLE(
    coll,
    name='MeshtasticBLEBridge.app',
    icon=None,  # Add icon path when available: 'icon.icns'
    bundle_identifier='com.meshtastic.ble-bridge',
    info_plist={
        'CFBundleName': 'Meshtastic BLE Bridge',
        'CFBundleDisplayName': 'Meshtastic BLE Bridge',
        'CFBundleVersion': '2.0.0',
        'CFBundleShortVersionString': '2.0.0',
        'NSBluetoothAlwaysUsageDescription': 'Meshtastic BLE Bridge requires Bluetooth access to communicate with Meshtastic devices.',
        'NSBluetoothPeripheralUsageDescription': 'Meshtastic BLE Bridge requires Bluetooth access to communicate with Meshtastic devices.',
        'LSUIElement': True,  # Makes it a menu bar app (no dock icon)
        'LSMinimumSystemVersion': '10.13.0',
        'NSHighResolutionCapable': True,
        'NSRequiresAquaSystemAppearance': False,  # Support dark mode
    },
)
