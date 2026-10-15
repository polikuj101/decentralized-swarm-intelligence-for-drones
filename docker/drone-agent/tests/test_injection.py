import json
import time
import pytest
import rclpy
from rclpy.node import Node
from std_msgs.msg import String


def test_live_injection():
    if not rclpy.ok():
        rclpy.init()
    node = Node("test_coordinator_rejection")
    pub = node.create_publisher(String, "/swarm/local/planner_cmd", 10)

    latest_state = [None]

    def on_state(msg):
        try:
            latest_state[0] = json.loads(msg.data)
        except Exception:
            pass

    sub = node.create_subscription(String, "/swarm/local/state", on_state, 10)

    def spin_for(secs=0.5):
        t0 = time.time()
        while time.time() - t0 < secs:
            rclpy.spin_once(node, timeout_sec=0.1)

    try:
        # Wait for state
        spin_for(1.5)
        assert latest_state[0] is not None, "Did not receive state"
        cur_phase = latest_state[0].get("phase", "NOMINAL")
        print("Initial state received, phase:", cur_phase)

        # Case 1: Schema with extra field
        cmd1 = {
            "id": 101,
            "source": "S1",
            "phase": cur_phase,
            "t_mono": time.monotonic(),
            "tool": "HOLD_POSITION",
            "extra": "invalid",
        }
        pub.publish(String(data=json.dumps(cmd1)))
        spin_for(0.6)
        lc1 = latest_state[0].get("last_cmd")
        print("Case 1 (extra field):", lc1)
        assert lc1 and lc1.get("ok") is False and "schema:" in lc1.get("msg", "")

        # Case 2: Stale command (age > 2.0s for S1)
        cmd2 = {
            "id": 102,
            "source": "S1",
            "phase": cur_phase,
            "t_mono": time.monotonic() - 10.0,
            "tool": "HOLD_POSITION",
        }
        pub.publish(String(data=json.dumps(cmd2)))
        spin_for(0.6)
        lc2 = latest_state[0].get("last_cmd")
        print("Case 2 (stale):", lc2)
        assert lc2 and lc2.get("ok") is False and "stale" in lc2.get("msg", "")

        # Case 3: Tool outside phase (HOLD_POSITION not permitted in NOMINAL)
        cmd3 = {
            "id": 103,
            "source": "S1",
            "phase": "NOMINAL",
            "t_mono": time.monotonic(),
            "tool": "HOLD_POSITION",
        }
        pub.publish(String(data=json.dumps(cmd3)))
        spin_for(0.6)
        lc3 = latest_state[0].get("last_cmd")
        print("Case 3 (tool outside phase):", lc3)
        assert lc3 and lc3.get("ok") is False and (
            "not permitted in NOMINAL" in lc3.get("msg", "")
            or "phase mismatch" in lc3.get("msg", "")
        )

        # Case 4: S1 with tool only for S2 (SPLIT_SECTOR)
        cmd4 = {
            "id": 104,
            "source": "S1",
            "phase": cur_phase,
            "t_mono": time.monotonic(),
            "tool": "SPLIT_SECTOR",
            "args": {"sector": 0, "parts": 2},
        }
        pub.publish(String(data=json.dumps(cmd4)))
        spin_for(0.6)
        lc4 = latest_state[0].get("last_cmd")
        print("Case 4 (S1 trying S2 tool):", lc4)
        assert lc4 and lc4.get("ok") is False and (
            "not permitted" in lc4.get("msg", "")
            or "schema:" in lc4.get("msg", "")
        )

    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
