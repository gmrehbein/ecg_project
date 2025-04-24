#!/bin/bash
set -e

echo "🧹 Stopping ECG containers..."

# Stop by container name
docker stop app signal_processing ecg_simulator 2>/dev/null || true

# Remove any leftover network
docker network rm test-project-network 2>/dev/null || true

# Optionally prune unused volumes, images, and containers
docker system prune -f --volumes

echo "✅ All containers stopped and cleaned up."
