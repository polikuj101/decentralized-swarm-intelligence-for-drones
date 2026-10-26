#!/usr/bin/env bash
# HANDOFF п. 6: перезапуск ТОЛЬКО агента D3 при живом PX4 — раньше это давало «connected», Payload tx = 0 B/s навсегда.
# С патчем P5 клиент PX4 должен сам пересоздать сессию через ~5 с.
cd "/mnt/c/Users/nurbolatte/Desktop/Swarm of Drones" || exit 1
OUT=${OUT_DIR:-/tmp}/exp_dds.log
: > "$OUT"
say(){ echo "[$(date +%T)] $*" | tee -a "$OUT"; }
st(){ docker exec swarm-sitl-fc-3-1 px4-shell uxrce_dds_client status 2>&1 | grep -E "Payload tx|Payload rx|connected" | tr -s ' ' | tr '\n' ' '; }
say "before: $(st)"
touch logs/.watchdog_pause
docker compose restart drone-agent-3 >/dev/null 2>&1
say "agent-3 restarted"
for k in 2 5 8 12 16 20 30 45; do
  sleep $((k - ${prev:-0})); prev=$k
  say "t+${k}s: $(st)"
done
docker logs --since 2m swarm-sitl-fc-3-1 2>&1 | grep -E "stalled|recreating|No ping|synchronized|session" | tail -6 | tee -a "$OUT"
n=$(curl -s -m 5 http://127.0.0.1:8000/api/state | python3 -c "import sys,json;s=json.load(sys.stdin);d=s['drones'].get('3',{});print((d.get('pkt') or {}).get('nav',{}).get('state'), (d.get('pkt') or {}).get('nav',{}).get('armed'))" 2>/dev/null)
say "HUD D3 nav state/armed: $n"
rm -f logs/.watchdog_pause
