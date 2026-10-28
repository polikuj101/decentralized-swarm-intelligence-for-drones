#!/usr/bin/env bash
# run_demo.sh — запуск демо-таймлайна в один клик. ПРЕДПОЛАГАЕТ, что стенд уже поднят и прогрет
# (make up + ожидание готовности выполнены заранее, за 15-20 минут до встречи — см. D7, п. 9.1).
set -euo pipefail
cd "$(dirname "$0")/.."
AUTOPILOT="${DEMO_AUTOPILOT:-0}"
PROMPT="${DEMO_PROMPT:-Разведать квадрат Браво, вскрыть позиции, при обнаружении целей — доразведка и зависание}"
KILL_AT_S="${DEMO_KILL_AT_S:-100}"
KILL_DRONE="${DEMO_KILL_DRONE:-2}"
KILL_DRONES="${DEMO_KILL_DRONES:-$KILL_DRONE}"      # список через пробел, например "2 3 4"
SCENARIO="${DEMO_SCENARIO:-05_investor_demo}"
RF_AT_S="${DEMO_RF_AT_S:-25}"
TAKEOFF_TIMEOUT_S="${DEMO_TAKEOFF_TIMEOUT_S:-240}"

echo "[1/4] сброс РЭБ и проверка живости бортов"
./scripts/ewctl.sh reset >/dev/null 2>&1 || true
for i in 1 2 3 4 5; do
  docker ps --format '{{.Names}}' | grep -q "drone-agent-$i" \
    || docker start "swarm-sitl-drone-agent-$i-1" >/dev/null 2>&1 || true
done
curl -fsS http://127.0.0.1:8000/healthz >/dev/null || { echo "HUD не отвечает — проверьте 'make up'"; exit 1; }
echo "ok"

echo "[1б] связь PX4 с агентами (Payload tx > 0 у всех пяти)"
for k in $(seq 1 24); do
  bad=0
  for i in 1 2 3 4 5; do
    tx=$(docker exec "swarm-sitl-fc-$i-1" px4-shell uxrce_dds_client status 2>&1 | awk '/Payload tx/{print $5}')
    [ "${tx:-0}" = 0 ] && bad=1
  done
  [ $bad = 0 ] && break; sleep 5
done
[ $bad = 0 ] || { echo "нет обмена PX4-агент у части бортов: ./scripts/swarm_fresh.sh и повторите"; exit 1; }
echo "ok"

# Рой после старта взлетает сам и висит, ожидая приказа, но не дольше 90 с (coordinator.manifest_wait_timeout_s): потом он без приказа
# берёт все сектора, и приказ «Браво» уже ничего не меняет (репетиции 2026-10-27). Приказ надо дать в течение 90 с после swarm_fresh.sh,
# либо на время показа задать MANIFEST_WAIT_S=900 в .env (swarm_fresh.sh пересоздаст агентов с ним).
for i in 1 2 3 4 5; do
  c="swarm-sitl-drone-agent-$i-1"
  if docker logs --since "$(docker inspect -f '{{.State.StartedAt}}' "$c")" "$c" 2>&1 | grep -q "manifest timeout"; then
    echo "борт $i не дождался приказа и ушёл на все сектора: ./scripts/swarm_fresh.sh и сразу ./scripts/run_demo.sh"; exit 1
  fi
done

echo "[2/4] приказ оператора (pre-flight, C2 ещё жив)"
if [ "$AUTOPILOT" = 1 ]; then
  TOKEN=$(grep ^EW_TOKEN .env 2>/dev/null | cut -d= -f2 || echo "")
  payload=$(python3 -c 'import json,sys; print(json.dumps({"prompt": sys.argv[1]}))' "$PROMPT")
  curl -fsS -X POST http://127.0.0.1:8000/api/tasking -H "X-HUD: 1" -H 'Content-Type: application/json' \
    -d "$payload"
  echo
else
  echo "LIVE-режим: введите приказ в поле Web-HUD (http://127.0.0.1:8000) и нажмите «Отправить приказ»."
  read -rp "Enter — когда манифест скомпилирован и рой взлетел..." _
fi

# Таймлайн отсчитывается от взлёта роя, а не от приказа: на стенде взлёт занимает 1-2 минуты, и РЭБ «на 25-й секунде» приходился на землю
# (отказ GPS до взлёта не даёт PX4 взвестись, рой не взлетал вообще — репетиция 2026-10-26).
# Нужны все 5: борт, у которого PX4 остался без положения (репетиция 2026-10-27, D5), не взлетает, а 4 бортов раньше засчитывались.
MIN_AIRBORNE="${DEMO_MIN_AIRBORNE:-5}"
echo "ожидание взлёта: не менее ${MIN_AIRBORNE} бортов в воздухе (до ${TAKEOFF_TIMEOUT_S}с)"
airborne=0
for k in $(seq 1 $((TAKEOFF_TIMEOUT_S / 5))); do
  airborne=$(curl -s -m 5 http://127.0.0.1:8000/api/state | python3 -c "
import sys, json
s = json.load(sys.stdin); c = 0
for d in s['drones'].values():
    n = ((d.get('pkt') or {}).get('nav') or {})
    if n.get('state') in ('NAV', 'HOLD', 'DWELL') and (n.get('z') or 0) > 1.0: c += 1
print(c)" 2>/dev/null)
  [ "${airborne:-0}" -ge "$MIN_AIRBORNE" ] && break; sleep 5
done
[ "${airborne:-0}" -ge "$MIN_AIRBORNE" ] || { echo "рой не взлетел за ${TAKEOFF_TIMEOUT_S}с (в воздухе: ${airborne:-0} из ${MIN_AIRBORNE}); РЭБ не запускаю: ./scripts/px4_position_check.sh --fix, при неудаче ./scripts/swarm_fresh.sh"; exit 1; }
echo "в воздухе: $airborne, отсчёт таймлайна пошёл"
echo "[3/4] таймлайн: РЭБ через ${RF_AT_S}с, поражение бортов $KILL_DRONES через ${KILL_AT_S}с (от взлёта)"
( sleep "$RF_AT_S" && ./scripts/ewctl.sh scenario run "$SCENARIO" >/dev/null \
    && echo "  РЭБ: C2/GPS подавление включено" ) &
( sleep "$KILL_AT_S" && for k in $KILL_DRONES; do
    (docker kill "swarm-sitl-drone-agent-$k-1" >/dev/null 2>&1 || docker kill "drone-agent-$k" >/dev/null 2>&1 || true)
    echo "  борт $k подавлен (симуляция поражения)"
  done ) &
wait

echo "[4/4] таймлайн завершён."
echo "Evidence-карточки: http://127.0.0.1:8000 (панель BDA Evidence)"
echo "После демо: ./scripts/ewctl.sh reset && docker start swarm-sitl-drone-agent-$KILL_DRONE-1"
