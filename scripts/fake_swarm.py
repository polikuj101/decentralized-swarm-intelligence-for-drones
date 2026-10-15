#!/usr/bin/env python3
"""fake_swarm.py — синтетическая телеметрия N бортов для проверки HUD (схема п. 4.11).
--jam-after S: через S секунд борты «перестают слышать» друг друга, у борта 1 пропадает GPS."""
import argparse
import json
import math
import os
import time
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--url", default="http://127.0.0.1:8000")
ap.add_argument("--n", type=int, default=5)
ap.add_argument("--jam-after", type=float, default=0.0)
a = ap.parse_args()
TOKEN = os.environ.get("EW_TOKEN", "")
T0 = time.time()
OPEN = [{"id": p, "name": f"S{p}", "kind": "SECTOR", "cx": 18.3 + 16.7 * (p % 3), "cy": -13.75 + 32.5 * (p // 3),
         "area": 542, "splittable": True} for p in range(6)]


def packet(i: int, t: float) -> dict:
    jam = bool(a.jam_after) and t > a.jam_after
    ang = 0.08 * t + i
    x, y, z = 35 + 18 * math.cos(ang) + 3 * (i - 3), 3 + 20 * math.sin(ang), 3 + 0.8 * (i - 1)
    others = [k for k in range(1, a.n + 1) if k != i]
    peers = {str(k): {"alive": not jam, "declared_failed": False, "silent_s": 20.0 if jam else 0.3,
                      "q": 0.1 if jam else 0.9, "x": 0, "y": 0, "z": 3, "task": 255, "state": 2, "flags": 0,
                      "epoch": 3, "heard": []} for k in others}
    gps_lost = jam and i == 1
    nav = {"seq": 1, "idx": 0, "done": False, "state": "NAV", "phase": "FLY", "x": x, "y": y, "z": z, "vx": 1.0, "vy": 0.0,
           "hdg": 0.0, "src": "VIO" if gps_lost else "PX4", "sigma": 6.0 if gps_lost else 1.5,
           "gps_fix": 0 if gps_lost else 3, "gps_suspect": False, "vio_ok": True, "xy_valid": True, "armed": True,
           "obst_min": 99.0}
    ths = [{"id": k, "t": k * 15, "trigger": "SCAN_TARGET", "reasoning": "<b>test</b> open target, drone idle",
            "tool": "SCAN_TARGET", "args": {"target": 0}, "verdict": "ACCEPTED" if k % 2 else "REJECTED",
            "reason": "" if k % 2 else "tool not in legal list", "latency_ms": 3000 + 100 * i}
           for k in range(1, int(t // 15) + 1)][-20:]
    evs = [[k * 20.0, f"fake event {k}"] for k in range(1, int(t // 20) + 1)][-10:]
    return {"v": 1, "drone": i, "ts": time.time(), "pos": [x, y, z], "nav": nav,
            "swarm": {"epoch": 3, "task_name": f"S{(i - 1) % 6}", "winners": {}, "done": [], "open": OPEN,
                      "alive": [i] if jam else list(range(1, a.n + 1)), "peers": peers, "events": evs,
                      "withdrawn": False, "last_cmd": None, "total_tasks": 9},
            "mesh": {"link_q": 0.1 if jam else 0.9, "periods": {"hb": 0.5, "claim": 1.0, "alloc": 2.0, "rate_Bps": 1500},
                     "heard": [] if jam else others, "ready": True, "tuned": True, "zenoh_peers": 0 if jam else a.n - 1,
                     "dropped": {"hb": 0, "claim": 0, "alloc": 0, "fail": 0}, "bad_tag": 0, "tx_bytes": {}},
            "llm": {"stats": {"calls": len(ths), "accepted": len(ths) // 2, "rejected": len(ths) // 2, "failed": 0},
                    "last_latency_ms": 3100, "model": "qwen2.5-0.5b-instruct-q4_k_m"},
            "thoughts": ths}


if __name__ == "__main__":
    while True:
        t = time.time() - T0
        for i in range(1, a.n + 1):
            req = urllib.request.Request(f"{a.url}/api/ingest/{i}", json.dumps(packet(i, t)).encode(),
                                         {"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"})
            try:
                urllib.request.urlopen(req, timeout=2).read()
            except Exception as e:  # noqa: BLE001
                print("post failed:", e)
        time.sleep(0.5)
