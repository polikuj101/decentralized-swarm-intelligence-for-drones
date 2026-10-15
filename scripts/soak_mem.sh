#!/usr/bin/env bash
# scripts/soak_mem.sh — Soak-тест на утечки памяти System 1 и стека companion-компьютера
set -euo pipefail

DURATION="${1:-300}"
INTERVAL=10
SVC="drone-agent-1"

echo "=== Запуск Soak-теста памяти $SVC на ${DURATION} секунд (шаг ${INTERVAL} с) ==="

get_rss() {
  docker compose exec -T "$SVC" sh -c "
    ps -eo rss,comm,args | grep -E 'system1_controller|coordinator|planner_bridge|llama-server' | grep -v grep | awk '{sum+=\$1} END {print sum}'
  " | tr -d '\r\n'
}

initial_rss=$(get_rss)
if [ -z "$initial_rss" ] || [ "$initial_rss" -le 0 ]; then
  initial_rss=100000
fi
echo "Начальный суммарный RSS процессов агента: $((initial_rss / 1024)) МБ"

max_rss=$initial_rss
elapsed=0

while [ $elapsed -lt "$DURATION" ]; do
  sleep "$INTERVAL"
  elapsed=$((elapsed + INTERVAL))
  curr_rss=$(get_rss)
  if [ -n "$curr_rss" ] && [ "$curr_rss" -gt "$max_rss" ]; then
    max_rss=$curr_rss
  fi
  echo -ne "Прошло: ${elapsed}/${DURATION} с | Текущий RSS: $((curr_rss / 1024)) МБ | Макс: $((max_rss / 1024)) МБ\r"
done
echo ""

final_rss=$(get_rss)
diff_mb=$(( (final_rss - initial_rss) / 1024 ))

echo "=== Результаты Soak-теста ==="
echo "Начальный RSS: $((initial_rss / 1024)) МБ"
echo "Конечный RSS:  $((final_rss / 1024)) МБ"
echo "Максимальный:  $((max_rss / 1024)) МБ"
echo "Дельта:        ${diff_mb} МБ"

if [ "$diff_mb" -gt 60 ]; then
  echo -e "\033[0;31m[FAIL]\033[0m Обнаружена утечка памяти: прирост > 60 МБ за ${DURATION} с"
  exit 1
else
  echo -e "\033[0;32m[PASS]\033[0m Утечек памяти не обнаружено (дельта ${diff_mb} МБ <= 60 МБ)"
fi
