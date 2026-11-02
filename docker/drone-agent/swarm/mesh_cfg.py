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


# Заклинивание слушателя UDP в Zenoh 1.10.1 (и в main на 2026-11-02): одна задача accept_read_task читает сокет :7447 за все входящие
# соединения и для каждой датаграммы ждёт, пока соединение заберёт предыдущую (Mvar ёмкостью 1, zenoh-link-udp/src/unicast.rs).
# Если читатель соединения его бросил (транспорт закрыт), а датаграммы для него ещё идут, ожидание вечное: сокет больше не читается,
# очередь приёма растёт, новые сессии ни с кем не поднимаются. Остановить такой слушатель Zenoh не может (del_listener ждёт задачу),
# порт остаётся занят — восстановление только перезапуском процесса.
# На стенде (2026-11-02): заклинивает борт, только что снятый с паузы контейнера, если канал с потерями (L2/L3) — 9 раз за 10 минут;
# без потерь (6 пауз) и без пауз (пачки L3, разрывы L4) — ни разу.
# Признак: очередь сокета 7447 не пуста и не убывает STALL_S подряд. Работающий слушатель разбирает её за миллисекунды, борт после
# паузы — за 1-3 с (убывает); при потерях до сокета доходит мало, и заклинивший копит медленно (5 КБ за 50 с), порог в байтах не годится.
LISTEN_PORT = 7447
STALL_S = 15.0


def listen_backlog(proc_net_udp: str, port: int = LISTEN_PORT):
    """очередь приёма (байт) UDP-сокета, слушающего port, из текста /proc/net/udp; None — сокета нет"""
    for line in proc_net_udp.splitlines()[1:]:
        f = line.split()
        if len(f) > 4 and f[1].rsplit(":", 1)[-1].upper() == f"{port:04X}":
            return int(f[4].split(":")[1], 16)
    return None


class StallWatch:
    """True, когда очередь приёма не пуста и не убывает дольше hold_s подряд"""

    def __init__(self, hold_s=STALL_S):
        self.hold_s, self.since, self.last = hold_s, None, None

    def update(self, backlog, now):
        if not backlog:
            self.since = None
        elif self.since is None or self.last is None or backlog < self.last:
            self.since = now
        self.last = backlog
        return self.since is not None and now - self.since >= self.hold_s
