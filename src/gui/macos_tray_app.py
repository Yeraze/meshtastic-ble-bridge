"""macOS Menu Bar Application"""
import asyncio
import threading
import logging
import json
import sys
import os
import subprocess
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
from gui.macos_settings_dialog import SettingsDialog

logger = logging.getLogger(__name__)


def _show_messagebox_safe(title: str, message: str, msg_type: str = 'info', yes_no: bool = False):
    """
    Show a messagebox using subprocess (macOS workaround).
    Tkinter must run on main thread, but pystray owns it.
    """
    import subprocess
    import sys

    # Use AppleScript for native macOS dialogs - much simpler and reliable
    if yes_no:
        script = f'''
        tell application "System Events"
            activate
            display dialog "{message}" with title "{title}" buttons {{"No", "Yes"}} default button "Yes"
            set theButton to button returned of result
            if theButton is "Yes" then
                return "YES"
            else
                return "NO"
            end if
        end tell
        '''
    else:
        if msg_type == 'error':
            icon = 'stop'
        elif msg_type == 'warning':
            icon = 'caution'
        else:
            icon = 'note'

        script = f'''
        tell application "System Events"
            activate
            display dialog "{message}" with title "{title}" buttons {{"OK"}} default button "OK" with icon {icon}
        end tell
        '''

    try:
        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True,
            text=True,
            timeout=60
        )

        if yes_no:
            return 'YES' in result.stdout

        return None

    except Exception as e:
        logger.error(f"Failed to show dialog: {e}")
        return None


