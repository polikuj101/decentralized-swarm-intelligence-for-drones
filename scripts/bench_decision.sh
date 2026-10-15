#!/usr/bin/env bash
# scripts/bench_decision.sh — Бенчмарк латентности решений System 1 / System 2 (G3, G4)
set -euo pipefail

MODE="${1:-micro}"
DURATION="${2:-60}"
TAG="${3:-after}"

mkdir -p logs/bench

if [ "$MODE" = "micro" ]; then
  echo "=== Запуск микро-бенчмарка System 1 (tools/bench_s1.py) ==="
  docker compose exec -T -e PYTHONPATH=/opt/swarm_agent drone-agent-1 python3 /opt/swarm/tools/bench_s1.py --n 500

elif [ "$MODE" = "live" ]; then
  echo "=== Запуск live-сбора телеметрии решений System 1 / 2 (${DURATION} с) ==="
  OUT_FILE="logs/bench/${TAG}.jsonl"
  
  # Собираем данные через tools/collect_topic.py
  docker compose exec -T drone-agent-1 /usr/local/bin/ros-run.sh python3 /opt/swarm/tools/collect_topic.py \
    --duration "$DURATION" \
    --key "swarm/local/s1_log" > "$OUT_FILE" || true
    
  echo "Собрано записей: $(wc -l < "$OUT_FILE") в $OUT_FILE"
  
  # Анализируем результаты
  python3 - "$OUT_FILE" <<'PY'
import sys, json, numpy as np

fname = sys.argv[1]
latencies = []
phases = {}
sources = {}
count = 0

with open(fname, 'r') as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            lat = d.get("dt_ms", d.get("latency_ms", None))
            if lat is not None:
                latencies.append(float(lat))
            ph = d.get("phase", "UNKNOWN")
            phases[ph] = phases.get(ph, 0) + 1
            src = d.get("source", "S1")
            sources[src] = sources.get(src, 0) + 1
            count += 1
        except Exception:
            pass

print(f"=== Результаты live-бенчмарка ({count} событий) ===")
print(f"Распределение по фазам: {phases}")
print(f"Распределение по источникам: {sources}")

if latencies:
    lats = np.array(latencies)
    p50 = np.percentile(lats, 50)
    p95 = np.percentile(lats, 95)
    p99 = np.percentile(lats, 99)
    print(f"Латентность (мс): p50={p50:.2f}, p95={p95:.2f}, p99={p99:.2f}, min={np.min(lats):.2f}, max={np.max(lats):.2f}")
    if p95 <= 50.0:
        print("[PASS] p95 <= 50 мс соблюдено!")
    else:
        print(f"[WARN] p95 ({p95:.2f} мс) > 50 мс")
else:
    print("info: нет записей с латентностью")
PY

else
  echo "Использование: $0 [micro|live] [duration_s] [tag]"
  exit 1
fi
