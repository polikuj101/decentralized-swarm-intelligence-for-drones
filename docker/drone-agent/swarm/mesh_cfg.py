"""swarm/mesh_cfg.py — конфигурация Zenoh-сессии. Чистая функция без zenoh: тестируется на хосте (tests/test_mesh_cfg.py)."""
import json

LEASE_MS = 4000
# keep_alive в Zenoh — ЧИСЛО keepalive за lease, а не миллисекунды: интервал = LEASE_MS / KEEP_ALIVE_PER_LEASE.
# Значение 1000 (прочитанное как мс) давало ~1340 однобайтных пакетов/с на борт с 2026-10-16 до 2026-10-28.
KEEP_ALIVE_PER_LEASE = 4


def cfg(own_ip, peer_ips, tuned):
    d = {
        "mode": "peer",
        "listen": {"endpoints": [f"udp/{own_ip}:7447"]},
        "connect": {"endpoints": [f"udp/{ip}:7447" for ip in peer_ips]},
        "scouting": {"multicast": {"enabled": False}, "gossip": {"enabled": True}},
    }
    if tuned:
        d["transport"] = {"link": {"tx": {"lease": LEASE_MS, "keep_alive": KEEP_ALIVE_PER_LEASE}}}
        d["connect"]["retry"] = {"period_init_ms": 500, "period_max_ms": 2000, "period_increase_factor": 2}
    return json.dumps(d)
