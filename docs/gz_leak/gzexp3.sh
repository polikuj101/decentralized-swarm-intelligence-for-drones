#!/usr/bin/env bash
cd "/mnt/c/Users/nurbolatte/Desktop/Swarm of Drones" || exit 1
V=/mnt/c/Users/nurbolatte/AppData/Local/Temp/claude/C--Users-nurbolatte-Desktop-Swarm-of-Drones/d4c840d1-de90-4638-be15-b56c8045aa02/scratchpad/gzvar
OUT=/tmp/gzexp3.txt; : > $OUT
docker compose stop drone-agent-1 drone-agent-2 drone-agent-3 drone-agent-4 drone-agent-5 fc-1 fc-2 fc-3 fc-4 fc-5 simulator-core >/dev/null 2>&1
rss(){ ps -eo rss,comm --sort=-rss | awk '$2=="ruby"{print int($1/1024); exit}'; }
start(){ docker rm -f gzexp >/dev/null 2>&1
  docker run -d --rm --name gzexp --cpus 6 --memory 8g --device /dev/dxg:/dev/dxg --device /dev/dri:/dev/dri \
   -e GZ_WORLD=swarm_world -e SWARM_SIZE=5 -e GZ_PARTITION=swarm -e LIBGL_ALWAYS_SOFTWARE=0 -e DISPLAY=:0 \
   -e MESA_D3D12_DEFAULT_ADAPTER_NAME=NVIDIA -e LD_LIBRARY_PATH=/usr/lib/wsl/lib:/usr/lib/x86_64-linux-gnu \
   -v /usr/lib/wsl:/usr/lib/wsl:ro -v /tmp/.X11-unix:/tmp/.X11-unix:ro -v /mnt/wslg:/mnt/wslg:rw \
   -v "$V/gen_control.py:/opt/swarm/gen_swarm.py:ro" swarm/simulator-core:latest >/dev/null 2>&1
  for k in $(seq 1 40); do docker exec gzexp sh -c 'gz topic -l 2>/dev/null | grep -q "x500_swarm_5/link/base_link/sensor/imu_sensor/imu"' 2>/dev/null && break; sleep 3; done
  sleep 45; }
measure(){ a=$(rss); ta=$(date +%s); sleep 150; b=$(rss); tb=$(date +%s); echo "$1: ${a}MB -> ${b}MB in $((tb-ta))s = $(( (b-a)*60/(tb-ta) )) MB/min" | tee -a $OUT; }
# 1: тот же контейнер, но с «healthcheck»: gz topic -l каждые 5 с, как в compose
start
( while true; do docker exec gzexp sh -c 'gz topic -l >/dev/null 2>&1'; sleep 5; done ) & LOOP=$!
measure "gz topic -l каждые 5 с"
kill $LOOP 2>/dev/null; wait $LOOP 2>/dev/null
# 2: после остановки цикла — растёт ли дальше (память могла накопиться, но рост должен прекратиться)
measure "тот же контейнер, цикл остановлен"
docker rm -f gzexp >/dev/null 2>&1
# 3: каждые 60 с
start
( while true; do docker exec gzexp sh -c 'gz topic -l >/dev/null 2>&1'; sleep 60; done ) & LOOP=$!
measure "gz topic -l каждые 60 с"
kill $LOOP 2>/dev/null; wait $LOOP 2>/dev/null
docker rm -f gzexp >/dev/null 2>&1
docker compose up -d >/dev/null 2>&1; echo restored | tee -a $OUT
