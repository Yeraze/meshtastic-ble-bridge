# BLE-to-TCP Bridge for Meshtastic: Implementation Analysis

**Analysis Date:** 2025-10-21
**Purpose:** Enable MeshMonitor to connect to Meshtastic nodes over Bluetooth Low Energy (BLE)

## Executive Summary

Creating a BLE-to-TCP bridge application would allow MeshMonitor to connect to Meshtastic nodes via Bluetooth, effectively translating BLE characteristic reads/writes into the TCP framing protocol that MeshMonitor expects. This is a **moderate complexity project** (~3-5 days of development) that would significantly expand MeshMonitor's compatibility.

### Effort Estimate
- **Simple Proof of Concept:** 1-2 days
- **Production-Ready Bridge:** 3-5 days
- **Python Alternative:** 2-3 days (using existing meshtastic library)

---

## Architecture Overview

```
┌──────────────────┐
│   MeshMonitor    │
│   (Browser)      │
└────────┬─────────┘
         │ HTTP/WebSocket
         │
┌────────▼─────────┐
│  MeshMonitor     │
│  Backend Server  │
└────────┬─────────┘
         │ TCP (localhost:4403)
         │ Meshtastic Protocol:
         │ [0x94][0xC3][LEN_MSB][LEN_LSB][PROTOBUF]
         │
┌────────▼─────────┐
│  BLE-TCP Bridge  │  ← NEW APPLICATION
│                  │
│  • Listen on TCP │
│  • Connect to BLE│
│  • Translate     │
│    protocols     │
└────────┬─────────┘
         │ BLE
         │ Service: 6ba1b218-15a8-461f-9fa8-5dcae273eafd
         │ ToRadio:   f75c76d2-129e-4dad-a1dd-7866124401e7 (write)
         │ FromRadio: 2c55e69e-4993-11ed-b878-0242ac120002 (read/notify)
         │
┌────────▼─────────┐
│  Meshtastic Node │
│  (BLE enabled)   │
└──────────────────┘
```

---

## Protocol Translation

### TCP Protocol (What MeshMonitor Expects)

**Frame Structure:**
```
[START1] [START2] [LENGTH_MSB] [LENGTH_LSB] [PROTOBUF_PAYLOAD]
  0x94     0xC3    high byte    low byte     N bytes
```

**Key Characteristics:**
- Fixed 4-byte header
- Length field is big-endian 16-bit (max 65535 bytes)
- MeshMonitor code expects MAX_PACKET_SIZE = 512 bytes
- Multiple frames can be in flight
- TCP provides reliable, ordered delivery

### BLE Protocol (What Meshtastic Node Provides)

**Service UUID:** `6ba1b218-15a8-461f-9fa8-5dcae273eafd`

**Characteristics:**

1. **ToRadio (Write)**
   - UUID: `f75c76d2-129e-4dad-a1dd-7866124401e7`
   - Direction: Bridge → Node
   - Write protobuf bytes directly (no framing!)
   - Max payload: Typically 512 bytes (BLE MTU dependent)

2. **FromRadio (Read/Notify)**
   - UUID: `2c55e69e-4993-11ed-b878-0242ac120002`
   - Direction: Node → Bridge
   - Subscribe to notifications for new packets
   - Read to get current packet
   - Returns raw protobuf bytes (no framing!)

3. **FromNum (Notify)**
   - UUID: `ed9da18c-a800-4f66-a670-aa7547e34453`
   - Indicates packet number available
   - Notifies when new packets arrive

**Key Differences:**
- No framing layer (raw protobufs)
- BLE handles packet boundaries at lower layer
- MTU negotiation affects max packet size
- Must subscribe to notifications for async messages

---

## Implementation Options

### Option 1: Node.js Bridge (Recommended)

**Pros:**
- Same language as MeshMonitor (TypeScript/JavaScript)
- Can potentially integrate into MeshMonitor directly later
- Good BLE library available (@abandonware/noble)
- Native async/await patterns

**Cons:**
- BLE library (noble) requires native bindings
- Platform-specific (uses different Bluetooth stacks)
- More complex than Python

