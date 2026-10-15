#!/bin/bash
set -e

echo "Testing Gazebo with WSL2 GPU mounts..."
docker run --rm --gpus all \
  --device=/dev/dxg \
  --device=/dev/dri \
  -v /usr/lib/wsl/lib:/usr/lib/wsl/lib:ro \
  -e LD_LIBRARY_PATH=/usr/lib/wsl/lib \
  -e LIBGL_ALWAYS_SOFTWARE=0 \
  -e MESA_D3D12_DEFAULT_ADAPTER_NAME=NVIDIA \
  --entrypoint bash \
  swarm/simulator-core:latest \
  -c 'gz sim -s -r --headless-rendering -v 3 /opt/swarm/generated/swarm_world.sdf &
pid=$!
sleep 5
echo "=== Loaded drivers in Gazebo process ==="
grep -E "(d3d12|nvidia|swrast)" /proc/$pid/maps | sort -u || echo "No matches"
kill -9 $pid 2>/dev/null || true'
