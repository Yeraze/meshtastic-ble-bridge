"""TCP protocol framing and parsing for Meshtastic"""
import struct
import logging

logger = logging.getLogger(__name__)

# TCP Protocol constants
START1 = 0x94
START2 = 0xC3
MAX_PACKET_SIZE = 512


class ProtocolHandler:
    """Handles TCP frame creation and parsing"""

    @staticmethod
    def create_tcp_frame(protobuf_bytes: bytes) -> bytes:
        """
        Create TCP frame from protobuf bytes.
        Frame format: [0x94][0xC3][LENGTH_MSB][LENGTH_LSB][PROTOBUF]

        Args:
            protobuf_bytes: Raw protobuf message bytes

        Returns:
            Complete TCP frame with header

        Raises:
            ValueError: If packet size exceeds MAX_PACKET_SIZE
        """
        length = len(protobuf_bytes)
        if length > MAX_PACKET_SIZE:
            raise ValueError(
                f"Packet too large: {length} bytes > {MAX_PACKET_SIZE} bytes"
            )

        # Pack header: START1 + START2 + length (big-endian 16-bit)
        header = struct.pack('>BBH', START1, START2, length)
        return header + protobuf_bytes

    @staticmethod
    async def read_tcp_frame(reader) -> bytes:
        """
        Read TCP frame from asyncio StreamReader.

        Args:
            reader: asyncio.StreamReader instance

        Returns:
            Raw protobuf bytes (without frame header)

        Raises:
            asyncio.IncompleteReadError: If connection closes mid-frame
            ValueError: If frame header is invalid
        """
        # Read 4-byte header
        header = await reader.readexactly(4)

        # Validate magic bytes
        if header[0] != START1 or header[1] != START2:
            raise ValueError(
                f"Invalid frame header: got {header[0]:02x} {header[1]:02x}, "
                f"expected {START1:02x} {START2:02x}"
            )

        # Parse length field (big-endian)
        length = struct.unpack('>H', header[2:4])[0]

        # Validate length
        if length > MAX_PACKET_SIZE:
            raise ValueError(
                f"Frame length too large: {length} > {MAX_PACKET_SIZE}"
            )

        # Read protobuf payload
        protobuf_bytes = await reader.readexactly(length)
        return protobuf_bytes

    @staticmethod
    def validate_frame_size(protobuf_bytes: bytes) -> bool:
        """
        Check if protobuf bytes are within valid frame size.

        Args:
            protobuf_bytes: Raw protobuf bytes

        Returns:
            True if valid size, False otherwise
        """
        return len(protobuf_bytes) <= MAX_PACKET_SIZE
