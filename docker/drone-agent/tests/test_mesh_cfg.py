"""tests/test_mesh_cfg.py — единицы keep_alive в конфигурации Zenoh (регрессия 2026-10-28: 1000 вместо 4 забивало mesh пакетами)."""
import json

from swarm.mesh_cfg import cfg


def test_keep_alive_is_a_count_per_lease_not_milliseconds():
    link = json.loads(cfg("172.31.0.11", ["172.31.0.12"], True))["transport"]["link"]["tx"]
    interval_ms = link["lease"] / link["keep_alive"]
    assert 250 <= interval_ms <= 2000, f"keepalive каждые {interval_ms} мс"
    assert link["lease"] >= 2000


def test_endpoints_and_untuned_config():
    d = json.loads(cfg("172.31.0.11", ["172.31.0.12", "172.31.0.13"], False))
    assert d["listen"]["endpoints"] == ["udp/172.31.0.11:7447"]
    assert d["connect"]["endpoints"] == ["udp/172.31.0.12:7447", "udp/172.31.0.13:7447"]
    assert "transport" not in d
