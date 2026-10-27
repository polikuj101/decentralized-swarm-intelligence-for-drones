#!/usr/bin/env bash
# px4_position_check.sh [--fix] — у каждого из пяти PX4 ekf2 должен отдавать local position.
# Зачем: репетиция демо 2026-10-27, борт 5 не взлетел. Через ~40 с после старта у его PX4 навсегда пропал поток магнитометра и GPS из Gazebo,
# ekf2 остался без положения («local position: 0»), PX4 вытолкнул борт из OFFBOARD и включил «Failsafe: blind land», агент слал подъём вхолостую.
# Лечится перезапуском PX4 этого борта и затем его агента (порядок важен: иначе протухшая сессия DDS, см. swarm_fresh.sh).
# Без --fix только проверяет (код возврата 1, если у кого-то нет положения). С --fix перезапускает проблемные борта, до POS_TRIES попыток.
# Переменные: POS_WAIT_S (по умолчанию 90) — сколько ждать положения после старта/перезапуска, POS_TRIES (по умолчанию 2).
set -uo pipefail
cd "$(dirname "$0")/.."
FIX=0; [ "${1:-}" = "--fix" ] && FIX=1
WAIT_S=${POS_WAIT_S:-90}; TRIES=${POS_TRIES:-2}
CHECK_TX=0
say(){ echo "$(date +%T) $*"; }

has_pos(){ docker exec "swarm-sitl-fc-$1-1" px4-shell ekf2 status 2>&1 | grep -q "local position: 1"; }
has_tx(){
  local tx
  tx=$(docker exec "swarm-sitl-fc-$1-1" px4-shell uxrce_dds_client status 2>&1 | awk '/Payload tx/{print $5}')
  [ "${tx:-0}" != 0 ]
}
bad_boards(){
  local b="" i
  for i in 1 2 3 4 5; do
    has_pos "$i" || { b="$b $i"; continue; }
    [ "$CHECK_TX" = 1 ] && ! has_tx "$i" && b="$b $i"
  done
  echo "$b"
}
wait_all(){   # ждём до WAIT_S секунд, пока у всех есть положение; печатает список бортов, у которых его нет
  local t0=$SECONDS b
  while :; do
    b=$(bad_boards)
    { [ -z "$b" ] || [ $((SECONDS - t0)) -ge "$WAIT_S" ]; } && break
    sleep 5
  done
  echo "$b"
}

bad=$(wait_all)
if [ -z "$bad" ]; then say "ekf2: у всех пяти бортов есть положение"; exit 0; fi
say "ekf2 без положения у бортов:$bad"
[ "$FIX" = 1 ] || exit 1

CHECK_TX=1
for try in $(seq 1 "$TRIES"); do
  say "попытка $try из $TRIES: перезапуск PX4, затем агента у бортов:$bad"
  for i in $bad; do docker compose restart "fc-$i" >/dev/null 2>&1; done
  sleep 25
  for i in $bad; do docker compose restart "drone-agent-$i" >/dev/null 2>&1; done
  bad=$(wait_all)
  if [ -z "$bad" ]; then say "положение и обмен PX4-агент восстановлены"; exit 0; fi
  say "всё ещё нет положения/обмена у бортов:$bad"
done
say "НЕ ВОССТАНОВЛЕНО у бортов:$bad — запустите ./scripts/swarm_fresh.sh заново"
exit 1
