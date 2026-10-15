#!/usr/bin/env python3
"""failover_probe.py <peer> <seconds>: регистрирует тайминги failover-событий."""
import json
import sys
import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

peer, secs = sys.argv[1], float(sys.argv[2])
rclpy.init()
n = Node("failover_probe")
seen, had = set(), {"task": False}


def emit(ev, **kw):
    print(json.dumps({"ev": ev, "t": time.time(), **kw}), flush=True)


def on_state(m):
    st = json.loads(m.data)
    p = st["peers"].get(peer)
    if st["winners"] and any(str(v) == peer for v in st["winners"].values()):
        had["task"] = True
    if p and not p["alive"] and "dead" not in seen:
        seen.add("dead")
        emit("dead")
    if p and p["declared_failed"] and "declared" not in seen:
        seen.add("declared")
        emit("declared")
    if "dead" in seen and had["task"] and not any(str(v) == peer for v in st["winners"].values()) and "reassigned" not in seen:
        seen.add("reassigned")
        emit("reassigned")
    ph = st.get("phase")
    if ph and ("phase", ph) not in seen:
        seen.add(("phase", ph))
        emit("phase", value=ph)


def on_log(m):
    emit("s2_call", msg=json.loads(m.data))


n.create_subscription(String, "/swarm/local/state", on_state, 20)
n.create_subscription(String, "/swarm/local/planner_log", on_log, 20)
t0 = time.time()
while time.time() - t0 < secs:
    rclpy.spin_once(n, timeout_sec=0.2)

n.destroy_node()
rclpy.shutdown()
