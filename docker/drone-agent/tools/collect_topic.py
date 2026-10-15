#!/usr/bin/env python3
"""collect_topic.py <topic> <seconds>: строки {"rx": wall-clock, "msg": <json>} для std_msgs/String."""
import json
import sys
import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

topic = sys.argv[1]
secs = float(sys.argv[2])

rclpy.init()
n = Node("collect_topic")


def cb(m):
    try:
        print(json.dumps({"rx": time.time(), "msg": json.loads(m.data)}), flush=True)
    except json.JSONDecodeError:
        pass


n.create_subscription(String, topic, cb, 100)
t0 = time.time()
while time.time() - t0 < secs:
    rclpy.spin_once(n, timeout_sec=0.5)

n.destroy_node()
rclpy.shutdown()
