#!/usr/bin/env bash
# Частоты сенсоров при 15 подписчиках: gpu_lidar против аналитических дальномеров (range_sim). Отдельный контейнер, стенд не трогается.
PROJ="/mnt/c/Users/nurbolatte/Desktop/Swarm of Drones"
SP=${OUT_DIR:-/tmp}
OUT=$SP/rates_cmp.log; : > "$OUT"
C=gzrate
X(){ docker exec $C bash -c "$1"; }
run(){ # mode
  docker rm -f $C >/dev/null 2>&1
  docker run -d --name $C --cpus 6 --memory 8g --entrypoint sleep \
    --device /dev/dxg:/dev/dxg --device /dev/dri:/dev/dri \
    -e GZ_WORLD=swarm_world -e SWARM_SIZE=5 -e SWARM_SPACING_M=5.0 -e GZ_PARTITION=rates -e RANGE_SENSORS=$1 \
    -e LIBGL_ALWAYS_SOFTWARE=0 -e DISPLAY=:0 -e MESA_D3D12_DEFAULT_ADAPTER_NAME=NVIDIA \
    -e LD_LIBRARY_PATH=/usr/lib/wsl/lib:/usr/lib/x86_64-linux-gnu \
    -v /usr/lib/wsl:/usr/lib/wsl:ro -v /tmp/.X11-unix:/tmp/.X11-unix:ro -v /mnt/wslg:/mnt/wslg:rw \
    -v "$PROJ/docker/simulator-core/gen_swarm.py:/opt/swarm/gen_swarm.py:ro" -v "$PROJ/docker/simulator-core/range_sim.py:/opt/range_sim.py:ro" \
    swarm/simulator-core:latest infinity >/dev/null
  X 'mkdir -p /opt/swarm/generated /opt/swarm/scene && python3 /opt/swarm/gen_swarm.py >/dev/null 2>&1'
  docker exec -d $C bash -c "export GZ_SIM_RESOURCE_PATH=/opt/px4-gz/models:/opt/swarm/generated/models; gz sim -s -r --headless-rendering -v 2 /opt/swarm/generated/swarm_world.sdf > /tmp/gz.log 2>&1"
  for k in $(seq 1 60); do X 'gz topic -l 2>/dev/null | grep -q "x500_swarm_5/link/base_link/sensor/imu_sensor/imu"' 2>/dev/null && break; sleep 3; done
  docker exec -d $C bash -c "python3 /opt/range_sim.py > /tmp/rs.log 2>&1"
  sleep 15
  for n in 1 2 3 4 5; do for t in cam_down/image lidar_ring/scan lidar_down/scan; do
    docker exec -d $C sh -c "gz topic -e -t /swarm/$n/$t > /dev/null 2>&1"; done; done
  sleep 40
  echo "== RANGE_SENSORS=$1" | tee -a "$OUT"
  for t in cam_down/image lidar_ring/scan lidar_down/scan; do
    c=$(X "timeout 20 gz topic -e -t /swarm/3/$t 2>/dev/null | grep -c '^header'"); echo "  /swarm/3/$t: $(awk "BEGIN{print $c/20}") Hz" | tee -a "$OUT"
  done
  X "timeout 12 gz topic -e -t /stats 2>/dev/null | grep real_time_factor | tail -3 | tr '\n' ' '" | tee -a "$OUT"; echo | tee -a "$OUT"
  X "top -b -n1 | grep -E 'ruby|python3' | head -4" | tee -a "$OUT"
  docker rm -f $C >/dev/null 2>&1
}
run gpu
run analytic
