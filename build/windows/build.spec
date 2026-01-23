# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for Windows GUI build
"""

block_cipher = None

# Analysis - determine what to include
a = Analysis(
    ['../../src/gui/main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('../../src/gui/resources/*.*', 'gui/resources'),
    ],
    hiddenimports=[
        'bleak',
        'meshtastic',
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
    console=False,  # No console window (GUI app)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon='../../src/gui/resources/icon.ico'  # Uncomment when icon exists
)