class TrayApplication:
    """Menu bar application for macOS"""

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

    def _create_icon_image(self, state: str = 'disconnected'):
        """
        Create menu bar icon image.

        Args:
            state: 'connected' (green), 'disconnected' (gray), or 'error' (red)
        """
        width = 64
        height = 64

        # Colors
        if state == 'connected':
            bg_color = (34, 139, 34)  # Green
            fg_color = (255, 255, 255)  # White
        elif state == 'error':
            bg_color = (200, 50, 50)  # Red
            fg_color = (255, 255, 255)  # White
        else:  # disconnected
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
        """Create menu bar menu"""
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
            item('Quit', self._quit_app)
        )

    def _is_connected(self) -> bool:
        """Check if bridge is connected"""
        return self.bridge and self.bridge.running and self.last_stats and self.last_stats.ble_connected

    def _show_status(self, icon=None, item=None):
        """Show status message box"""
        if not self.last_stats or not self.last_stats.ble_connected:
            message = "Bridge is not running\n\nConnect to a device from the menu bar"
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
        """Show settings dialog using native macOS dialogs"""
        if self._is_connected():
            result = _show_messagebox_safe(
                "Bridge Running",
                "Bridge is currently connected. Settings changes require reconnection. Continue?",
                yes_no=True
            )

            if not result:
                return

        # Run settings in a background thread to not block pystray
        settings_thread = threading.Thread(
            target=self._show_settings_applescript,
            daemon=True
        )
        settings_thread.start()

    def _scan_for_devices(self):
        """Scan for BLE devices and return list of (name, address) tuples"""
        try:
            from core.ble_handler import BLEHandler
            from core.stats import StatsCollector

            # Create event loop for scanning
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                stats = StatsCollector()
                ble = BLEHandler("", stats)
                devices = loop.run_until_complete(ble.scan_devices())
                return [(d.name or 'Unknown', d.address) for d in devices]
            finally:
                loop.close()

        except Exception as e:
            logger.error(f"Scan failed: {e}")
            return []

    def _show_settings_applescript(self):
        """Show settings using native AppleScript dialogs"""
        import subprocess

        current_address = self.config.get('ble_address', '')
        current_port = self.config.get('tcp_port', 4403)
        current_autostart = self.config.get('autostart', False)

        # First, ask if user wants to scan or enter manually
        choice_script = '''
        tell application "System Events"
            activate
            set userChoice to button returned of (display dialog "How would you like to select your device?" with title "Meshtastic Bridge Settings" buttons {"Cancel", "Enter Manually", "Scan for Devices"} default button "Scan for Devices")
            return userChoice
        end tell
        '''

        try:
            choice_result = subprocess.run(
                ['osascript', '-e', choice_script],
                capture_output=True,
                text=True,
                timeout=60
            )

            if choice_result.returncode != 0:
                return

            choice = choice_result.stdout.strip()

            if choice == "Scan for Devices":
                # Show scanning notification
                _show_messagebox_safe("Scanning", "Scanning for Meshtastic devices...\\nThis may take a few seconds.")

                # Perform scan
                devices = self._scan_for_devices()

                if not devices:
                    _show_messagebox_safe(
                        "No Devices Found",
                        "No Meshtastic devices found.\\n\\nMake sure your device is:\\n- Powered on\\n- Bluetooth enabled\\n- In range",
                        msg_type='warning'
                    )
                    return

                # Build AppleScript list of devices
                device_list = ', '.join([f'"{name} ({addr})"' for name, addr in devices])

                select_script = f'''
                tell application "System Events"
                    activate
                    set deviceList to {{{device_list}}}
                    set selectedDevice to choose from list deviceList with title "Select Device" with prompt "Found {len(devices)} Meshtastic device(s):" OK button name "Select" cancel button name "Cancel"
                    if selectedDevice is false then
                        return "CANCELLED"
                    else
                        return item 1 of selectedDevice
                    end if
                end tell
                '''

                select_result = subprocess.run(
                    ['osascript', '-e', select_script],
                    capture_output=True,
                    text=True,
                    timeout=120
                )

                if select_result.returncode != 0 or 'CANCELLED' in select_result.stdout:
                    return

                # Extract address from selection "Name (AA:BB:CC:DD:EE:FF)"
                selected = select_result.stdout.strip()
                # Find the MAC address in parentheses
                import re
                mac_match = re.search(r'\(([0-9A-Fa-f:]{17})\)', selected)
                if mac_match:
                    new_address = mac_match.group(1)
                else:
                    _show_messagebox_safe("Error", "Could not parse device address", msg_type='error')
                    return

            else:  # Enter Manually
                # AppleScript for BLE address input
                script = f'''
                tell application "System Events"
                    activate
                    set bleAddress to text returned of (display dialog "Enter BLE MAC Address:" & return & return & "Format: AA:BB:CC:DD:EE:FF" with title "Meshtastic Bridge Settings" default answer "{current_address}")
                    return bleAddress
                end tell
                '''

                result = subprocess.run(
                    ['osascript', '-e', script],
                    capture_output=True,
                    text=True,
                    timeout=120
                )

                if result.returncode != 0:
                    return

                new_address = result.stdout.strip()

            # Validate MAC address format
            import re
            mac_pattern = r'^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$'
            if not re.match(mac_pattern, new_address):
                _show_messagebox_safe(
                    "Invalid Address",
                    "Invalid MAC address format. Expected: AA:BB:CC:DD:EE:FF",
                    msg_type='error'
                )
                return

            # Ask for TCP port
            port_script = f'''
            tell application "System Events"
                activate
                set tcpPort to text returned of (display dialog "Enter TCP Port:" with title "Meshtastic Bridge Settings" default answer "{current_port}")
                return tcpPort
            end tell
            '''

            port_result = subprocess.run(
                ['osascript', '-e', port_script],
                capture_output=True,
                text=True,
                timeout=60
            )

            if port_result.returncode != 0:
                return

            try:
                new_port = int(port_result.stdout.strip())
                if new_port < 1 or new_port > 65535:
                    raise ValueError("Port out of range")
            except ValueError:
                _show_messagebox_safe(
                    "Invalid Port",
                    "TCP port must be a number between 1 and 65535",
                    msg_type='error'
                )
                return

            # Ask for autostart
            autostart_script = '''
            tell application "System Events"
                activate
                set autoStart to button returned of (display dialog "Auto-connect on startup?" with title "Meshtastic Bridge Settings" buttons {"No", "Yes"} default button "No")
                return autoStart
            end tell
            '''

            autostart_result = subprocess.run(
                ['osascript', '-e', autostart_script],
                capture_output=True,
                text=True,
                timeout=60
            )

            new_autostart = 'Yes' in autostart_result.stdout

            # Update config
            was_connected = self._is_connected()
            self.config['ble_address'] = new_address.upper()
            self.config['tcp_port'] = new_port
            self.config['autostart'] = new_autostart
            self._save_config()

            logger.info(f"Settings saved: {new_address}, port {new_port}, autostart={new_autostart}")

            _show_messagebox_safe(
                "Settings Saved",
                f"Settings saved successfully.\\n\\nBLE Address: {new_address}\\nTCP Port: {new_port}\\nAuto-connect: {'Yes' if new_autostart else 'No'}"
            )

            if was_connected:
                asyncio.run_coroutine_threadsafe(self._restart_bridge(), self.loop)

        except subprocess.TimeoutExpired:
            logger.warning("Settings dialog timed out")
        except Exception as e:
            logger.error(f"Failed to show settings dialog: {e}")

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

            # Register callbacks
            self.bridge.register_stats_callback(self._on_stats_update)
            self.bridge.register_failure_callback(self._on_reconnection_failed)

            # Start bridge (but don't serve_forever yet - just initialize)
            await self.bridge.start()

            # Update icon
            if self.icon:
                self.icon.icon = self._create_icon_image(state='connected')
                self.icon.menu = self._create_menu()

            self._show_notification("Connected", f"Connected to {self.config['ble_address']}")

            logger.info("Bridge started successfully")

        except Exception as e:
            logger.error(f"Failed to start bridge: {e}", exc_info=True)

            _show_messagebox_safe(
                "Connection Failed",
                f"Failed to start bridge:\n\n{str(e)}\n\n"
                f"Check that device is paired in System Settings > Bluetooth.",
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
            self.icon.icon = self._create_icon_image(state='disconnected')
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

    def _on_reconnection_failed(self):
        """Handle reconnection failure - all attempts exhausted"""
        logger.warning("All reconnection attempts failed, setting icon to error state")

        # Update icon to red
        if self.icon:
            self.icon.icon = self._create_icon_image(state='error')

        # Show notification
        self._show_notification(
            "Connection Failed",
            "Failed to reconnect after device reboot. Bridge stopped."
        )

    def _view_logs(self, icon=None, item=None):
        """Open log file in default text editor (TextEdit on macOS)"""
        log_file = Path.home() / ".meshtastic-bridge" / "bridge.log"

        if log_file.exists():
            # macOS: open with TextEdit or default text editor
            subprocess.run(['open', '-a', 'TextEdit', str(log_file)])
        else:
            _show_messagebox_safe("No Logs", "Log file not found")

    def _show_notification(self, title: str, message: str):
        """Show system notification"""
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

        # Stop menu bar icon
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

        # Create menu bar icon
        icon_image = self._create_icon_image(state='disconnected')
        self.icon = pystray.Icon(
            "Meshtastic Bridge",
            icon_image,
            "Meshtastic BLE Bridge",
            menu=self._create_menu()
        )

        # Auto-connect if configured
        if self.config.get('ble_address') and self.config.get('autostart'):
            asyncio.run_coroutine_threadsafe(self._start_bridge(), self.loop)

        # Run menu bar icon (blocks until quit)
        logger.info("Menu bar icon running")
        self.icon.run()

        logger.info("Application exiting")
