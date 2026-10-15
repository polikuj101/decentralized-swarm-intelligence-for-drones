#!/usr/bin/env bash
# ew-tc.sh set <loss%> <corr%> <delay_ms> <jitter_ms> <rate_kbit|0> [limit]
# ew-tc.sh clear | show
set -euo pipefail
MESH_CIDR="${MESH_CIDR:-172.31.0.0/24}"

dev=$(ip -j -4 addr show | python3 -c '
import sys, json, ipaddress
net = ipaddress.ip_network(sys.argv[1])
for l in json.load(sys.stdin):
    for a in l.get("addr_info", []):
        if ipaddress.ip_address(a["local"]) in net:
            print(l["ifname"]); sys.exit(0)
sys.exit("mesh interface not found")' "$MESH_CIDR")

case "${1:-}" in
  set)
    loss="${2:?loss%}"; corr="${3:?corr%}"; delay="${4:?delay_ms}"; jit="${5:?jitter_ms}"
    rate="${6:?rate_kbit or 0}"; limit="${7:-50}"
    tc qdisc del dev "$dev" root 2>/dev/null || true
    args=(netem limit "$limit" delay "${delay}ms" "${jit}ms" distribution normal loss "${loss}%" "${corr}%")
    [ "$rate" != "0" ] && args+=(rate "${rate}kbit")
    tc qdisc add dev "$dev" root "${args[@]}"
    tc qdisc show dev "$dev" ;;
  clear) tc qdisc del dev "$dev" root 2>/dev/null || true; echo "cleared on $dev" ;;
  show)  tc -s qdisc show dev "$dev" ;;
  *) echo "usage: $0 set|clear|show" >&2; exit 2 ;;
esac
