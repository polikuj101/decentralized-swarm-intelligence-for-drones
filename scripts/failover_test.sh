#!/usr/bin/env bash
# scripts/failover_test.sh — Тестирование реакции роя на отказ узла / изоляцию (G10)
set -euo pipefail

TARGET_PEER="${1:-3}"
HALF_MODE=0
if [ "${2:-}" = "--half" ]; then
  HALF_MODE=1
fi

echo "=== Тестирование Failover (целевой дрон: $TARGET_PEER, half=$HALF_MODE) ==="

cleanup() {
  echo "Восстановление состояния контейнеров..."
  docker compose unpause drone-agent-3 2>/dev/null || true
  docker compose unpause drone-agent-4 2>/dev/null || true
  docker compose unpause drone-agent-5 2>/dev/null || true
  docker compose start drone-agent-3 2>/dev/null || true
  docker compose start drone-agent-4 2>/dev/null || true
  docker compose start drone-agent-5 2>/dev/null || true
}
trap cleanup EXIT

if [ "$HALF_MODE" -eq 1 ]; then
  echo "Изоляция половины роя: остановка drone-agent-3, drone-agent-4, drone-agent-5..."
  docker compose pause drone-agent-3 drone-agent-4 drone-agent-5
  
  echo "Запуск зонда на drone-agent-1 на 15 секунд..."
  LOG=$(docker compose exec -T drone-agent-1 python3 /opt/swarm/tools/failover_probe.py "$TARGET_PEER" 15 || true)
  echo "$LOG"
  
  if echo "$LOG" | grep -q "SWARM_LOSS\|ISOLATED"; then
    echo -e "\033[0;32m[PASS]\033[0m Фаза SWARM_LOSS/ISOLATED зафиксирована при изоляции половины роя"
  else
    echo -e "\033[0;33m[WARN]\033[0m Фаза не сменилась на SWARM_LOSS/ISOLATED за время теста"
  fi

else
  echo "Остановка целевого дрона drone-agent-$TARGET_PEER..."
  # Запускаем зонд на drone-agent-1 в фоне
  OUT_TMP="/tmp/probe_output.jsonl"
  
  # Паузим узел
  t0=$(date +%s%N)
  docker compose pause "drone-agent-$TARGET_PEER"
  
  LOG=$(docker compose exec -T drone-agent-1 python3 /opt/swarm/tools/failover_probe.py "$TARGET_PEER" 12 || true)
  t1=$(date +%s%N)
  
  echo "$LOG"
  
  # Проверяем события dead или declared
  if echo "$LOG" | grep -q '"ev": "dead"\|"ev": "declared"'; then
    echo -e "\033[0;32m[PASS]\033[0m Дрон-агент 1 зафиксировал отказ дрона $TARGET_PEER"
  else
    echo -e "\033[0;33m[INFO]\033[0m Событие отказа в логе:"
  fi
fi

cleanup
trap - EXIT
echo "=== Failover тест завершен ==="
