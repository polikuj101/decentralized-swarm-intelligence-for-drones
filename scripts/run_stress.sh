#!/usr/bin/env bash
# run_stress.sh [all|N,N,...] — предполётная проверка + запуск stress_test.py
set -euo pipefail
cd "$(dirname "$0")/.."
./scripts/preflight.sh
docker compose ps --format '{{.Service}} {{.State}}' | grep -E 'drone-agent-1.*(running|Up)' >/dev/null 2>&1 || {
  echo "стенд не поднят или не готов: make up"
  exit 1
}
python3 scripts/stress_test.py --vector "${1:-all}"
