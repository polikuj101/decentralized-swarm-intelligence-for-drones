#!/usr/bin/env bash
# run_demo.sh — запуск демо-таймлайна в один клик. ПРЕДПОЛАГАЕТ, что стенд уже поднят и прогрет
# (make up + ожидание готовности выполнены заранее, за 15-20 минут до встречи — см. D7, п. 9.1).
set -euo pipefail
cd "$(dirname "$0")/.."
AUTOPILOT="${DEMO_AUTOPILOT:-0}"
PROMPT="${DEMO_PROMPT:-Разведать квадрат Браво, вскрыть позиции, при обнаружении целей — доразведка и зависание}"
KILL_AT_S="${DEMO_KILL_AT_S:-100}"
KILL_DRONE="${DEMO_KILL_DRONE:-2}"
RF_AT_S="${DEMO_RF_AT_S:-25}"

echo "[1/4] сброс РЭБ и проверка живости бортов"
./scripts/ewctl.sh reset >/dev/null 2>&1 || true
for i in 1 2 3 4 5; do
  docker ps --format '{{.Names}}' | grep -q "drone-agent-$i" \
    || docker start "swarm-sitl-drone-agent-$i-1" >/dev/null 2>&1 || true
done
curl -fsS http://127.0.0.1:8000/healthz >/dev/null || { echo "HUD не отвечает — проверьте 'make up'"; exit 1; }
echo "ok"

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

echo "[3/4] таймлайн: РЭБ через ${RF_AT_S}с, поражение борта $KILL_DRONE через ${KILL_AT_S}с"
( sleep "$RF_AT_S" && ./scripts/ewctl.sh scenario run 05_investor_demo >/dev/null \
    && echo "  РЭБ: C2/GPS подавление включено" ) &
( sleep "$KILL_AT_S" && (docker kill "swarm-sitl-drone-agent-$KILL_DRONE-1" >/dev/null 2>&1 || docker kill "drone-agent-$KILL_DRONE" >/dev/null 2>&1 || true) \
    && echo "  борт $KILL_DRONE подавлен (симуляция поражения)" ) &
wait

echo "[4/4] таймлайн завершён."
echo "Evidence-карточки: http://127.0.0.1:8000 (панель BDA Evidence)"
echo "После демо: ./scripts/ewctl.sh reset && docker start swarm-sitl-drone-agent-$KILL_DRONE-1"
