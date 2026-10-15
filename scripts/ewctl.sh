#!/usr/bin/env bash
# ewctl.sh — управление РЭБ с хоста. Требует: curl; EW_URL по умолчанию http://127.0.0.1:8080
set -euo pipefail
URL="${EW_URL:-http://127.0.0.1:8080}"

arr() { if [ -z "${1:-}" ] || [ "$1" = "all" ]; then echo null; else echo "[${1// /}]"; fi; }
post() { curl -fsS -X POST "$URL$1" -H 'Content-Type: application/json' -d "$2"; echo; }

usage() { cat <<'EOF'
ewctl.sh state
ewctl.sh c2 cut [drones_csv|all] [duration_s] [--onboard]
ewctl.sh c2 restore [drones_csv|all]
ewctl.sh gps denied|degraded|noise|drift|spoof [drones_csv|all] [duration_s]
ewctl.sh gps restore [drones_csv|all]
ewctl.sh mesh L1|L2|L3|L4 [drones_csv|all] [duration_s]
ewctl.sh mesh restore [drones_csv|all]
ewctl.sh scenario list|run <name>|stop
ewctl.sh reset
EOF
exit 2; }

dur() { if [ -n "${1:-}" ] && [[ "$1" != --* ]]; then echo ", \"duration_s\": $1"; fi; }

cmd="${1:-}"; shift || true
case "$cmd" in
  state) curl -fsS "$URL/v1/state"; echo ;;
  c2)
    sub="${1:-}"; shift || true
    case "$sub" in
      cut)
        onboard=false; for a in "$@"; do [ "$a" = "--onboard" ] && onboard=true; done
        post /v1/ew/c2 "{\"drones\": $(arr "${1:-}"), \"include_onboard\": $onboard$(dur "${2:-}")}" ;;
      restore) post /v1/ew/restore "{\"channel\": \"c2\", \"drones\": $(arr "${1:-}")}" ;;
      *) usage ;;
    esac ;;
  gps)
    mode="${1:-}"; shift || true
    if [ "$mode" = restore ]; then post /v1/ew/restore "{\"channel\": \"gps\", \"drones\": $(arr "${1:-}")}"
    else post /v1/ew/gps "{\"mode\": \"$mode\", \"drones\": $(arr "${1:-}")$(dur "${2:-}")}"; fi ;;
  mesh)
    lvl="${1:-}"; shift || true
    if [ "$lvl" = restore ]; then post /v1/ew/restore "{\"channel\": \"mesh\", \"drones\": $(arr "${1:-}")}"
    else post /v1/ew/mesh "{\"level\": \"$lvl\", \"drones\": $(arr "${1:-}")$(dur "${2:-}")}"; fi ;;
  scenario)
    sub="${1:-}"; shift || true
    case "$sub" in
      list) curl -fsS "$URL/v1/scenarios"; echo ;;
      run)  post /v1/scenarios/run "{\"name\": \"${1:?scenario name}\"}" ;;
      stop) post /v1/scenarios/stop '{"restore": true}' ;;
      *) usage ;;
    esac ;;
  reset) post /v1/ew/restore '{"channel": "all", "drones": null}' ;;
  *) usage ;;
esac
