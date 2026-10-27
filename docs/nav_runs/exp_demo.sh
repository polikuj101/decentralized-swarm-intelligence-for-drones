#!/usr/bin/env bash
# Прогон демо так, как его покажут: swarm_fresh.sh, затем DEMO_AUTOPILOT=1 run_demo.sh (РЭБ сценария 05, поражение борта 2), сторож
# запущен. Сверка с позами Gazebo, а не с HUD: высоты бортов, ошибка EKF без GPS (vio_eval.py), ложные тревоги подмены GPS, карточки BDA.
# Использование: exp_demo.sh <метка> [секунд наблюдения после таймлайна]
LABEL=${1:-demo}; AFTER_S=${2:-90}
export VIO_FRAME_LOG=/tmp/vio_frames.jsonl
export MANIFEST_WAIT_S=${MANIFEST_WAIT_S:-900}   # как на показе: рой ждёт приказа, а не уходит на все сектора через 90 с
PROJ="/mnt/c/Users/nurbolatte/Desktop/Swarm of Drones"
SIM=swarm-sitl-simulator-core-1
OUT=${OUT_DIR:-/tmp}/exp_$LABEL
mkdir -p "$OUT"; rm -f "$OUT"/*; LOG="$OUT/run.log"
say(){ echo "[$(date +%T)] $*" | tee -a "$LOG"; }
cd "$PROJ" || exit 1
touch logs/.watchdog_pause            # сторож не трогает борты, пока swarm_fresh их поднимает
say "label=$LABEL"
./scripts/swarm_fresh.sh 2>&1 | tee -a "$LOG"
# vio_node держит журнал открытым: после rm без перезапуска он пишет в удалённый файл
for i in 1 2 3 4 5; do docker exec swarm-sitl-drone-agent-$i-1 sh -c 'rm -f /tmp/vio_frames.jsonl; supervisorctl restart vio >/dev/null'; done
docker cp docs/gz_leak/pose_log.py $SIM:/tmp/pose_log.py
docker exec $SIM sh -c "pkill -f '[p]ose_log.py'; rm -f /tmp/poses.jsonl"
docker exec -d $SIM python3 /tmp/pose_log.py /tmp/poses.jsonl
EV0=$(curl -s http://127.0.0.1:8000/api/evidence | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null)
T0=$(date +%s)
rm -f logs/.watchdog_pause
say "run_demo.sh (карточек BDA до прогона: ${EV0:-?})"
DEMO_AUTOPILOT=1 ./scripts/run_demo.sh 2>&1 | tee -a "$LOG"
RC=${PIPESTATUS[0]}
say "run_demo rc=$RC; наблюдение ещё $AFTER_S с"
sleep "$AFTER_S"
say "collect"
for i in 1 2 3 4 5; do docker cp swarm-sitl-drone-agent-$i-1:/tmp/vio_frames.jsonl "$OUT/frames_$i.jsonl" 2>>"$LOG"; done
docker exec $SIM sh -c "pkill -f '[p]ose_log.py'"
docker cp $SIM:/tmp/poses.jsonl "$OUT/poses.jsonl" 2>>"$LOG"
curl -s http://127.0.0.1:8000/api/state > "$OUT/hud_state.json"
curl -s http://127.0.0.1:8000/api/evidence > "$OUT/evidence.json"
SINCE="$(( $(date +%s) - T0 + 30 ))s"
for i in 1 2 3 4 5; do
  docker logs --since "$SINCE" swarm-sitl-drone-agent-$i-1 2>&1 | grep -E "GPS SUSPECT|GPS OK|suspect cleared" | sed "s/^/D$i /" >> "$OUT/suspect.log"
done
say "тревоги подмены GPS (подмены в сценарии нет, любая — ложная): $(wc -l < "$OUT/suspect.log")"
cat "$OUT/suspect.log" | tee -a "$LOG"
python3 - "$OUT" "${EV0:-0}" <<'PY' 2>&1 | tee -a "$LOG"
import json, sys
out, ev0 = sys.argv[1], int(sys.argv[2])
last = None
for l in open(f"{out}/poses.jsonl"):
    if l.startswith("{"): last = json.loads(l)
print("Gazebo, высоты в конце:", {k: round(v[2], 1) for k, v in sorted(last["p"].items())} if last else "нет поз")
s = json.load(open(f"{out}/hud_state.json"))
for k, d in sorted(s["drones"].items()):
    n = ((d.get("pkt") or {}).get("nav") or {})
    print(f"  HUD D{k}: state={n.get('state')} src={n.get('src')} z={n.get('z')} alive={bool(d.get('pkt'))}")
ev = json.load(open(f"{out}/evidence.json"))
new = ev[ev0:] if isinstance(ev, list) else []
print(f"карточки BDA за прогон: {len(new)}", [(c.get('drone'), c.get('target_id'), (c.get('detection') or {}).get('tone'), c.get('nav_source')) for c in new][:12])
PY
python3 docs/gz_leak/vio_eval.py "$OUT/poses.jsonl" "$OUT"/frames_*.jsonl 2>&1 | grep -E "^D|без GPS|^  кадры" | tee -a "$LOG"
say "gz-sim: $(docker exec $SIM supervisorctl status gz-sim | awk '{print $NF, $(NF-1)}'), OOM в dmesg: $(dmesg 2>/dev/null | grep -c 'Out of memory')"
say "сторож: $(tail -3 logs/gz_watchdog.log 2>/dev/null | tr '\n' ' ')"
./scripts/ewctl.sh reset >/dev/null 2>&1
docker start swarm-sitl-drone-agent-2-1 >/dev/null 2>&1
say "done"
