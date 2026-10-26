#!/usr/bin/env bash
# Эксперимент VIO без GPS на стенде: чистый старт роя, приказ, все борты в воздухе -> GPS denied на DENY_S, журнал кадров VIO и поз Gazebo.
# Использование: exp_vio.sh <метка> [VIO_DEROT] [DENY_S]
LABEL=${1:-run}; export VIO_DEROT=${2:-0}; DENY_S=${3:-150}
export VIO_FRAME_LOG=/tmp/vio_frames.jsonl
PROJ="/mnt/c/Users/nurbolatte/Desktop/Swarm of Drones"
OUT=${OUT_DIR:-/tmp}/exp_$LABEL
mkdir -p "$OUT"; rm -f "$OUT"/*; LOG="$OUT/run.log"
say(){ echo "[$(date +%T)] $*" | tee -a "$LOG"; }
cd "$PROJ" || exit 1
touch logs/.watchdog_pause
say "label=$LABEL VIO_DEROT=$VIO_DEROT DENY_S=$DENY_S"
./scripts/swarm_fresh.sh 2>&1 | tee -a "$LOG"
for i in 1 2 3 4 5; do docker exec swarm-sitl-drone-agent-$i-1 sh -c 'rm -f /tmp/vio_frames.jsonl; supervisorctl restart vio >/dev/null; echo "D'$i' VIO_DEROT=$VIO_DEROT"'; done | tee -a "$LOG"
docker exec swarm-sitl-simulator-core-1 sh -c 'rm -f /tmp/poses.jsonl; supervisorctl status range-sim 2>&1; grep -o "range_sensors.*" /opt/swarm/scene/scene.json' | tee -a "$LOG"
docker cp docs/gz_leak/pose_log.py swarm-sitl-simulator-core-1:/tmp/pose_log.py
docker exec -d swarm-sitl-simulator-core-1 python3 /tmp/pose_log.py /tmp/poses.jsonl
sleep 3
python3 -c 'import json,sys; print(json.dumps({"prompt": "Обследовать все квадраты"}))' > /tmp/exp_task.json
for k in 1 2 3; do
  r=$(curl -s -m 90 -X POST http://127.0.0.1:8000/api/tasking -H "X-HUD: 1" -H 'Content-Type: application/json' -d @/tmp/exp_task.json)
  echo "$r" | python3 -c "import sys,json; r=json.load(sys.stdin); print('tasking ok', [x.get('ok') for x in r.get('results',[])])" 2>&1 | tee -a "$LOG"
  echo "$r" | grep -q '"ok":false' || break; sleep 15
done
# ждём, пока все пять в воздухе выше 2 м
for k in $(seq 1 60); do
  n=$(docker exec swarm-sitl-simulator-core-1 sh -c 'tail -1 /tmp/poses.jsonl' | python3 -c "import sys,json; p=json.loads(sys.stdin.read())['p']; print(sum(1 for v in p.values() if v[2] > 2.0))" 2>/dev/null)
  [ "${n:-0}" = 5 ] && break; sleep 3
done
say "airborne: ${n:-?}/5; GPS denied all for $DENY_S s"
sleep 15
./scripts/ewctl.sh gps denied all $DENY_S 2>&1 | head -c 300 | tee -a "$LOG"; echo >> "$LOG"
T_DENY=$(date +%s)
sleep $((DENY_S + 10))
say "collect"
for i in 1 2 3 4 5; do docker cp swarm-sitl-drone-agent-$i-1:/tmp/vio_frames.jsonl "$OUT/frames_$i.jsonl" 2>>"$LOG"; done
docker cp swarm-sitl-simulator-core-1:/tmp/poses.jsonl "$OUT/poses.jsonl" 2>>"$LOG"
docker exec swarm-sitl-simulator-core-1 sh -c 'pkill -f pose_log.py' 2>/dev/null
curl -s http://127.0.0.1:8000/api/state > "$OUT/hud_state.json"
python3 docs/gz_leak/vio_eval.py "$OUT/poses.jsonl" "$OUT"/frames_*.jsonl 2>&1 | tee -a "$LOG"
./scripts/ewctl.sh gps restore all >/dev/null 2>&1
rm -f logs/.watchdog_pause
say "done"
