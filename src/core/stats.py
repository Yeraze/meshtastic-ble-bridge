"""Statistics tracking for bridge operations"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Callable, List
import asyncio
import logging

logger = logging.getLogger(__name__)


@dataclass
class BridgeStatistics:
    """Real-time bridge statistics"""
    # Connection state
    ble_connected: bool = False
    ble_address: Optional[str] = None
    connected_since: Optional[datetime] = None
    last_disconnect: Optional[datetime] = None

    # Packet counters
    packets_from_ble: int = 0
    packets_to_ble: int = 0
    packets_to_tcp: int = 0
    bytes_from_ble: int = 0
    bytes_to_ble: int = 0

    # TCP clients
    tcp_clients_count: int = 0
    tcp_clients_peak: int = 0

    # Reconnection tracking
    reconnect_attempts: int = 0
    reconnect_successes: int = 0

    # Cache stats
    cache_enabled: bool = False
    cache_size: int = 0
    cache_hits: int = 0
    cache_misses: int = 0

    # Performance
    last_packet_time: Optional[datetime] = None

    def reset_counters(self):
        """Reset packet counters (not lifetime stats)"""
        self.packets_from_ble = 0
        self.packets_to_ble = 0
        self.packets_to_tcp = 0
        self.bytes_from_ble = 0
        self.bytes_to_ble = 0

    def uptime(self) -> Optional[str]:
        """Get uptime string"""
        if not self.connected_since:
            return None
        delta = datetime.now() - self.connected_since
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


class StatsCollector:
    """Collects and exposes bridge statistics with callback notifications"""

    def __init__(self):
        self.stats = BridgeStatistics()
        self._callbacks: List[Callable] = []
        self._lock = asyncio.Lock()

    def register_callback(self, callback: Callable):
        """Register callback for stats updates"""
        self._callbacks.append(callback)

    def unregister_callback(self, callback: Callable):
        """Unregister callback"""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    async def _notify_update(self):
        """Notify all callbacks of stats change"""
        for callback in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(self.stats)
                else:
                    callback(self.stats)
            except Exception as e:
                logger.debug(f"Stats callback error: {e}")

    async def on_ble_connected(self, address: str):
        """Called when BLE connection established"""
        async with self._lock:
            self.stats.ble_connected = True
            self.stats.ble_address = address
            self.stats.connected_since = datetime.now()
            await self._notify_update()

    async def on_ble_disconnected(self):
        """Called when BLE connection lost"""
        async with self._lock:
            self.stats.ble_connected = False
            self.stats.last_disconnect = datetime.now()
            self.stats.connected_since = None
            await self._notify_update()

    async def on_packet_from_ble(self, size: int):
        """Called when packet received from BLE"""
        async with self._lock:
            self.stats.packets_from_ble += 1
            self.stats.bytes_from_ble += size
            self.stats.last_packet_time = datetime.now()
            await self._notify_update()

    async def on_packet_to_ble(self, size: int):
        """Called when packet sent to BLE"""
        async with self._lock:
            self.stats.packets_to_ble += 1
            self.stats.bytes_to_ble += size
            await self._notify_update()

    async def on_packet_to_tcp(self):
        """Called when packet sent to TCP client"""
        async with self._lock:
            self.stats.packets_to_tcp += 1
            await self._notify_update()

    async def on_tcp_clients_changed(self, count: int):
        """Called when TCP client count changes"""
        async with self._lock:
            self.stats.tcp_clients_count = count
            if count > self.stats.tcp_clients_peak:
                self.stats.tcp_clients_peak = count
            await self._notify_update()

    async def on_reconnect_attempt(self):
        """Called when reconnection attempt starts"""
        async with self._lock:
            self.stats.reconnect_attempts += 1
            await self._notify_update()

    async def on_reconnect_success(self):
        """Called when reconnection succeeds"""
        async with self._lock:
            self.stats.reconnect_successes += 1
            await self._notify_update()

    async def on_cache_hit(self):
        """Called when cache serves a request"""
        async with self._lock:
            self.stats.cache_hits += 1
            await self._notify_update()

    async def on_cache_miss(self):
        """Called when cache cannot serve request"""
        async with self._lock:
            self.stats.cache_misses += 1
            await self._notify_update()

    async def update_cache_size(self, size: int):
        """Update cache size"""
        async with self._lock:
            self.stats.cache_size = size
            await self._notify_update()

    def get_stats(self) -> BridgeStatistics:
        """Get current statistics snapshot"""
        return self.stats
