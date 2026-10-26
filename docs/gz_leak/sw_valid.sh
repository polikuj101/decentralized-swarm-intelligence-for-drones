#!/usr/bin/env bash
cd "/mnt/c/Users/nurbolatte/Desktop/Swarm of Drones" || exit 1
V=/mnt/c/Users/nurbolatte/AppData/Local/Temp/claude/C--Users-nurbolatte-Desktop-Swarm-of-Drones/d4c840d1-de90-4638-be15-b56c8045aa02/scratchpad/gzvar
OUT=/tmp/sw_valid.txt; : > $OUT
docker compose stop drone-agent-1 drone-agent-2 drone-agent-3 drone-agent-4 drone-agent-5 fc-1 fc-2 fc-3 fc-4 fc-5 simulator-core >/dev/null 2>&1
rss(){ ps -eo rss,comm --sort=-rss | awk '$2=="ruby"{print int($1/1024); exit}'; }
try(){ name=$1; soft=$2; dev=$3; extra=$4
  docker rm -f gzexp >/dev/null 2>&1
  DEV=""; [ "$dev" = 1 ] && DEV="--device /dev/dxg:/dev/dxg --device /dev/dri:/dev/dri"
  docker run -d --rm --name gzexp --cpus 6 --memory 8g $DEV $extra \
   -e GZ_WORLD=swarm_world -e SWARM_SIZE=5 -e GZ_PARTITION=swarm -e LIBGL_ALWAYS_SOFTWARE=$soft -e DISPLAY=:0 \
   -e MESA_D3D12_DEFAULT_ADAPTER_NAME=NVIDIA -e LD_LIBRARY_PATH=/usr/lib/wsl/lib:/usr/lib/x86_64-linux-gnu \
   -v /usr/lib/wsl:/usr/lib/wsl:ro -v /tmp/.X11-unix:/tmp/.X11-unix:ro -v /mnt/wslg:/mnt/wslg:rw \
   -v "$V/gen_control.py:/opt/swarm/gen_swarm.py:ro" swarm/simulator-core:latest >/dev/null 2>&1
  for k in $(seq 1 40); do docker exec gzexp sh -c 'gz topic -l 2>/dev/null | grep -q "x500_swarm_5/link/base_link/sensor/imu_sensor/imu"' 2>/dev/null && break; sleep 3; done
  sleep 40
  for n in 1 2 3 4 5; do for t in cam_down/image lidar_ring/scan lidar_down/scan; do docker exec -d gzexp sh -c "gz topic -e -t /swarm/$n/$t > /dev/null 2>&1"; done; done
  sleep 20
  rate(){ docker exec gzexp bash -c "s=\$(date +%s.%N); timeout 30 gz topic -e -n 30 -t $1 >/dev/null 2>&1; e=\$(date +%s.%N); python3 -c \"print(round(30/(\$e-\$s),1))\"" 2>/dev/null; }
  camhz=$(rate /swarm/1/cam_down/image); lidhz=$(rate /swarm/1/lidar_ring/scan)
  errs=$(docker logs gzexp 2>&1 | grep -c "Unable to create the rendering window")
  rtf=$(docker exec gzexp sh -c 'timeout 8 gz topic -e -n 1 -t /stats 2>/dev/null' | awk '/real_time_factor/{print $2; exit}')
  cpu=$(docker stats --no-stream --format '{{.CPUPerc}}' gzexp)
  echo "$name | render_errors=$errs | rtf=$rtf | cam_hz=$camhz (задано 15) | lidar_hz=$lidhz (задано 10) | CPU=$cpu" | tee -a $OUT
}
try "software, без устройств /dev/dxg,/dev/dri" 1 0 ""
try "software + EGL_PLATFORM=surfaceless, без устройств" 1 0 "-e EGL_PLATFORM=surfaceless"
try "GPU (как раньше)" 0 1 ""
docker rm -f gzexp >/dev/null 2>&1
echo finished | tee -a $OUT
