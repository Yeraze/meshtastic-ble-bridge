"""Windows System Tray Application"""
import asyncio
import threading
import logging
import json
import sys
import os
from pathlib import Path
from typing import Optional
from PIL import Image, ImageDraw

# Handle pystray import
try:
    import pystray
    from pystray import MenuItem as item
except ImportError:
    print("Error: pystray not installed. Install with: pip install pystray")
    sys.exit(1)

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.bridge import MeshtasticBridge
from core.stats import BridgeStatistics
from gui.settings_dialog import SettingsDialog

logger = logging.getLogger(__name__)


def _show_messagebox_safe(title: str, message: str, msg_type: str = 'info', yes_no: bool = False):
    """
    Thread-safe messagebox display.
    Can be called from any thread - will run in a separate thread if needed.
    """
    import tkinter as tk
    from tkinter import messagebox

    def _show():
        # Create a temporary root window
        root = tk.Tk()
        root.withdraw()  # Hide the root window
        root.attributes('-topmost', True)  # Bring to front

        try:
            if yes_no:
                result = messagebox.askyesno(title, message, parent=root)
            elif msg_type == 'warning':
                messagebox.showwarning(title, message, parent=root)
                result = None
            elif msg_type == 'error':
                messagebox.showerror(title, message, parent=root)
                result = None
            else:  # info
                messagebox.showinfo(title, message, parent=root)
                result = None
        finally:
            # Properly destroy the root
            root.quit()
            root.destroy()

        return result

    # Check if we're in the main thread
    import threading
    if threading.current_thread() == threading.main_thread():
        # Safe to call directly from main thread
        return _show()
    else:
        # Run in a separate thread and wait for it
        result = [None]
        def _thread_wrapper():
            result[0] = _show()

        t = threading.Thread(target=_thread_wrapper, daemon=False)
        t.start()
        t.join()  # Wait for dialog to close
        return result[0]


