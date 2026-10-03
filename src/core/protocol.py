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
        Read one Meshtastic TCP frame from an asyncio StreamReader.

        The Meshtastic stream protocol may contain arbitrary bytes before
        a frame, including the 0xC3 wake/resync preamble sent by official
        clients. Scan byte-by-byte until the 0x94 0xC3 magic sequence is
        found instead of assuming the stream starts on a frame boundary.
        """

        state = 0

        while True:
            byte = await reader.readexactly(1)
            value = byte[0]

            if state == 0:
                # Looking for START1 (0x94)
                if value == START1:
                    state = 1
                continue

            # We already saw START1
            if value == START2:
                # Valid magic sequence 94 C3 found
                length_bytes = await reader.readexactly(2)
                length = struct.unpack('>H', length_bytes)[0]

                if length > MAX_PACKET_SIZE:
                    logger.warning(
                        f"Invalid frame length {length}; resynchronizing"
                    )
                    state = 0
                    continue

                return await reader.readexactly(length)

            # Handle 94 94 C3 without losing the second 94
            if value == START1:
                state = 1
            else:
                state = 0

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
