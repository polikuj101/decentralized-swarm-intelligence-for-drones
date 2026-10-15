#!/usr/bin/env python3
"""telemetry: состояние борта -> web-hud (HTTP POST, mgmt-net)."""
import json
import os
import queue
import threading
import time
import urllib.request
from collections import deque

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from swarm import common

URL = os.environ.get("HUD_URL", "http://172.33.0.5:8000/api/ingest") + f"/{common.DRONE_ID}"
TOKEN = os.environ.get("EW_TOKEN", "")


class Tele(Node):
    def __init__(self):
        super().__init__("telemetry")
        self.state, self.thoughts, self.llm = None, deque(maxlen=40), {}
        self.s1, self.phase = {}, "NOMINAL"
        self.q = queue.Queue(maxsize=4)
        self.create_subscription(String, "/swarm/local/state", self._on_state, 10)
        self.create_subscription(String, "/swarm/local/phase", self._on_phase, 10)
        self.create_subscription(String, "/swarm/local/s1_log", self._on_s1, 10)
        self.create_subscription(String, "/swarm/local/planner_log", self._on_log, 10)
        self.create_timer(0.5, self._tick)
        threading.Thread(target=self._worker, daemon=True).start()

    def _on_state(self, m):
        try:
            self.state = json.loads(m.data)
        except json.JSONDecodeError:
            pass

    def _on_phase(self, m):
        try:
            self.phase = json.loads(m.data).get("phase", "NOMINAL")
        except (ValueError, KeyError, json.JSONDecodeError):
            pass

    def _on_s1(self, m):
        try:
            d = json.loads(m.data)
        except json.JSONDecodeError:
            return
        if d.get("kind") == "stats":
            self.s1 = d
        else:
            self.thoughts.append({k: d.get(k) for k in ("id", "source", "t", "trigger", "reasoning", "tool", "args", "verdict", "reason", "latency_ms")})

    def _on_log(self, m):
        try:
            d = json.loads(m.data)
        except json.JSONDecodeError:
            return
        self.thoughts.append({k: d.get(k) for k in ("id", "source", "t", "trigger", "reasoning", "tool", "args", "verdict", "reason", "latency_ms")})
        self.llm = {"stats": d.get("stats", {}), "last_latency_ms": d.get("latency_ms"),
                    "model": "qwen2.5-" + os.environ.get("QWEN_VARIANT", "0.5b") + "-instruct-q4_k_m"}

    def _tick(self):
        s = self.state
        if not s:
            return
        pkt = {"v": 1, "drone": common.DRONE_ID, "ts": time.time(), "pos": s["pos"], "nav": s["nav"],
               "swarm": {k: s[k] for k in ("epoch", "task_name", "winners", "done", "open", "alive", "peers", "events",
                                           "withdrawn", "last_cmd", "total_tasks")},
               "mesh": {"link_q": s["link_q"], "periods": s["periods"], "heard": s["heard"], **s["mesh"]},
               "llm": self.llm, "thoughts": list(self.thoughts),
               "phase": self.phase, "s1": self.s1}
        try:
            self.q.put_nowait(json.dumps(pkt).encode())
        except queue.Full:
            pass

    def _worker(self):
        while True:
            data = self.q.get()
            try:
                req = urllib.request.Request(URL, data, {"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"})
                urllib.request.urlopen(req, timeout=2).read()
            except Exception:  # noqa: BLE001
                pass


def main():
    rclpy.init()
    node = Tele()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
