#!/usr/bin/env bash
cd "/mnt/c/Users/nurbolatte/Desktop/Swarm of Drones" || exit 1
V=/mnt/c/Users/nurbolatte/AppData/Local/Temp/claude/C--Users-nurbolatte-Desktop-Swarm-of-Drones/d4c840d1-de90-4638-be15-b56c8045aa02/scratchpad/gzvar
OUT=/tmp/mesa.txt; : > $OUT
docker compose stop drone-agent-1 drone-agent-2 drone-agent-3 drone-agent-4 drone-agent-5 fc-1 fc-2 fc-3 fc-4 fc-5 simulator-core >/dev/null 2>&1
rss(){ ps -eo rss,comm --sort=-rss | awk '$2=="ruby"{print int($1/1024); exit}'; }
docker rm -f gzexp >/dev/null 2>&1
docker run -d --name gzexp --cpus 6 --memory 8g --device /dev/dxg:/dev/dxg --device /dev/dri:/dev/dri --entrypoint sleep \
 -e GZ_WORLD=swarm_world -e SWARM_SIZE=5 -e GZ_PARTITION=swarm -e LIBGL_ALWAYS_SOFTWARE=0 -e DISPLAY=:0 \
 -e MESA_D3D12_DEFAULT_ADAPTER_NAME=NVIDIA -e LD_LIBRARY_PATH=/usr/lib/wsl/lib:/usr/lib/x86_64-linux-gnu \
 -v /usr/lib/wsl:/usr/lib/wsl:ro -v /tmp/.X11-unix:/tmp/.X11-unix:ro -v /mnt/wslg:/mnt/wslg:rw \
 -v "$V/gen_control.py:/opt/swarm/gen_swarm.py:ro" swarm/simulator-core:latest infinity >/dev/null
echo "Mesa до: $(docker exec gzexp dpkg-query -W -f='${Version}' libegl-mesa0)" | tee -a $OUT
docker exec gzexp bash -c "apt-get update -qq && apt-get install -y -qq software-properties-common >/dev/null && add-apt-repository -y ppa:kisak/kisak-mesa >/dev/null 2>&1 && apt-get update -qq && apt-get -y -qq dist-upgrade >/dev/null 2>&1; echo apt-exit=\$?" 2>&1 | tail -2 | tee -a $OUT
echo "Mesa после: $(docker exec gzexp dpkg-query -W -f='${Version}' libegl-mesa0)" | tee -a $OUT
docker exec -d gzexp /usr/local/bin/sim-entrypoint.sh
for k in $(seq 1 60); do docker exec gzexp sh -c 'gz topic -l 2>/dev/null | grep -q "x500_swarm_5/link/base_link/sensor/imu_sensor/imu"' 2>/dev/null && break; sleep 3; done
sleep 30
for n in 1 2 3 4 5; do for t in cam_down/image lidar_ring/scan lidar_down/scan; do docker exec -d gzexp sh -c "gz topic -e -t /swarm/$n/$t > /dev/null 2>&1"; done; done
sleep 45
a=$(rss); ta=$(date +%s); sleep 150; b=$(rss); tb=$(date +%s)
echo "новая Mesa + все подписчики: ${a}MB -> ${b}MB = $(( (b-a)*60/(tb-ta) )) MB/min (контроль со старой Mesa: 133 МБ/мин)" | tee -a $OUT
docker exec gzexp bash -c "docker 2>/dev/null; grep -c 'Unable to create the rendering window' /proc/1/fd/1 2>/dev/null" >/dev/null 2>&1
docker rm -f gzexp >/dev/null 2>&1
docker compose up -d >/dev/null 2>&1; echo restored | tee -a $OUT