**Dependencies:**
```json
{
  "@abandonware/noble": "^1.9.2-28",
  "@bufbuild/protobuf": "^2.9.0",
  "@meshtastic/protobufs": "^2.x"
}
```

**Code Structure:**
```typescript
// ble-tcp-bridge.ts
import noble from '@abandonware/noble';
import net from 'net';
import { Protobuf } from '@meshtastic/protobufs';

class MeshtasticBLEBridge {
  private tcpServer: net.Server;
  private blePeripheral: noble.Peripheral | null = null;
  private toRadioChar: noble.Characteristic | null = null;
  private fromRadioChar: noble.Characteristic | null = null;

  async start() {
    // 1. Scan for Meshtastic devices
    await this.scanForMeshtastic();

    // 2. Connect to BLE device
    await this.connectBLE();

    // 3. Discover service and characteristics
    await this.setupCharacteristics();

    // 4. Start TCP server
    this.startTCPServer();

    // 5. Bridge the protocols
    this.bridgeProtocols();
  }

  private async scanForMeshtastic() {
    // Scan for devices advertising Meshtastic service UUID
  }

  private async connectBLE() {
    // Connect to peripheral
    // Subscribe to FromRadio notifications
  }

  private async setupCharacteristics() {
    // Discover service: 6ba1b218-15a8-461f-9fa8-5dcae273eafd
    // Get ToRadio characteristic
    // Get FromRadio characteristic
    // Subscribe to notifications
  }

  private startTCPServer() {
    this.tcpServer = net.createServer((socket) => {
      // Handle TCP client (MeshMonitor)

      socket.on('data', async (data) => {
        // Parse TCP frames
        // Extract protobuf payload
        // Write to BLE ToRadio characteristic
        const frames = this.parseTCPFrames(data);
        for (const frame of frames) {
          await this.toRadioChar?.writeAsync(frame.payload, false);
        }
      });
    });

    this.tcpServer.listen(4403, 'localhost');
  }

  private bridgeProtocols() {
    // FromRadio notifications → TCP frames
    this.fromRadioChar?.on('data', (data: Buffer) => {
      // Wrap protobuf in TCP frame
      const frame = this.createTCPFrame(data);
      // Send to all connected TCP clients
      this.broadcastToTCP(frame);
    });
  }

  private createTCPFrame(protobuf: Buffer): Buffer {
    const length = protobuf.length;
    const header = Buffer.from([
      0x94,  // START1
      0xC3,  // START2
      (length >> 8) & 0xFF,  // MSB
      length & 0xFF          // LSB
    ]);
    return Buffer.concat([header, protobuf]);
  }

  private parseTCPFrames(data: Buffer): Array<{payload: Buffer}> {
    // Parse TCP framing protocol
    // Return array of protobuf payloads
  }
}
```

### Option 2: Python Bridge (Simpler)

**Pros:**
- Official meshtastic Python library with BLE support built-in
- Well-tested BLE implementation (using Bleak)
- Simpler to implement (library handles most BLE complexity)
- Cross-platform (Bleak works on Windows, Mac, Linux)

**Cons:**
- Requires Python runtime alongside Node.js
- Extra process to manage
- Slightly more resource usage

**Dependencies:**
```bash
pip install meshtastic
```

