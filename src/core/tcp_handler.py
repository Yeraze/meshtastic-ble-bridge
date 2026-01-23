"""TCP server handling for Meshtastic bridge"""
import asyncio
import logging
from typing import List, Optional, Callable
from meshtastic import mesh_pb2
from .protocol import ProtocolHandler
from .stats import StatsCollector

logger = logging.getLogger(__name__)


class TCPHandler:
    """Handles TCP server and client connections"""

    def __init__(self, port: int, stats: StatsCollector):
        self.port = port
        self.stats = stats

        self.server: Optional[asyncio.Server] = None
        self.clients: List[asyncio.StreamWriter] = []
        self.running = False

        # Callbacks
        self.on_packet_received: Optional[Callable] = None  # (packet_bytes, client_addr)

    async def start(self):
        """Start TCP server"""
        self.server = await asyncio.start_server(
            self._handle_client,
            '0.0.0.0',  # Listen on all interfaces
            self.port
        )

        addr = self.server.sockets[0].getsockname()
        logger.info(f"✅ TCP server listening on {addr[0]}:{addr[1]}")
        logger.info(f"Clients can connect to <bridge-ip>:{self.port}")

        self.running = True

    async def serve_forever(self):
        """Run server until stopped"""
        if not self.server:
            raise RuntimeError("Server not started")

        async with self.server:
            await self.server.serve_forever()

    async def _handle_client(self, reader: asyncio.StreamReader,
                            writer: asyncio.StreamWriter):
        """Handle a new TCP client connection"""
        addr = writer.get_extra_info('peername')
        logger.info(f"🔌 TCP client connected from {addr}")

        self.clients.append(writer)
        await self.stats.on_tcp_clients_changed(len(self.clients))

        try:
            while self.running:
                # Read TCP frame
                try:
                    protobuf_bytes = await ProtocolHandler.read_tcp_frame(reader)
                except ValueError as e:
                    logger.warning(f"Invalid frame from {addr}: {e}")
                    continue

                logger.debug(f"📥 TCP frame received from {addr}: {len(protobuf_bytes)} bytes")

                # Notify callback
                if self.on_packet_received:
                    await self.on_packet_received(protobuf_bytes, addr)

        except asyncio.IncompleteReadError:
            logger.info(f"TCP client {addr} disconnected")
        except ConnectionResetError:
            logger.info(f"TCP client {addr} connection reset by peer")
        except Exception as e:
            logger.error(f"Error handling TCP client {addr}: {e}")
        finally:
            # Remove client
            if writer in self.clients:
                self.clients.remove(writer)
                await self.stats.on_tcp_clients_changed(len(self.clients))

            # Close writer
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionResetError, ConnectionError, OSError):
                pass  # Already closed

            logger.info(f"TCP client {addr} closed ({len(self.clients)} remaining)")

    async def broadcast(self, frame: bytes):
        """
        Broadcast frame to all connected TCP clients.

        Args:
            frame: TCP-framed protobuf bytes to send
        """
        if not self.clients:
            logger.debug("No TCP clients connected, dropping packet")
            return

        logger.debug(f"📤 Broadcasting to {len(self.clients)} TCP client(s)")

        disconnected = []
        for writer in self.clients:
            try:
                writer.write(frame)
                await writer.drain()
                await self.stats.on_packet_to_tcp()
            except Exception as e:
                logger.warning(f"Failed to send to TCP client: {e}")
                disconnected.append(writer)

        # Remove disconnected clients
        for writer in disconnected:
            if writer in self.clients:
                self.clients.remove(writer)
                await self.stats.on_tcp_clients_changed(len(self.clients))
                logger.info(f"TCP client disconnected ({len(self.clients)} remaining)")

    async def send_to_client(self, addr, frames: List[bytes]):
        """
        Send multiple frames to a specific client.

        Args:
            addr: Client address tuple (ip, port)
            frames: List of TCP-framed bytes to send
        """
        # Find client by address
        target_writer = None
        for writer in self.clients:
            if writer.get_extra_info('peername') == addr:
                target_writer = writer
                break

        if not target_writer:
            logger.warning(f"Client {addr} not found for sending")
            return

        try:
            for frame in frames:
                target_writer.write(frame)
                await target_writer.drain()
                await self.stats.on_packet_to_tcp()

            logger.debug(f"✅ Sent {len(frames)} frames to {addr}")

        except Exception as e:
            logger.error(f"Failed to send to {addr}: {e}")
            if target_writer in self.clients:
                self.clients.remove(target_writer)
                await self.stats.on_tcp_clients_changed(len(self.clients))

    async def stop(self):
        """Stop TCP server"""
        self.running = False

        # Close all clients
        for writer in self.clients:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

        self.clients.clear()
        await self.stats.on_tcp_clients_changed(0)

        # Close server
        if self.server:
            logger.info("Closing TCP server...")
            self.server.close()
            await self.server.wait_closed()
            logger.info("✅ TCP server closed")

    def get_client_count(self) -> int:
        """Get number of connected clients"""
        return len(self.clients)
