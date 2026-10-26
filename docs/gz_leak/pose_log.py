#!/usr/bin/env python3
"""Журнал истинных поз бортов (время симуляции) для сверки VIO/EKF: запускать внутри simulator-core.
    docker cp docs/gz_leak/pose_log.py swarm-sitl-simulator-core-1:/tmp/pose_log.py
    docker exec -d swarm-sitl-simulator-core-1 python3 /tmp/pose_log.py /tmp/poses.jsonl
Пишет строку на каждое сообщение /world/<world>/dynamic_pose/info (~60 Гц): {"t": сим. время, "p": {N: [x, y, z, qw, qx, qy, qz]}}."""
import json
import os
import sys
import threading

from gz.msgs10.pose_v_pb2 import Pose_V
from gz.transport13 import Node

out = open(sys.argv[1] if len(sys.argv) > 1 else "/tmp/poses.jsonl", "a", buffering=1)


def on(msg):
    p = {}
    for q in msg.pose:
        if q.name.startswith("x500_swarm_"):
            p[q.name.rsplit("_", 1)[1]] = [round(q.position.x, 4), round(q.position.y, 4), round(q.position.z, 4),
                                          round(q.orientation.w, 5), round(q.orientation.x, 5), round(q.orientation.y, 5), round(q.orientation.z, 5)]
    if p:
        out.write(json.dumps({"t": round(msg.header.stamp.sec + msg.header.stamp.nsec * 1e-9, 4), "p": p}) + "\n")


node = Node()
node.subscribe(Pose_V, f"/world/{os.environ.get('GZ_WORLD', 'swarm_world')}/dynamic_pose/info", on)
threading.Event().wait()
