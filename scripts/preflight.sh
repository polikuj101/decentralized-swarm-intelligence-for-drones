#!/usr/bin/env bash
set -euo pipefail
fail() { echo "FAIL: $*" >&2; exit 1; }

[ "$(uname -s)" = Linux ] || fail "нужна Linux-система (netem недоступен на macOS/Windows Docker Desktop)"
docker compose version >/dev/null 2>&1 || fail "нужен Docker Compose v2"

case "$(uname -r)" in
  *microsoft*|*WSL*|*standard*)
    echo "WARN: обнаружена среда WSL2 ($(uname -r)). Модули netem обычно встроены в ядро или требуют настройки."
    for m in sch_netem sch_prio cls_u32 nf_tables; do
      if lsmod | grep -q "^$m"; then
        echo "ok: $m (loaded)"
      elif sudo -n modprobe "$m" 2>/dev/null; then
        echo "ok: $m (loaded via modprobe)"
      else
        echo "info: $m (builtin or skipped under WSL2)"
      fi
    done
    # Без netem/prio РЭБ-агент режет mesh через nftables; без nf_tables профили L1-L4 не применятся и тесты хаоса будут вхолостую.
    if ! { lsmod | grep -q '^nf_tables' || { [ -r /proc/config.gz ] && zcat /proc/config.gz | grep -q '^CONFIG_NF_TABLES=[ym]'; }; }; then
      fail "в ядре WSL нет ни netem, ни nf_tables: эмуляция потерь mesh (L1-L4) невозможна"
    fi
    echo "ok: nf_tables доступен (запасной механизм эмуляции mesh)"
    ;;
  *)
    for m in sch_netem sch_prio cls_u32 nf_tables; do
      if ! lsmod | grep -q "^$m"; then
        sudo -n modprobe "$m" 2>/dev/null || fail "модуль ядра $m недоступен (uname -r: $(uname -r))"
      fi
      echo "ok: $m"
    done
    ;;
esac

[ -f .env ] && grep -q '^EW_TOKEN=.\+' .env || {
  echo "EW_TOKEN=$(openssl rand -hex 16)" >> .env
  echo "создан EW_TOKEN в .env"
}
[ -f .env ] && grep -q '^SWARM_KEY=.\+' .env || {
  echo "SWARM_KEY=$(openssl rand -hex 16)" >> .env
  echo "создан SWARM_KEY в .env"
}
echo "preflight passed"
