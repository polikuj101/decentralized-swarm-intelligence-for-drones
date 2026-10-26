#!/usr/bin/env bash
cd "/mnt/c/Users/nurbolatte/Desktop/Swarm of Drones" || exit 1
OUT=/tmp/gzexp4.txt; : > $OUT
rss(){ ps -eo rss,comm --sort=-rss | awk '$2=="ruby"{print int($1/1024); exit}'; }
measure(){ a=$(rss); ta=$(date +%s); sleep 180; b=$(rss); tb=$(date +%s); echo "$1: ${a}MB -> ${b}MB in $((tb-ta))s = $(( (b-a)*60/(tb-ta) )) MB/min" | tee -a $OUT; }
AG="drone-agent-1 drone-agent-2 drone-agent-3 drone-agent-4 drone-agent-5"; FC="fc-1 fc-2 fc-3 fc-4 fc-5"
docker compose stop $AG $FC simulator-core >/dev/null 2>&1
docker compose up -d --no-deps --force-recreate simulator-core >/dev/null 2>&1
for k in $(seq 1 60); do [ "$(docker inspect -f '{{.State.Health.Status}}' swarm-sitl-simulator-core-1 2>/dev/null)" = healthy ] && break; sleep 3; done
sleep 45; measure "S0 свежий simulator-core стенда, клиентов нет"
docker compose up -d --no-deps $FC >/dev/null 2>&1; sleep 60; measure "S1 + PX4 (fc), агентов нет"
docker compose up -d --no-deps $AG >/dev/null 2>&1; sleep 60; measure "S2 + агенты"
echo finished | tee -a $OUT
