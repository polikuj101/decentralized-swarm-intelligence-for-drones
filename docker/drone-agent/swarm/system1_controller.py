#!/usr/bin/env python3
"""System 1: быстрый тактический контур. Событийно по /swarm/local/state (5 Гц), без LLM."""
import json
import os
import time
from collections import deque

import numpy as np
import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from swarm import actions as A
from swarm.candidates import enumerate_candidates
from swarm.features import build_obs
from swarm.matcher import Matcher
from swarm.phase import PhaseTracker


class System1(Node):
    def __init__(self):
        super().__init__("system1_controller")
        self.enabled = os.environ.get("S1_ENABLE", "1") == "1"
        mode, emb, protos = os.environ.get("S1_MODE", "hybrid"), None, None
        if mode != "rules":
            try:
                from swarm.embedder import Embedder, load_or_build_protos
                d = os.environ.get("S1_EMBED_DIR", "/opt/models/embed")
                emb = Embedder(d, dim=int(os.environ.get("S1_EMBED_DIM", "128")),
                               threads=int(os.environ.get("S1_EMBED_THREADS", "1")))
                protos = load_or_build_protos(emb, os.path.join(d, "protos.npz"))
                for t in ("phase nominal. link good. gps ok. sigma low. silent none. lost none. task sector.",) * 2:
                    emb.encode(t)                      # прогрев ORT
            except Exception as e:  # noqa: BLE001
                self.get_logger().error(f"embedder unavailable -> rules mode: {e!r}")
                emb = protos = None
        self.matcher = Matcher(mode, emb, protos, tau=float(os.environ.get("S1_TAU", "0.5")))
        self.tracker, self.phase = PhaseTracker(), A.Phase.NOMINAL
        self.lat, self.paths, self.n, self.sent, self.last_fb = deque(maxlen=500), {}, 0, {}, None
        self.pub_cmd = self.create_publisher(String, "/swarm/local/planner_cmd", 10)
        self.pub_phase = self.create_publisher(String, "/swarm/local/phase", 10)
        self.pub_log = self.create_publisher(String, "/swarm/local/s1_log", 10)
        self.create_subscription(String, "/swarm/local/state", self._on_state, 10)
        self.create_timer(1.0, self._phase_beat)
        self.create_timer(10.0, self._stats)
        self.get_logger().info(f"system1 started mode={self.matcher.mode} enabled={self.enabled}")

    def _pub_phase(self):
        self.pub_phase.publish(String(data=json.dumps({"phase": self.phase.value, "t_mono": time.monotonic()})))

    def _phase_beat(self):
        self._pub_phase()

    def _feedback(self, st, now):
        lc = st.get("last_cmd")
        if lc and lc.get("source") == "S1" and lc.get("id") != self.last_fb:
            self.last_fb = lc.get("id")
            k = self.sent.get(lc.get("id"))
            if k and not lc.get("ok"):
                self.matcher.until[k] = now + 60.0

    def _on_state(self, msg):
        t_in = time.monotonic()
        try:
            st = json.loads(msg.data)
            obs = build_obs(st)
        except Exception as e:  # noqa: BLE001
            self.get_logger().warning(f"bad state: {e!r}")
            return
        phase = self.tracker.update(obs, t_in)
        if phase != self.phase:
            self.get_logger().info(f"phase {self.phase.value} -> {phase.value}")
            self.phase = phase
            self._pub_phase()
        self._feedback(st, t_in)
        if not self.enabled:
            return
        dec = self.matcher.decide(enumerate_candidates(st, obs, phase, "S1"), obs, phase, t_in)
        self.paths[dec.path] = self.paths.get(dec.path, 0) + 1
        if dec.action is not None:
            self.n += 1
            cmd = {"id": self.n, "source": "S1", "phase": phase.value, "t_mono": t_in,
                   "tool": dec.action.tool, "args": dec.action.args.model_dump()}
            self.pub_cmd.publish(String(data=json.dumps(cmd)))
            self.sent[self.n] = self.matcher.key_of(dec.action)
            if len(self.sent) > 32:
                self.sent.pop(min(self.sent))
            dt = (time.monotonic() - t_in) * 1000.0
            self.pub_log.publish(String(data=json.dumps({
                "kind": "decision", "source": "S1", "id": self.n, "t": round(st["t"], 1), "trigger": phase.value,
                "reasoning": f"{dec.path} score={dec.score:.2f}", "tool": cmd["tool"], "args": cmd["args"],
                "verdict": "ACCEPTED", "reason": "", "latency_ms": round(dt, 1)})))
        self.lat.append((time.monotonic() - t_in) * 1000.0)

    def _stats(self):
        if not self.lat:
            return
        a = np.asarray(self.lat)
        em = np.asarray(self.matcher.embed_ms) if self.matcher.embed_ms else np.zeros(1)
        self.pub_log.publish(String(data=json.dumps({
            "kind": "stats", "mode": self.matcher.mode, "n": len(a), "phase": self.phase.value,
            "p50_ms": round(float(np.percentile(a, 50)), 1), "p95_ms": round(float(np.percentile(a, 95)), 1),
            "p99_ms": round(float(np.percentile(a, 99)), 1), "embed_p95_ms": round(float(np.percentile(em, 95)), 1),
            "paths": self.paths})))


def main():
    rclpy.init()
    node = System1()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
