#!/usr/bin/env bash
# swarm_fresh.sh — чистый перезапуск бортов (PX4 и агентов) в правильном порядке и ожидание связи.
# Порядок важен: сначала PX4, потом агенты. Перезапуск ТОЛЬКО агента (при живом PX4) оставляет у PX4 протухшую сессию DDS:
# «connected», но Payload tx = 0 B/s, борт не взлетает (живые прогоны 2026-10-26, D1 и D5).
# Перезапуск PX4 посреди полёта тоже нельзя: борт разоружается, а локальная система координат PX4 сбрасывается.
# Мир Gazebo тоже перезапускается: упавшие/перевёрнутые модели (после аварий и тестов) остаются лежать, и PX4 отказывается взводиться
# («Preflight Fail: Attitude failure (pitch)», репетиция демо 2026-10-26: все 5 бортов лежали на боку или вверх колёсами).
# Использование: ./scripts/swarm_fresh.sh [--task "Обследовать все квадраты"]
set -uo pipefail
cd "$(dirname "$0")/.."
TASK=""
[ "${1:-}" = "--task" ] && TASK="${2:-}"
AG="drone-agent-1 drone-agent-2 drone-agent-3 drone-agent-4 drone-agent-5"
FC="fc-1 fc-2 fc-3 fc-4 fc-5"
say(){ echo "$(date +%T) $*"; }
say "остановка агентов и PX4"; docker compose stop $AG $FC >/dev/null 2>&1
# Приказ хранится в томе агента и переживает перезапуск: с ним рой взлетал сам через ~12 с после старта, до приказа оператора, и повторный
# приказ «Браво» становился пустым («сектора те же») — репетиции демо 2026-10-27. Чистый старт = рой на земле ждёт приказа.
# (gz_watchdog.py приказ, наоборот, сохраняет: после его перезапуска миссия продолжается.)
say "сброс сохранённого приказа"
for i in 1 2 3 4 5; do
  vol=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/opt/swarm/state"}}{{.Name}}{{end}}{{end}}' swarm-sitl-drone-agent-$i-1 2>/dev/null)
  [ -n "$vol" ] && docker run --rm --entrypoint rm -v "$vol":/s swarm/drone-agent:latest -f /s/manifest.json /s/manifest.json.tmp
done
say "перезапуск мира Gazebo (модели возвращаются в точки старта)"
docker compose restart simulator-core >/dev/null 2>&1
for k in $(seq 1 60); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' swarm-sitl-simulator-core-1 2>/dev/null)" = healthy ] && break; sleep 3
done
say "запуск PX4"; docker compose up -d --no-deps $FC >/dev/null 2>&1; sleep 25
say "запуск агентов"; docker compose up -d --no-deps $AG >/dev/null 2>&1
for k in $(seq 1 40); do
  n=$(curl -s -m 5 http://127.0.0.1:8000/api/state | python3 -c "import sys,json;s=json.load(sys.stdin);print(sum(1 for d in s['drones'].values() if d.get('pkt')))" 2>/dev/null)
  [ "${n:-0}" = 5 ] && break; sleep 5
done
sleep 20
bad=0
for i in 1 2 3 4 5; do
  tx=$(docker exec swarm-sitl-fc-$i-1 px4-shell uxrce_dds_client status 2>&1 | awk '/Payload tx/{print $5}')
  say "fc-$i Payload tx = ${tx:-?} B/s"; [ "${tx:-0}" = 0 ] && bad=1
done
if [ $bad = 1 ]; then say "ВНИМАНИЕ: у части бортов нет обмена PX4-агент; повторите скрипт или перезапустите такой борт целиком (PX4, затем агент)"; fi
# У PX4 одного борта после старта может пропасть поток магнитометра и GPS из Gazebo (репетиция 2026-10-27, D5): положения нет, борт не взлетит.
say "проверка положения ekf2 у всех пяти (при необходимости перезапуск PX4 и агента борта)"
bash scripts/px4_position_check.sh --fix || bad=1
if [ -n "$TASK" ]; then
  python3 -c 'import json,sys; print(json.dumps({"prompt": sys.argv[1]}))' "$TASK" > /tmp/swarm_fresh_task.json
  curl -s -m 90 -X POST http://127.0.0.1:8000/api/tasking -H "X-HUD: 1" -H 'Content-Type: application/json' -d @/tmp/swarm_fresh_task.json >/dev/null && say "приказ отправлен"
fi
exit $bad
