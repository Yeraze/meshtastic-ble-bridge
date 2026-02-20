"""Settings dialog for macOS GUI"""
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Callable, Dict, List
import re
import asyncio
import threading


def _show_error(title: str, message: str):
    """Thread-safe error messagebox"""
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    root.lift()
    root.focus_force()
    try:
        messagebox.showerror(title, message, parent=root)
    finally:
        # Don't call quit() since we're not running mainloop
        root.destroy()


class SettingsDialog:
    """Configuration dialog for bridge settings"""

    def __init__(self, config: Dict, on_save: Callable):
        """
        Initialize settings dialog.

        Args:
            config: Current configuration dictionary
            on_save: Callback when settings are saved (receives updated config)
        """
        self.config = config.copy()
        self.on_save = on_save
        self.scan_results: List = []  # List of (name, address) tuples
        self.scanning = False

        self.root = tk.Tk()
        self.root.title("Meshtastic Bridge Settings")
        self.root.geometry("600x720")
        self.root.resizable(False, False)

        # Bring to front
        self.root.attributes('-topmost', True)
        self.root.after(100, lambda: self.root.attributes('-topmost', False))

        self._create_widgets()

        # Center window
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() // 2) - (self.root.winfo_width() // 2)
        y = (self.root.winfo_screenheight() // 2) - (self.root.winfo_height() // 2)
        self.root.geometry(f"+{x}+{y}")

    def _create_widgets(self):
        """Create dialog widgets"""
        # Main frame with padding
        main_frame = ttk.Frame(self.root, padding="15")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        row = 0

        # Title
        title_label = ttk.Label(
            main_frame,
            text="Bridge Configuration",
            font=('Arial', 12, 'bold')
        )
        title_label.grid(row=row, column=0, columnspan=2, pady=(0, 5), sticky=tk.W)
        row += 1

        # Pairing reminder frame
        pairing_frame = ttk.Frame(main_frame, relief='solid', borderwidth=1, padding="8")
        pairing_frame.grid(row=row, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 15))

        ttk.Label(
            pairing_frame,
            text="IMPORTANT: Device must be paired in macOS first!",
            font=('Arial', 9, 'bold'),
            foreground='#d97706'
        ).pack(anchor=tk.W)

        ttk.Label(
            pairing_frame,
            text="System Settings \u2192 Bluetooth \u2192 Connect your Meshtastic device",
            font=('Arial', 8),
            foreground='#666'
        ).pack(anchor=tk.W, pady=(2, 0))

        row += 1

        # BLE Address
        ttk.Label(main_frame, text="BLE MAC Address:").grid(
            row=row, column=0, sticky=tk.W, pady=5
        )

        # BLE entry and scan button frame
        ble_frame = ttk.Frame(main_frame)
        ble_frame.grid(row=row, column=1, sticky=(tk.W, tk.E), pady=5)

        self.ble_address_var = tk.StringVar(value=self.config.get('ble_address', ''))
        ble_entry = ttk.Entry(
            ble_frame,
            textvariable=self.ble_address_var,
            width=25,
            font=('Courier', 10)
        )
        ble_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.scan_button = ttk.Button(
            ble_frame,
            text="Scan",
            command=self._on_scan_devices,
            width=8
        )
        self.scan_button.pack(side=tk.LEFT, padx=(5, 0))
        row += 1

        # Format hint
        ttk.Label(
            main_frame,
            text="Format: AA:BB:CC:DD:EE:FF",
            font=('Arial', 8),
            foreground='gray'
        ).grid(row=row, column=1, sticky=tk.W)
        row += 1

        # Device scan results frame
        scan_frame = ttk.LabelFrame(main_frame, text="Available Devices", padding="5")
        scan_frame.grid(row=row, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(5, 10))

        # Listbox with scrollbar
        list_frame = ttk.Frame(scan_frame)
        list_frame.pack(fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.device_listbox = tk.Listbox(
            list_frame,
            height=6,
            yscrollcommand=scrollbar.set,
            font=('Courier', 9)
        )
        self.device_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.device_listbox.yview)

        # Bind double-click to select device
        self.device_listbox.bind('<Double-Button-1>', self._on_device_select)

        # Status label for scan feedback
        self.scan_status_var = tk.StringVar(value="Click 'Scan' to find devices")
        self.scan_status_label = ttk.Label(
            scan_frame,
            textvariable=self.scan_status_var,
            font=('Arial', 8),
            foreground='gray'
        )
        self.scan_status_label.pack(pady=(5, 0))

        row += 1

        # Separator
        ttk.Separator(main_frame, orient='horizontal').grid(
            row=row, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=15
        )
        row += 1

        # TCP Port
        ttk.Label(main_frame, text="TCP Port:").grid(
            row=row, column=0, sticky=tk.W, pady=5
        )
        self.tcp_port_var = tk.IntVar(value=self.config.get('tcp_port', 4403))
        port_entry = ttk.Entry(main_frame, textvariable=self.tcp_port_var, width=10)
        port_entry.grid(row=row, column=1, sticky=tk.W, pady=5)
        row += 1

        ttk.Label(
            main_frame,
            text="Default: 4403",
            font=('Arial', 8),
            foreground='gray'
        ).grid(row=row, column=1, sticky=tk.W)
        row += 1

        # Separator
        ttk.Separator(main_frame, orient='horizontal').grid(
            row=row, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=15
        )
        row += 1

        # Cache settings header
        ttk.Label(
            main_frame,
            text="Config Caching",
            font=('Arial', 10, 'bold')
        ).grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=(0, 10))
        row += 1

        # Enable cache checkbox
        self.cache_enabled_var = tk.BooleanVar(
            value=self.config.get('cache_enabled', True)
        )
        cache_check = ttk.Checkbutton(
            main_frame,
            text="Enable config caching (faster reconnections)",
            variable=self.cache_enabled_var,
            command=self._on_cache_toggle
        )
        cache_check.grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=5)
        row += 1

        # Max cache nodes
        self.max_nodes_label = ttk.Label(main_frame, text="Max cached nodes:")
        self.max_nodes_label.grid(row=row, column=0, sticky=tk.W, pady=5)

        self.max_cache_nodes_var = tk.IntVar(
            value=self.config.get('max_cache_nodes', 500)
        )
        self.max_nodes_entry = ttk.Entry(
            main_frame,
            textvariable=self.max_cache_nodes_var,
            width=10
        )
        self.max_nodes_entry.grid(row=row, column=1, sticky=tk.W, pady=5)
        row += 1

        # Cache warning
        self.cache_warning = ttk.Label(
            main_frame,
            text="Warning: Caching may interfere with device reconfiguration",
            font=('Arial', 8),
            foreground='#d97706'
        )
        self.cache_warning.grid(row=row, column=0, columnspan=2, sticky=tk.W)
        row += 1

        # Separator
        ttk.Separator(main_frame, orient='horizontal').grid(
            row=row, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=15
        )
        row += 1

        # Startup settings header
        ttk.Label(
            main_frame,
            text="Startup",
            font=('Arial', 10, 'bold')
        ).grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=(0, 10))
        row += 1

        # Auto-connect checkbox
        self.autostart_var = tk.BooleanVar(
            value=self.config.get('autostart', False)
        )
        ttk.Checkbutton(
            main_frame,
            text="Auto-connect on application start",
            variable=self.autostart_var
        ).grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=5)
        row += 1

        # Spacer
        ttk.Frame(main_frame, height=20).grid(row=row, column=0)
        row += 1

        # Buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=row, column=0, columnspan=2, pady=(10, 0))

        ttk.Button(
            button_frame,
            text="Save",
            command=self._on_save,
            width=12
        ).pack(side=tk.LEFT, padx=5)

        ttk.Button(
            button_frame,
            text="Cancel",
            command=self._on_cancel,
            width=12
        ).pack(side=tk.LEFT, padx=5)

        # Update cache controls state
        self._on_cache_toggle()

        # Configure grid weights
        main_frame.columnconfigure(1, weight=1)

    def _on_cache_toggle(self):
        """Handle cache enable/disable toggle"""
        enabled = self.cache_enabled_var.get()
        state = 'normal' if enabled else 'disabled'

        self.max_nodes_label.config(state=state)
        self.max_nodes_entry.config(state=state)

        if enabled:
            self.cache_warning.grid()
        else:
            self.cache_warning.grid_remove()

    def _validate(self):
        """Validate settings"""
        ble_address = self.ble_address_var.get().strip()

        # Validate BLE address
        if not ble_address:
            _show_error(
                "Validation Error",
                "BLE MAC address is required"
            )
            return False

        # Validate MAC address format
        mac_pattern = r'^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$'
        if not re.match(mac_pattern, ble_address):
            _show_error(
                "Validation Error",
                "Invalid MAC address format.\n\n"
                "Expected format: AA:BB:CC:DD:EE:FF\n"
                "Example: 48:CA:43:59:4C:71"
            )
            return False

        # Validate TCP port
        tcp_port = self.tcp_port_var.get()
        if tcp_port < 1 or tcp_port > 65535:
            _show_error(
                "Validation Error",
                "TCP port must be between 1 and 65535"
            )
            return False

        # Validate max cache nodes
        if self.cache_enabled_var.get():
            max_nodes = self.max_cache_nodes_var.get()
            if max_nodes < 1:
                _show_error(
                    "Validation Error",
                    "Max cache nodes must be at least 1"
                )
                return False

        return True

    def _on_scan_devices(self):
        """Handle scan button click"""
        if self.scanning:
            return  # Already scanning

        self.scanning = True
        self.scan_button.config(state='disabled', text='Scanning...')
        self.scan_status_var.set('Scanning for devices...')
        self.device_listbox.delete(0, tk.END)
        self.scan_results.clear()

        # Run scan in background thread
        scan_thread = threading.Thread(target=self._scan_devices_thread, daemon=True)
        scan_thread.start()

    def _scan_devices_thread(self):
        """Background thread for device scanning"""
        try:
            # Import here to avoid circular imports
            from core.ble_handler import BLEHandler
            from core.stats import StatsCollector

            # Create event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                stats = StatsCollector()
                ble = BLEHandler("", stats)
                devices = loop.run_until_complete(ble.scan_devices())

                # Update UI from main thread
                self.root.after(0, self._on_scan_complete, devices, None)
            finally:
                loop.close()

        except Exception as e:
            # Update UI from main thread
            self.root.after(0, self._on_scan_complete, None, str(e))

    def _on_scan_complete(self, devices, error):
        """Handle scan completion (runs in main thread)"""
        self.scanning = False
        self.scan_button.config(state='normal', text='Scan')

        if error:
            self.scan_status_var.set(f'Scan failed: {error}')
            _show_error("Scan Failed", f"Failed to scan for devices:\n\n{error}")
            return

        if not devices:
            self.scan_status_var.set('No Meshtastic devices found')
            return

        # Populate listbox
        for device in devices:
            name = device.name or 'Unknown'
            address = device.address
            self.scan_results.append((name, address))
            self.device_listbox.insert(tk.END, f"{name:30s} {address}")

        self.scan_status_var.set(f'Found {len(devices)} device(s) - Double-click to select')

    def _on_device_select(self, event=None):
        """Handle device selection from list"""
        selection = self.device_listbox.curselection()
        if not selection:
            return

        index = selection[0]
        if index < len(self.scan_results):
            name, address = self.scan_results[index]
            self.ble_address_var.set(address)
            self.scan_status_var.set(f'Selected: {name}')

    def _on_save(self):
        """Handle save button"""
        if not self._validate():
            return

        # Update config
        self.config['ble_address'] = self.ble_address_var.get().strip().upper()
        self.config['tcp_port'] = self.tcp_port_var.get()
        self.config['cache_enabled'] = self.cache_enabled_var.get()
        self.config['max_cache_nodes'] = self.max_cache_nodes_var.get()
        self.config['autostart'] = self.autostart_var.get()

        # Call callback
        self.on_save(self.config)

        # Close dialog
        self.root.destroy()

    def _on_cancel(self):
        """Handle cancel button"""
        self.root.destroy()

    def show(self):
        """Show dialog (blocks until closed)"""
        self.root.mainloop()

    def focus(self):
        """Bring window to front"""
        self.root.lift()
        self.root.focus_force()
