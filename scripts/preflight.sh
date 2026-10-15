#!/usr/bin/env bash
set -euo pipefail
fail() { echo "FAIL: $*" >&2; exit 1; }

[ "$(uname -s)" = Linux ] || fail "нужна Linux-система (netem недоступен на macOS/Windows Docker Desktop)"
docker compose version >/dev/null 2>&1 || fail "нужен Docker Compose v2"

for m in sch_netem sch_prio cls_u32 nf_tables; do
  if ! lsmod | grep -q "^$m"; then
    sudo modprobe "$m" 2>/dev/null || fail "модуль ядра $m недоступен (uname -r: $(uname -r))"
  fi
  echo "ok: $m"
done

[ -f .env ] && grep -q '^EW_TOKEN=.\+' .env || {
  echo "EW_TOKEN=$(openssl rand -hex 16)" >> .env
  echo "создан EW_TOKEN в .env"
}
[ -f .env ] && grep -q '^SWARM_KEY=.\+' .env || {
  echo "SWARM_KEY=$(openssl rand -hex 16)" >> .env
  echo "создан SWARM_KEY в .env"
}
echo "preflight passed"