**Code Structure:**
```python
# ble_tcp_bridge.py
import socket
import asyncio
from meshtastic import ble_interface, mesh_pb2
import logging

class MeshtasticBLEBridge:
    def __init__(self, ble_address, tcp_port=4403):
        self.ble_address = ble_address
        self.tcp_port = tcp_port
        self.tcp_clients = []

    async def start(self):
        # Connect to BLE device
        self.interface = await ble_interface.BLEInterface(self.ble_address)

        # Start TCP server
        server = await asyncio.start_server(
            self.handle_tcp_client,
            'localhost',
            self.tcp_port
        )

        # Subscribe to BLE messages
        self.interface.onReceive = self.on_ble_message

        async with server:
            await server.serve_forever()

    def on_ble_message(self, packet):
        """BLE → TCP"""
        # Serialize protobuf
        protobuf = packet.SerializeToString()

        # Wrap in TCP frame
        length = len(protobuf)
        header = bytes([0x94, 0xC3, (length >> 8) & 0xFF, length & 0xFF])
        frame = header + protobuf

        # Broadcast to all TCP clients
        for client in self.tcp_clients:
            try:
                client.write(frame)
            except:
                self.tcp_clients.remove(client)

    async def handle_tcp_client(self, reader, writer):
        """TCP → BLE"""
        self.tcp_clients.append(writer)

        try:
            while True:
                # Read TCP frame
                header = await reader.readexactly(4)
                if header[0] != 0x94 or header[1] != 0xC3:
                    continue  # Skip to next frame

                length = (header[2] << 8) | header[3]
                protobuf = await reader.readexactly(length)

                # Parse protobuf
                packet = mesh_pb2.ToRadio()
                packet.ParseFromString(protobuf)

                # Send via BLE
                await self.interface.sendData(packet)
        finally:
            self.tcp_clients.remove(writer)

if __name__ == '__main__':
    # Usage: python ble_tcp_bridge.py <BLE_ADDRESS>
    import sys
    bridge = MeshtasticBLEBridge(sys.argv[1])
    asyncio.run(bridge.start())
```

---

## Key Implementation Challenges

### 1. BLE Device Discovery
**Challenge:** Finding the correct Meshtastic device
**Solution:**
- Scan for devices advertising service UUID `6ba1b218-15a8-461f-9fa8-5dcae273eafd`
- Filter by device name (usually starts with "Meshtastic")
- Optionally take BLE address as CLI argument

### 2. MTU Negotiation
**Challenge:** BLE has Maximum Transmission Unit limits (typically 20-512 bytes)
**Solution:**
- Request maximum MTU during connection (noble/Bleak handle this)
- Chunk large packets if needed (rare - Meshtastic packets usually < 512 bytes)
- Most modern devices support 512-byte MTU

### 3. Connection Stability
**Challenge:** BLE connections can drop
**Solution:**
- Implement automatic reconnection logic
- Buffer outgoing messages during disconnection
- Notify TCP clients of BLE connection status

### 4. Multiple TCP Clients
**Challenge:** MeshMonitor might connect multiple times (reconnects, refreshes)
**Solution:**
- Accept multiple TCP connections
- Broadcast BLE → TCP to all connected clients
- Accept TCP → BLE from any client (first wins)

### 5. Flow Control
**Challenge:** BLE write operations are async, TCP expects ack
**Solution:**
- Queue writes to BLE
- Use `writeWithResponse` for reliability
- Implement backpressure if queue grows too large

---

## Development Roadmap

### Phase 1: Proof of Concept (1-2 days)
- [ ] Set up Node.js project with @abandonware/noble
- [ ] Scan and connect to Meshtastic BLE device
- [ ] Discover service and characteristics
- [ ] Subscribe to FromRadio notifications
- [ ] Print received packets to console
- [ ] Manually test writing to ToRadio

### Phase 2: TCP Server (1 day)
- [ ] Create TCP server listening on localhost:4403
- [ ] Parse TCP framing protocol
- [ ] Extract protobuf payloads
- [ ] Handle multiple TCP clients

### Phase 3: Protocol Bridging (1 day)
- [ ] BLE → TCP: Wrap FromRadio packets in TCP frames
- [ ] TCP → BLE: Extract payloads and write to ToRadio
- [ ] Handle connection lifecycle (connect/disconnect)
- [ ] Implement error handling

### Phase 4: Production Hardening (1-2 days)
- [ ] Automatic BLE reconnection
- [ ] Connection status monitoring
- [ ] Logging and debugging output
- [ ] CLI arguments (BLE address, TCP port, log level)
- [ ] Graceful shutdown
- [ ] Documentation and README

---

## Alternative: Integrate BLE Directly into MeshMonitor

Instead of a separate bridge application, BLE support could be added directly to MeshMonitor as an alternative to TCP transport.

**Pros:**
- No separate process
- Cleaner architecture
- Better user experience (one app)

**Cons:**
- More complex integration
- Platform-specific code in main app
- BLE library native bindings
- Harder to test/debug

