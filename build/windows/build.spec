# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for Windows GUI build
"""

block_cipher = None

# Analysis - determine what to include
a = Analysis(
    ['../../src/gui/main.py'],
    pathex=['../../src'],
    binaries=[],
    datas=[
        ('../../src/gui/resources/*.*', 'gui/resources'),
    ],
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
        'gui.settings_dialog',
        'gui.tray_app',
        # Third-party dependencies
        'bleak',
        'bleak.backends.winrt',
        'meshtastic',
        'meshtastic.mesh_pb2',
        'meshtastic.protobuf',
        'pystray',
        'PIL',
        'PIL._tkinter_finder',
        'win32api',
        'win32con',
        'win32gui',
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
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='MeshtasticBLEBridge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # Show console for debugging
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon='../../src/gui/resources/icon.ico'  # Uncomment when icon exists
)
