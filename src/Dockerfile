FROM python:3.15-rc-alpine3.22

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

# Default command (can be overridden with BLE_ADDRESS env var)
ENTRYPOINT ["python", "-m", "cli.main"]
CMD ["--help"]
