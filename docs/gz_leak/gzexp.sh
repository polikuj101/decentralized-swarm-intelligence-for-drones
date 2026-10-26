#!/usr/bin/env bash
cd "/mnt/c/Users/nurbolatte/Desktop/Swarm of Drones" || exit 1
V=/mnt/c/Users/nurbolatte/AppData/Local/Temp/claude/C--Users-nurbolatte-Desktop-Swarm-of-Drones/d4c840d1-de90-4638-be15-b56c8045aa02/scratchpad/gzvar
V=$(cd "$V" && pwd)
OUT=/tmp/gzexp.txt; : > $OUT
echo "stopping stand parts" | tee -a $OUT
docker compose stop drone-agent-1 drone-agent-2 drone-agent-3 drone-agent-4 drone-agent-5 fc-1 fc-2 fc-3 fc-4 fc-5 simulator-core >/dev/null 2>&1
rss(){ ps -eo rss,comm --sort=-rss | awk '$2=="ruby"{print int($1/1024); exit}'; }
run() { # name gen env_software gpu
  name=$1; gen=$2; soft=$3; gpu=$4
  docker rm -f gzexp >/dev/null 2>&1
  DEV=""; [ "$gpu" = 1 ] && DEV="--device /dev/dxg:/dev/dxg --device /dev/dri:/dev/dri"
  docker run -d --rm --name gzexp --cpus 6 --memory 8g $DEV \
    -e GZ_WORLD=swarm_world -e SWARM_SIZE=5 -e GZ_PARTITION=swarm -e LIBGL_ALWAYS_SOFTWARE=$soft -e DISPLAY=:0 \
    -e MESA_D3D12_DEFAULT_ADAPTER_NAME=NVIDIA -e LD_LIBRARY_PATH=/usr/lib/wsl/lib:/usr/lib/x86_64-linux-gnu \
    -v /usr/lib/wsl:/usr/lib/wsl:ro -v /tmp/.X11-unix:/tmp/.X11-unix:ro -v /mnt/wslg:/mnt/wslg:rw \
    -v "$V/$gen:/opt/swarm/gen_swarm.py:ro" swarm/simulator-core:latest >/dev/null 2>&1
  for k in $(seq 1 40); do docker exec gzexp sh -c 'gz topic -l 2>/dev/null | grep -q "x500_swarm_5/link/base_link/sensor/imu_sensor/imu"' 2>/dev/null && break; sleep 3; done
  sleep 45
  a=$(rss); ta=$(date +%s)
  sleep 150
  b=$(rss); tb=$(date +%s)
  rate=$(( (b-a)*60/(tb-ta) ))
  cpu=$(docker stats --no-stream --format '{{.CPUPerc}}' gzexp 2>/dev/null)
  echo "$name: ${a}MB -> ${b}MB in $((tb-ta))s = ${rate} MB/min (container CPU $cpu)" | tee -a $OUT
  docker rm -f gzexp >/dev/null 2>&1
}
run control gen_control.py 0 1
run software gen_control.py 1 0
run lowrate gen_lowrate.py 0 1
run nocam gen_nocam.py 0 1
run nolidar gen_nolidar.py 0 1
run nosensors gen_nosensors.py 0 1
echo "restoring stand" | tee -a $OUT
docker compose up -d >/dev/null 2>&1
echo done | tee -a $OUT