class TrayApplication:
    """System tray application for Windows"""

    def __init__(self):
        self.bridge: Optional[MeshtasticBridge] = None
        self.icon: Optional[pystray.Icon] = None
        self.settings_dialog: Optional[SettingsDialog] = None

        # Configuration
        self.config = self._load_config()

        # Event loop for async operations
        self.loop = asyncio.new_event_loop()
        self.loop_thread: Optional[threading.Thread] = None

        # Statistics cache
        self.last_stats: Optional[BridgeStatistics] = None

        # Setup logging to file
        self._setup_logging()

    def _setup_logging(self):
        """Setup logging to file"""
        log_dir = Path.home() / ".meshtastic-bridge"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "bridge.log"

        # File handler with UTF-8 encoding to support emoji device names
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        )

        # Get root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(file_handler)

    def _load_config(self) -> dict:
        """Load configuration from file"""
        config_file = Path.home() / ".meshtastic-bridge" / "config.json"

        if config_file.exists():
            try:
                with open(config_file) as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load config: {e}")

        # Default config
        return {
            "ble_address": "",
            "tcp_port": 4403,
            "cache_enabled": True,
            "max_cache_nodes": 500,
            "autostart": False
        }

    def _save_config(self):
        """Save configuration to file"""
        config_file = Path.home() / ".meshtastic-bridge" / "config.json"
        config_file.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(config_file, 'w') as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save config: {e}")

    def _create_icon_image(self, connected: bool = False):
        """
        Create tray icon image.

        Args:
            connected: True for green (connected), False for gray (disconnected)
        """
        width = 64
        height = 64

        # Colors
        if connected:
            bg_color = (34, 139, 34)  # Green
            fg_color = (255, 255, 255)  # White
        else:
            bg_color = (128, 128, 128)  # Gray
            fg_color = (220, 220, 220)  # Light gray

        # Create image
        image = Image.new('RGB', (width, height), bg_color)
        draw = ImageDraw.Draw(image)

        # Draw radio waves icon
        center_x, center_y = width // 2, height // 2

        # Center dot
        dot_radius = 6
        draw.ellipse(
            [center_x - dot_radius, center_y - dot_radius,
             center_x + dot_radius, center_y + dot_radius],
            fill=fg_color
        )

        # Radio waves (arcs)
        for i in range(1, 4):
            radius = 10 + (i * 8)
            draw.arc(
                [center_x - radius, center_y - radius,
                 center_x + radius, center_y + radius],
                start=-45, end=45,
                fill=fg_color, width=3
            )

        return image

    def _create_menu(self):
        """Create system tray menu"""
        return pystray.Menu(
            item('Status', self._show_status, default=True),
            item('Settings', self._show_settings),
            pystray.Menu.SEPARATOR,
            item(
                'Disconnect' if self._is_connected() else 'Connect',
                self._toggle_connection
            ),
            pystray.Menu.SEPARATOR,
            item('View Logs', self._view_logs),
            pystray.Menu.SEPARATOR,
            item('Exit', self._quit_app)
        )

    def _is_connected(self) -> bool:
        """Check if bridge is connected"""
        return self.bridge and self.bridge.running and self.last_stats and self.last_stats.ble_connected

    def _show_status(self, icon=None, item=None):
        """Show status message box"""
        if not self.last_stats or not self.last_stats.ble_connected:
            message = "Bridge is not running\n\nConnect to a device from the tray menu"
        else:
            stats = self.last_stats
            uptime = stats.uptime() or 'N/A'

            message = f"""Connected: {stats.ble_address}
Uptime: {uptime}

Packets from BLE: {stats.packets_from_ble:,}
Packets to BLE: {stats.packets_to_ble:,}
Packets to TCP: {stats.packets_to_tcp:,}

TCP Clients: {stats.tcp_clients_count}
Peak Clients: {stats.tcp_clients_peak}

Cache: {'Enabled' if stats.cache_enabled else 'Disabled'}
Cache Size: {stats.cache_size}
Cache Hits: {stats.cache_hits}"""

        _show_messagebox_safe("Bridge Status", message)

    def _show_settings(self, icon=None, item=None):
        """Show settings dialog"""
        if self._is_connected():
            result = _show_messagebox_safe(
                "Bridge Running",
                "Bridge is currently connected.\n\n"
                "Settings changes require reconnection.\n"
                "Continue?",
                yes_no=True
            )

            if not result:
                return

        # Show settings dialog in a separate thread to avoid blocking pystray
        settings_thread = threading.Thread(
            target=self._show_settings_threaded,
            daemon=False
        )
        settings_thread.start()

    def _show_settings_threaded(self):
        """Show settings dialog in separate thread"""
        dialog = SettingsDialog(self.config, self._on_settings_save)
        dialog.show()

    def _on_settings_save(self, new_config: dict):
        """Handle settings save"""
        was_connected = self._is_connected()
        self.config = new_config
        self._save_config()

        logger.info("Settings saved")

        if was_connected:
            _show_messagebox_safe(
                "Settings Saved",
                "Settings saved successfully.\n\n"
                "Reconnecting to device..."
            )

            # Reconnect
            asyncio.run_coroutine_threadsafe(self._restart_bridge(), self.loop)

    def _toggle_connection(self, icon=None, item=None):
        """Connect or disconnect bridge"""
        if self._is_connected():
            asyncio.run_coroutine_threadsafe(self._stop_bridge(), self.loop)
        else:
            if not self.config.get('ble_address'):
                _show_messagebox_safe(
                    "Configuration Required",
                    "Please set BLE MAC address in Settings first",
                    msg_type='warning'
                )
                self._show_settings()
                return

            asyncio.run_coroutine_threadsafe(self._start_bridge(), self.loop)

    async def _start_bridge(self):
        """Start the bridge"""
        try:
            logger.info("Starting bridge...")

            self.bridge = MeshtasticBridge(
                ble_address=self.config['ble_address'],
                tcp_port=self.config.get('tcp_port', 4403),
                cache_enabled=self.config.get('cache_enabled', True),
                max_cache_nodes=self.config.get('max_cache_nodes', 500)
            )

            # Register stats callback
            self.bridge.register_stats_callback(self._on_stats_update)

            # Start bridge (but don't serve_forever yet - just initialize)
            await self.bridge.start()

            # Update icon
            if self.icon:
                self.icon.icon = self._create_icon_image(connected=True)
                self.icon.menu = self._create_menu()

            self._show_notification("Connected", f"Connected to {self.config['ble_address']}")

            logger.info("Bridge started successfully")

        except Exception as e:
            logger.error(f"Failed to start bridge: {e}", exc_info=True)

            _show_messagebox_safe(
                "Connection Failed",
                f"Failed to start bridge:\n\n{str(e)}\n\n"
                f"Check that device is paired and in range.",
                msg_type='error'
            )

    async def _stop_bridge(self):
        """Stop the bridge"""
        if self.bridge:
            logger.info("Stopping bridge...")
            await self.bridge.stop()
            self.bridge = None

        # Update icon
        if self.icon:
            self.icon.icon = self._create_icon_image(connected=False)
            self.icon.menu = self._create_menu()

        self._show_notification("Disconnected", "Bridge stopped")
        logger.info("Bridge stopped")

    async def _restart_bridge(self):
        """Restart the bridge"""
        await self._stop_bridge()
        await asyncio.sleep(1)
        await self._start_bridge()

    def _on_stats_update(self, stats: BridgeStatistics):
        """Handle statistics update from bridge"""
        self.last_stats = stats

    def _view_logs(self, icon=None, item=None):
        """Open log file"""
        import subprocess

        log_file = Path.home() / ".meshtastic-bridge" / "bridge.log"

        if log_file.exists():
            # Windows notepad
            subprocess.run(['notepad.exe', str(log_file)])
        else:
            _show_messagebox_safe("No Logs", "Log file not found")

    def _show_notification(self, title: str, message: str):
        """Show system tray notification"""
        if self.icon:
            self.icon.notify(message, title)

    def _quit_app(self, icon=None, item=None):
        """Quit application"""
        # Stop bridge if running
        if self._is_connected():
            future = asyncio.run_coroutine_threadsafe(self._stop_bridge(), self.loop)
            try:
                future.result(timeout=5)
            except Exception as e:
                logger.error(f"Error stopping bridge: {e}")

        # Stop event loop
        self.loop.call_soon_threadsafe(self.loop.stop)

        # Stop tray icon
        if self.icon:
            self.icon.stop()

    def _run_event_loop(self):
        """Run asyncio event loop in background thread"""
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run(self):
        """Run the application"""
        logger.info("Starting Meshtastic Bridge GUI")

        # Start event loop in background thread
        self.loop_thread = threading.Thread(target=self._run_event_loop, daemon=True)
        self.loop_thread.start()

        # Create tray icon
        icon_image = self._create_icon_image(connected=False)
        self.icon = pystray.Icon(
            "Meshtastic Bridge",
            icon_image,
            "Meshtastic BLE Bridge",
            menu=self._create_menu()
        )

        # Auto-connect if configured
        if self.config.get('ble_address') and self.config.get('autostart'):
            asyncio.run_coroutine_threadsafe(self._start_bridge(), self.loop)

        # Run tray icon (blocks until quit)
        logger.info("Tray icon running")
        self.icon.run()

        logger.info("Application exiting")
