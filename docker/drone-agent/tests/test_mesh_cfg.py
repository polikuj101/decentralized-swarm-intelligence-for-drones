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


PROC = """  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode ref pointer drops
  981: 0B001FAC:1D17 00000000:0000 07 00000000:0001C200 00:00000000 00000000     0        0 123 2 0000000000000000 0
  982: 0B001FAC:8E2D 0D001FAC:1D17 01 00000000:00000000 00:00000000 00000000     0        0 124 2 0000000000000000 0
"""


def test_listen_backlog_reads_listening_socket_only():
    from swarm.mesh_cfg import listen_backlog
    assert listen_backlog(PROC) == 0x1C200                # 115200 Б: заклинивший слушатель (прогон 2026-11-02)
    assert listen_backlog(PROC.splitlines()[0]) is None    # сокета нет
    assert listen_backlog(PROC, port=1234) is None


def test_stall_is_backlog_that_never_drains():
    from swarm.mesh_cfg import StallWatch
    w = StallWatch(hold_s=15.0)
    assert not w.update(5376, 0.0)
    assert not w.update(5376, 10.0)
    assert w.update(5376, 15.0)                           # заклинивший при L3: 5 КБ стоят на месте (прогон 2026-11-02)


def test_draining_backlog_is_not_a_stall():
    from swarm.mesh_cfg import StallWatch
    w = StallWatch(hold_s=15.0)
    for t, q in [(0, 38400), (1, 20000), (2, 0), (3, 30000), (8, 29000), (13, 28000), (18, 27000), (23, 0)]:
        assert not w.update(q, float(t)), (t, q)            # борт после паузы разбирает накопленное — убывает
    assert not w.update(None, 30.0)


def test_growing_backlog_is_a_stall():
    from swarm.mesh_cfg import StallWatch
    w = StallWatch(hold_s=15.0)
    for t, q in [(0, 13056), (5, 13824), (10, 14592)]:
        assert not w.update(q, float(t))
    assert w.update(16896, 15.0)
