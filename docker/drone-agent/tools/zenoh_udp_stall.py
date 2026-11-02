#!/usr/bin/env python3
"""zenoh_udp_stall.py — минимальное воспроизведение заклинивания UDP-слушателя Zenoh (swarm/mesh_cfg.py), без стенда.

Сессия A слушает udp/127.0.0.1:PORT. С одного исходного порта в слушатель летит пачка датаграмм, которые не являются рукопожатием Zenoh:
слушатель заводит под этот адрес соединение, рукопожатие падает, соединение брошено, а цикл приёма ждёт, пока брошенное соединение
заберёт следующую датаграмму (Mvar ёмкостью 1). После этого сессия B подключиться к A не может, очередь приёма сокета растёт.
Запуск (в образе агента есть eclipse-zenoh): docker run --rm --entrypoint python3 -v $PWD/docker/drone-agent/tools:/t swarm/drone-agent:latest /t/zenoh_udp_stall.py
"""
import json
import socket
import sys
import time

import zenoh

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 7999
BURST = int(sys.argv[2]) if len(sys.argv) > 2 else 50


def session(listen=None, connect=None):
    c = {"mode": "peer", "scouting": {"multicast": {"enabled": False}, "gossip": {"enabled": False}},
         "listen": {"endpoints": [listen] if listen else []}, "connect": {"endpoints": [connect] if connect else []}}
    return zenoh.open(zenoh.Config.from_json5(json.dumps(c)))


def backlog():
    for line in open("/proc/net/udp").read().splitlines()[1:]:
        f = line.split()
        if f[1].endswith(f":{PORT:04X}"):
            return int(f[4].split(":")[1], 16)
    return None


def peers(s, wait=6.0):
    t = time.time()
    while time.time() - t < wait:
        n = len(list(s.info.peers_zid()))
        if n:
            return n
        time.sleep(0.2)
    return 0


a = session(listen=f"udp/127.0.0.1:{PORT}")
b1 = session(connect=f"udp/127.0.0.1:{PORT}")
print("до: сессия B1 видит пиров", peers(b1), "очередь", backlog(), flush=True)

junk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
junk.bind(("127.0.0.1", 0))
for i in range(BURST):
    junk.sendto(b"\x00" * 32, ("127.0.0.1", PORT))
time.sleep(2.0)
for i in range(BURST):                          # продолжаем слать с того же адреса: как кадры старой сессии после паузы
    junk.sendto(b"\x00" * 32, ("127.0.0.1", PORT))
time.sleep(1.0)

b2 = session(connect=f"udp/127.0.0.1:{PORT}")
n2 = peers(b2, wait=10.0)
for _ in range(200):
    junk.sendto(b"\x00" * 512, ("127.0.0.1", PORT))
time.sleep(1.0)
q = backlog()
print("после: новая сессия B2 видит пиров", n2, "очередь", q, flush=True)
print("ЗАКЛИНИЛО" if n2 == 0 and (q or 0) > 0 else "не воспроизведено", flush=True)
