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

# Copy requirements and install dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy bridge source code
COPY core/ /app/core/
COPY cli/ /app/cli/

# Expose TCP port
EXPOSE 4403

# The bridge touches this file every 10s while BLE is connected. A stale file
# means the BLE link is down (TCP alone can still be accepting connections).
ENV HEALTH_FILE=/tmp/ble-bridge.health
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python3 -c "import os, sys, time; f = os.environ['HEALTH_FILE']; sys.exit(0 if os.path.exists(f) and time.time() - os.path.getmtime(f) < 60 else 1)"

# Default command (can be overridden with BLE_ADDRESS env var)
ENTRYPOINT ["python", "-m", "cli.main"]
CMD ["--help"]