**Recommendation:** Start with standalone bridge first. If successful and widely used, consider integration later.

---

## Technology Stack Recommendations

### Recommended Approach: Python Bridge

**Why Python:**
1. **Proven Library:** `meshtastic` Python package has mature BLE support
2. **Cross-Platform:** Bleak library works on Windows, Mac, Linux
3. **Rapid Development:** Less code, fewer edge cases
4. **Maintenance:** Official support from Meshtastic team
5. **Simple Deployment:** Single Python script

**Development Time:** 2-3 days

### Alternative: Node.js Bridge

**Why Node.js:**
1. **Language Consistency:** Same as MeshMonitor (TypeScript)
2. **Future Integration:** Easier to merge into main app later
3. **No Python Dependency:** Some users prefer single runtime

**Development Time:** 3-5 days

---

## Deployment Scenarios

### Scenario 1: Standalone Bridge (Recommended)
```bash
# Terminal 1: Start bridge
python ble_tcp_bridge.py AA:BB:CC:DD:EE:FF

# Terminal 2: Start MeshMonitor (configured for localhost:4403)
docker-compose up
```

### Scenario 2: Docker Compose Integration
```yaml
version: '3.8'
services:
  ble-bridge:
    image: meshmonitor/ble-bridge
    privileged: true  # For BLE access
    environment:
      - BLE_ADDRESS=AA:BB:CC:DD:EE:FF
    ports:
      - "4403:4403"

  meshmonitor:
    image: ghcr.io/yeraze/meshmonitor
    depends_on:
      - ble-bridge
    environment:
      - MESHTASTIC_NODE_IP=ble-bridge
      - MESHTASTIC_NODE_PORT=4403
```

### Scenario 3: Systemd Service
```ini
[Unit]
Description=Meshtastic BLE-TCP Bridge
After=bluetooth.service

[Service]
ExecStart=/usr/bin/python3 /opt/ble-bridge/ble_tcp_bridge.py AA:BB:CC:DD:EE:FF
Restart=always
User=meshtastic

[Install]
WantedBy=multi-user.target
```

---

## Security Considerations

1. **BLE Pairing:** Meshtastic nodes may require pairing/bonding
2. **TCP Binding:** Always bind to localhost (127.0.0.1) unless explicitly needed
3. **Authentication:** Consider adding simple auth to TCP server if exposed
4. **BLE Range:** BLE typically 10-30m range (physical security)

---

## Testing Strategy

### Unit Tests
- TCP frame parsing/generation
- BLE characteristic mapping
- Error handling (disconnects, invalid frames)

### Integration Tests
- Connect to real Meshtastic device via BLE
- MeshMonitor connects to bridge via TCP
- Send/receive messages end-to-end
- Connection recovery after BLE disconnect
- Multiple TCP client connections

### Performance Tests
- Latency measurement (BLE → TCP → MeshMonitor)
- Throughput (messages per second)
- Memory usage over time
- CPU usage comparison (BLE vs TCP)

---

## Success Criteria

- [ ] Successfully discover and connect to Meshtastic node via BLE
- [ ] MeshMonitor connects to bridge and shows "Connected" status
- [ ] Messages sent from MeshMonitor appear on mesh
- [ ] Messages from mesh appear in MeshMonitor
- [ ] Automatic reconnection on BLE disconnect
- [ ] < 100ms latency overhead vs direct TCP
- [ ] Stable for 24+ hours continuous operation

---

## Conclusion

**Feasibility:** HIGH - Well-defined protocols, existing libraries
**Complexity:** MODERATE - Protocol translation, async I/O
**Value:** HIGH - Enables BLE-only Meshtastic devices, no serial/TCP needed

**Recommended Implementation:**
1. **Start with Python bridge** (2-3 days development)
2. Use official `meshtastic` library for proven BLE support
3. Deploy as standalone service or Docker container
4. If successful, consider Node.js port or direct integration

**Next Steps:**
1. Set up development environment with Python and meshtastic library
2. Test BLE connection to target Meshtastic device
3. Implement basic proof of concept (1 day)
4. Iterate based on testing results
