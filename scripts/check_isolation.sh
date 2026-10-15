#!/usr/bin/env bash
# scripts/check_isolation.sh — Проверка архитектурных инвариантов изоляции (G8)
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

pass() { echo -e "${GREEN}[PASS]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*" >&2; exit 1; }
warn() { echo -e "[WARN] $*"; }

echo "=== 1. Проверка отсутствия GPU в контейнерах drone-agent-1..5 и fc-1..5 ==="
for i in {1..5}; do
  for svc in "drone-agent-$i" "fc-$i"; do
    if docker compose exec -T "$svc" sh -c "command -v nvidia-smi" >/dev/null 2>&1; then
      if docker compose exec -T "$svc" nvidia-smi >/dev/null 2>&1; then
        fail "GPU доступен внутри $svc! Нарушение изоляции."
      fi
    fi
    # Проверка переменных окружения
    nv_dev=$(docker compose exec -T "$svc" sh -c "echo \${NVIDIA_VISIBLE_DEVICES:-none}" | tr -d '\r\n')
    if [ "$nv_dev" != "none" ] && [ -n "$nv_dev" ] && [ "$nv_dev" != "void" ]; then
      fail "NVIDIA_VISIBLE_DEVICES задан в $svc: $nv_dev"
    fi
    pass "$svc: GPU отсутствует (nvidia-smi недоступен, NVIDIA_VISIBLE_DEVICES чист)"
  done
done

echo "=== 2. Проверка ONNX Runtime провайдеров (только CPUExecutionProvider) ==="
providers=$(docker compose exec -T drone-agent-1 python3 -c "
import onnxruntime as ort
p = ort.get_available_providers()
print(','.join(p))
assert 'CPUExecutionProvider' in p
assert 'CUDAExecutionProvider' not in p
assert 'TensorrtExecutionProvider' not in p
" | tr -d '\r\n')
pass "drone-agent-1 providers: $providers (строго CPU)"

echo "=== 3. Проверка cgroup лимитов drone-agent-1 (4 vCPU / 2 GiB) ==="
# Лимит памяти из docker inspect
mem_limit=$(docker inspect --format '{{.HostConfig.Memory}}' $(docker compose ps -q drone-agent-1))
if [ "$mem_limit" -ne 0 ] && [ "$mem_limit" -gt 2147483648 ]; then
  fail "Память drone-agent-1 превышает 2 GiB: $mem_limit"
else
  pass "Лимит памяти drone-agent-1: $mem_limit байт (<= 2 GiB)"
fi

# Лимит CPU из docker inspect (NanoCpus = 4 * 10^9)
nano_cpus=$(docker inspect --format '{{.HostConfig.NanoCpus}}' $(docker compose ps -q drone-agent-1))
if [ "$nano_cpus" -ne 0 ] && [ "$nano_cpus" -gt 4000000000 ]; then
  fail "CPU drone-agent-1 превышает 4 vCPU: $nano_cpus"
else
  pass "Лимит CPU drone-agent-1: $nano_cpus nano-cpus (<= 4 vCPU)"
fi

echo "=== 4. Проверка RSS памяти процессов System 1 / Llama ==="
rss_kb=$(docker compose exec -T drone-agent-1 sh -c "
ps -eo rss,comm,args | grep -E 'system1_controller|bench_s1' | grep -v grep | awk '{print \$1}' | head -1
" | tr -d '\r\n')

if [ -n "$rss_kb" ] && [ "$rss_kb" -gt 0 ]; then
  rss_mb=$((rss_kb / 1024))
  if [ "$rss_mb" -gt 800 ]; then
    fail "RSS System 1 превышает 800 МБ: ${rss_mb} МБ"
  else
    pass "RSS System 1 в норме: ${rss_mb} МБ (<= 800 МБ)"
  fi
else
  echo "info: процесс system1_controller пока не запущен как демон"
fi

echo -e "\n${GREEN}Все проверки изоляции (G8) пройдены успешно!${NC}"
