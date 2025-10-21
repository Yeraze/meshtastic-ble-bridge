FROM python:3.11-slim

LABEL org.opencontainers.image.title="MeshMonitor BLE Bridge"
LABEL org.opencontainers.image.description="Bluetooth Low Energy to TCP bridge for Meshtastic devices"
LABEL org.opencontainers.image.source="https://github.com/Yeraze/meshmonitor"

# Install system dependencies for BLE
RUN apt-get update && apt-get install -y \
    bluetooth \
    bluez \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Python dependencies
RUN pip install --no-cache-dir \
    meshtastic==2.3.12 \
    bleak==0.21.1

# Copy bridge script
COPY ble_tcp_bridge.py /app/

# Expose TCP port
EXPOSE 4403

# Default command (can be overridden with BLE_ADDRESS env var)
ENTRYPOINT ["python", "ble_tcp_bridge.py"]
CMD ["--help"]
