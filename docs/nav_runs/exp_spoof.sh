#!/usr/bin/env bash
# Живая проверка детектора подмены GPS (HANDOFF п. 3): рой в полёте, дрейф GPS 2 м/с на D1 и D2 на DRIFT_S секунд.
# Пишет nav_status из HUD раз в секунду и позы Gazebo; затем время до подозрения и ошибку оценки позиции.
LABEL=${1:-spoof}; DRIFT_S=${2:-90}
PROJ="/mnt/c/Users/nurbolatte/Desktop/Swarm of Drones"
OUT=${OUT_DIR:-/tmp}/exp_$LABEL
mkdir -p "$OUT"; rm -f "$OUT"/*; LOG="$OUT/run.log"
say(){ echo "[$(date +%T)] $*" | tee -a "$LOG"; }
cd "$PROJ" || exit 1
TOKEN=$(sed -n 's/^EW_TOKEN=//p' .env | tr -d '"\r')
touch logs/.watchdog_pause
./scripts/swarm_fresh.sh 2>&1 | tee -a "$LOG"
docker exec swarm-sitl-simulator-core-1 sh -c 'rm -f /tmp/poses.jsonl'
docker cp docs/gz_leak/pose_log.py swarm-sitl-simulator-core-1:/tmp/pose_log.py
docker exec -d swarm-sitl-simulator-core-1 python3 /tmp/pose_log.py /tmp/poses.jsonl
python3 -c 'import json; print(json.dumps({"prompt": "Обследовать все квадраты"}))' > /tmp/exp_task.json
for k in 1 2 3; do
  r=$(curl -s -m 90 -X POST http://127.0.0.1:8000/api/tasking -H "X-HUD: 1" -H 'Content-Type: application/json' -d @/tmp/exp_task.json)
  echo "$r" | grep -q '"ok":false' || break; sleep 15
done
for k in $(seq 1 60); do
  n=$(docker exec swarm-sitl-simulator-core-1 sh -c 'tail -1 /tmp/poses.jsonl' | python3 -c "import sys,json; p=json.loads(sys.stdin.read())['p']; print(sum(1 for v in p.values() if v[2] > 2.0))" 2>/dev/null)
  [ "${n:-0}" = 5 ] && break; sleep 3
done
say "airborne ${n:-?}/5"
# журнал nav_status из HUD
( for k in $(seq 1 $((DRIFT_S + 90))); do curl -s -m 2 http://127.0.0.1:8000/api/state | python3 -c "
import sys, json, time
s = json.load(sys.stdin)
print(json.dumps({'t': time.time(), 'nav': {i: (d.get('pkt') or {}).get('nav') for i, d in s['drones'].items()}}))" >> "$OUT/nav.jsonl" 2>/dev/null; sleep 1; done ) &
NAVPID=$!
sleep 20
T0=$(date +%s.%N)
say "drift 2 m/s heading 45 on D1,D2 for $DRIFT_S s"
curl -fsS -X POST http://127.0.0.1:8080/v1/ew/gps -H 'Content-Type: application/json' -H "Authorization: Bearer $TOKEN" \
  -d "{\"mode\": \"drift\", \"drones\": [1, 2], \"rate_mps\": 2.0, \"heading_deg\": 45, \"sigma_m\": 0.0, \"duration_s\": $DRIFT_S}" | head -c 200 | tee -a "$LOG"; echo >> "$LOG"
echo "$T0" > "$OUT/t_drift"
wait $NAVPID
docker cp swarm-sitl-simulator-core-1:/tmp/poses.jsonl "$OUT/poses.jsonl"
docker exec swarm-sitl-simulator-core-1 sh -c 'pkill -f pose_log.py'
for i in 1 2 3 4 5; do docker logs --since 15m swarm-sitl-drone-agent-$i-1 2>&1 | grep -E "GPS SUSPECT|GPS consistent" | sed "s/^/D$i /" >> "$OUT/suspect.log"; done
curl -fsS -X POST http://127.0.0.1:8080/v1/ew/restore -H 'Content-Type: application/json' -H "Authorization: Bearer $TOKEN" -d '{"channel": "gps"}' >/dev/null
rm -f logs/.watchdog_pause
python3 - "$OUT" <<'PY' | tee -a "$LOG"
import re, sys
d = sys.argv[1]
t0 = float(open(f"{d}/t_drift").read())
ev = {}
for line in open(f"{d}/suspect.log"):
    m = re.match(r"D(\d) \[\w+\] \[(\d+\.\d+)\].*?(GPS SUSPECT|GPS consistent)", line)
    if m:
        ev.setdefault(m.group(1), []).append((float(m.group(2)) - t0, "тревога" if m.group(3) == "GPS SUSPECT" else "снято"))
for i in "12345":
    e = ev.get(i, [])
    kind = "дрейф 2 м/с" if i in "12" else "без подмены"
    print(f"D{i} ({kind}): " + ("; ".join(f"{what} t{t:+.1f} с" for t, what in e) if e else "тревог нет"))
PY
say "done"
