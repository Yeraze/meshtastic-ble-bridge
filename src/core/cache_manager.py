"""Config caching system for faster reconnections"""
import asyncio
import logging
import time
from typing import Optional, Tuple, List
from meshtastic import mesh_pb2, telemetry_pb2
from .protocol import ProtocolHandler
from .stats import StatsCollector

logger = logging.getLogger(__name__)


class CacheManager:
    """Manages config caching for improved reconnection performance"""

    def __init__(self, enabled: bool, max_nodes: int, stats: StatsCollector):
        self.enabled = enabled
        self.max_nodes = max_nodes
        self.stats = stats

        # Cache storage: list of (protobuf_bytes, tcp_frame) tuples
        self.cache: List[Tuple[bytes, bytes]] = []
        self.complete = False
        self.recording = False
        self.current_config_id: Optional[int] = None

        # Update stats
        self.stats.stats.cache_enabled = enabled

    async def prewarm(self, ble_send_callback):
        """
        Pre-warm cache by requesting config from device.

        Args:
            ble_send_callback: Async function to send ToRadio messages to BLE
        """
        if not self.enabled:
            return

        logger.info("🔥 Pre-warming config cache...")

        try:
            # Generate unique config ID
            import random
            config_id = random.randint(1, 2**32 - 1)

            # Start recording
            self.recording = True
            self.current_config_id = config_id
            self.cache.clear()
            self.complete = False

            # Create want_config_id request
            to_radio = mesh_pb2.ToRadio()
            to_radio.want_config_id = config_id

            # Send request
            await ble_send_callback(to_radio.SerializeToString())
            logger.info(f"📨 Sent want_config_id request (id={config_id})")

            # Wait for completion
            max_wait = 30  # seconds
            check_interval = 0.5
            waited = 0

            while waited < max_wait:
                await asyncio.sleep(check_interval)
                waited += check_interval

                if self.complete:
                    logger.info(f"✅ Config cache pre-warmed with {len(self.cache)} packets")
                    self.recording = False
                    await self.stats.update_cache_size(len(self.cache))
                    return

            logger.warning(
                f"⚠️  Cache pre-warming timed out after {max_wait}s "
                f"(cache size: {len(self.cache)})"
            )
            self.recording = False

        except Exception as e:
            error_msg = str(e)
            if "Authentication" in error_msg or "Protocol Error 0x05" in error_msg:
                logger.warning(
                    f"⚠️  Cache pre-warming failed: Authentication required\n"
                    f"   💡 Pair device in Windows Settings → Bluetooth & devices\n"
                    f"   Bridge will continue without cache (slower reconnections)"
                )
            else:
                logger.warning(f"⚠️  Cache pre-warming failed: {e}")
            self.recording = False

    async def process_packet(self, protobuf_bytes: bytes, tcp_frame: bytes):
        """
        Process incoming BLE packet for caching.

        Args:
            protobuf_bytes: Raw protobuf FromRadio message
            tcp_frame: TCP-framed version of the packet
        """
        if not self.enabled:
            return

        try:
            from_radio = mesh_pb2.FromRadio()
            from_radio.ParseFromString(protobuf_bytes)

            # Check for config_complete_id
            if from_radio.HasField('config_complete_id'):
                if (self.recording and
                    from_radio.config_complete_id == self.current_config_id):
                    # Add to cache and mark complete
                    self.cache.append((protobuf_bytes, tcp_frame))
                    self.complete = True
                    self.recording = False

                    # Enforce size limit
                    await self._enforce_size_limit()

                    logger.info(f"✅ Config cache complete with {len(self.cache)} packets")
                    await self.stats.update_cache_size(len(self.cache))

            # If recording, cache everything
            elif self.recording:
                self.cache.append((protobuf_bytes, tcp_frame))
                self._log_cached_packet(from_radio)

            # Update cache for runtime changes
            elif self.complete:
                await self._update_runtime_cache(from_radio, protobuf_bytes, tcp_frame)

        except Exception as e:
            logger.debug(f"Failed to process packet for caching: {e}")

    def _log_cached_packet(self, from_radio):
        """Log what type of packet was cached"""
        if from_radio.HasField('node_info'):
            logger.debug(f"💾 Cached NodeInfo for node {from_radio.node_info.num:#x}")
        elif from_radio.HasField('my_info'):
            logger.debug(f"💾 Cached MyNodeInfo")
        elif from_radio.HasField('config'):
            logger.debug(f"💾 Cached Config")
        elif from_radio.HasField('moduleConfig'):
            logger.debug(f"💾 Cached ModuleConfig")
        elif from_radio.HasField('channel'):
            logger.debug(f"💾 Cached Channel")

    async def _enforce_size_limit(self):
        """Enforce max_nodes limit by removing oldest nodes"""
        node_count = 0
        for proto, _ in self.cache:
            try:
                temp_radio = mesh_pb2.FromRadio()
                temp_radio.ParseFromString(proto)
                if temp_radio.HasField('node_info'):
                    node_count += 1
            except Exception:
                pass

        if node_count > self.max_nodes:
            nodes_to_remove = node_count - self.max_nodes
            new_cache = []
            nodes_removed = 0

            for proto, frame in self.cache:
                try:
                    temp_radio = mesh_pb2.FromRadio()
                    temp_radio.ParseFromString(proto)
                    if (temp_radio.HasField('node_info') and
                        nodes_removed < nodes_to_remove):
                        nodes_removed += 1
                        continue  # Skip this node
                except Exception:
                    pass
                new_cache.append((proto, frame))

            self.cache = new_cache
            logger.warning(
                f"⚠️  Cache size limit reached: removed {nodes_removed} oldest nodes "
                f"(limit: {self.max_nodes})"
            )
            await self.stats.update_cache_size(len(self.cache))

    async def _update_runtime_cache(self, from_radio, protobuf_bytes: bytes,
                                    tcp_frame: bytes):
        """Update cache with runtime NodeInfo changes"""
        # Handle NodeInfo updates
        if from_radio.HasField('node_info'):
            node_num = from_radio.node_info.num
            node_found = False

            # Find and replace existing node
            for i, (cached_proto, _) in enumerate(self.cache[:-1]):
                try:
                    cached_from_radio = mesh_pb2.FromRadio()
                    cached_from_radio.ParseFromString(cached_proto)
                    if (cached_from_radio.HasField('node_info') and
                        cached_from_radio.node_info.num == node_num):
                        self.cache[i] = (protobuf_bytes, tcp_frame)
                        logger.debug(f"🔄 Updated cache for node {node_num:#x}")
                        node_found = True
                        break
                except Exception as e:
                    logger.debug(f"Failed to check cached node: {e}")

            # Add new node if not found
            if not node_found:
                # Find config_complete position
                complete_index = len(self.cache)
                for i, (proto, _) in enumerate(self.cache):
                    try:
                        temp = mesh_pb2.FromRadio()
                        temp.ParseFromString(proto)
                        if temp.HasField('config_complete_id'):
                            complete_index = i
                            break
                    except Exception:
                        pass

                self.cache.insert(complete_index, (protobuf_bytes, tcp_frame))
                logger.debug(f"➕ Added new node {node_num:#x} to cache")
                await self.stats.update_cache_size(len(self.cache))

        # Handle MeshPacket updates (position, telemetry, user)
        elif from_radio.HasField('packet'):
            await self._update_packet_data(from_radio.packet)

    async def _update_packet_data(self, packet):
        """Update cached NodeInfo with packet data (position, telemetry, user)"""
        node_num = getattr(packet, 'from')  # 'from' is reserved keyword

        if not packet.HasField('decoded'):
            return

        decoded = packet.decoded
        update_type = None

        # Determine update type
        if decoded.portnum == 3:  # POSITION_APP
            update_type = "position"
        elif decoded.portnum == 67:  # TELEMETRY_APP
            update_type = "telemetry"
        elif decoded.portnum == 4:  # NODEINFO_APP
            update_type = "user"

        if not update_type or not node_num:
            return

        # Find and update cached node
        for i, (cached_proto, _) in enumerate(self.cache[:-1]):
            try:
                cached_from_radio = mesh_pb2.FromRadio()
                cached_from_radio.ParseFromString(cached_proto)

                if (cached_from_radio.HasField('node_info') and
                    cached_from_radio.node_info.num == node_num):

                    # Update specific field
                    if update_type == "position":
                        position = mesh_pb2.Position()
                        position.ParseFromString(decoded.payload)
                        cached_from_radio.node_info.position.CopyFrom(position)
                        logger.debug(f"📍 Updated position for node {node_num:#x}")

                    elif update_type == "telemetry":
                        telemetry = telemetry_pb2.Telemetry()
                        telemetry.ParseFromString(decoded.payload)
                        if telemetry.HasField('device_metrics'):
                            cached_from_radio.node_info.device_metrics.CopyFrom(
                                telemetry.device_metrics
                            )
                            logger.debug(f"🔋 Updated telemetry for node {node_num:#x}")

                    elif update_type == "user":
                        user = mesh_pb2.User()
                        user.ParseFromString(decoded.payload)
                        cached_from_radio.node_info.user.CopyFrom(user)
                        logger.debug(f"👤 Updated user for node {node_num:#x}")

                    # Update last_heard timestamp
                    cached_from_radio.node_info.last_heard = int(time.time())

                    # Re-serialize and update cache
                    updated_bytes = cached_from_radio.SerializeToString()
                    updated_frame = ProtocolHandler.create_tcp_frame(updated_bytes)
                    self.cache[i] = (updated_bytes, updated_frame)
                    break

            except Exception as e:
                logger.debug(f"Failed to update cache with {update_type}: {e}")

    def can_serve(self, to_radio_bytes: bytes) -> bool:
        """
        Check if cache can serve a ToRadio request.

        Args:
            to_radio_bytes: ToRadio protobuf bytes

        Returns:
            True if cache can serve this request
        """
        if not self.enabled or not self.complete:
            return False

        try:
            to_radio = mesh_pb2.ToRadio()
            to_radio.ParseFromString(to_radio_bytes)
            return to_radio.HasField('want_config_id')
        except Exception:
            return False

    async def serve(self, to_radio_bytes: bytes) -> List[bytes]:
        """
        Serve cached config for a want_config_id request.

        Args:
            to_radio_bytes: ToRadio protobuf bytes

        Returns:
            List of TCP frames to send to client
        """
        if not self.can_serve(to_radio_bytes):
            await self.stats.on_cache_miss()
            return []

        try:
            to_radio = mesh_pb2.ToRadio()
            to_radio.ParseFromString(to_radio_bytes)
            request_id = to_radio.want_config_id

            logger.info(
                f"🚀 Serving {len(self.cache)} cached packets for "
                f"want_config_id={request_id}"
            )

            frames = []
            for i, (proto_bytes, tcp_frame) in enumerate(self.cache):
                # For the last packet (config_complete_id), update the ID to match request
                if i == len(self.cache) - 1:
                    from_radio = mesh_pb2.FromRadio()
                    from_radio.ParseFromString(proto_bytes)
                    if from_radio.HasField('config_complete_id'):
                        from_radio.config_complete_id = request_id
                        updated_bytes = from_radio.SerializeToString()
                        updated_frame = ProtocolHandler.create_tcp_frame(updated_bytes)
                        frames.append(updated_frame)
                    else:
                        frames.append(tcp_frame)
                else:
                    frames.append(tcp_frame)

            await self.stats.on_cache_hit()
            logger.debug(f"✅ Served {len(frames)} cached frames")
            return frames

        except Exception as e:
            logger.error(f"Failed to serve from cache: {e}")
            await self.stats.on_cache_miss()
            return []
