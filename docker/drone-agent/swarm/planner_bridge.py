#!/usr/bin/env python3
"""System 2: стратегический советник (Qwen2.5-0.5B). Вызывается только в ISOLATED/SWARM_LOSS/CRITICAL."""
import json
import os
import threading
import time
import urllib.request

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from swarm import actions as A
from swarm.candidates import enumerate_candidates
from swarm.features import build_obs
from swarm.grammar import choice_grammar

LLM_URL = os.environ.get("LLM_URL", "http://127.0.0.1:8080")
TIMEOUT = float(os.environ.get("LLM_TIMEOUT_S", "20"))
MIN_GAP_S = float(os.environ.get("S2_MIN_GAP_S", "15"))
PERIOD_S = float(os.environ.get("S2_PERIOD_S", "20"))
SYSTEM = ("You are the strategic advisor of drone {me} in a swarm of {n}. The swarm is in a critical phase. "
          "Pick exactly ONE option by its number from OPTIONS (0 means do nothing). "
          'Reply with JSON only: {{"choice": <number>, "reasoning": "<one short sentence>"}}.')


def _opt(i, a):
    return "%d: %s %s" % (i, a.tool, json.dumps(a.args.model_dump()))


def _peer(k, p):
    s = "ok" if p["alive"] else "silent %.0fs" % p["silent_s"]
    return "%s:%s task=%s" % (k, s, p["task"] if p["task"] != 255 else "none")


def observation(st, phase):
    nav = st["nav"]
    ev = "; ".join(e[1] for e in st["events"][-3:]) or "none"
    return ("t=%.0fs drone=%d phase=%s pos=(%.0f,%.0f) gps_fix=%s sigma=%.0fm link_q=%s alive=%d/%d epoch=%s\n"
            "my_task=%s done=%d/%d open=%s\npeers: %s\nevents: %s") % (
        st["t"], st["me"], phase.value, st["pos"][0], st["pos"][1], nav.get("gps_fix"), nav.get("sigma", 0),
        st["link_q"], len(st["alive"]), len(st["peers"]) + 1, st["epoch"], st["task_name"] or "none",
        len(st["done"]), st["total_tasks"], ",".join(t["name"] for t in st["open"]) or "none",
        "; ".join(_peer(k, p) for k, p in st["peers"].items()), ev)


class Planner(Node):
    def __init__(self):
        super().__init__("planner_bridge")
        self.pub_cmd = self.create_publisher(String, "/swarm/local/planner_cmd", 10)
        self.pub_log = self.create_publisher(String, "/swarm/local/planner_log", 10)
        self.create_subscription(String, "/swarm/local/state", self._on_state, 10)
        self.create_subscription(String, "/swarm/local/phase", self._on_phase, 10)
        self.st, self.phase, self.edge, self.busy = None, A.Phase.NOMINAL, False, False
        self.last_call, self.cmd_id, self.healthy = 0.0, 0, False
        self.stats = {"calls": 0, "accepted": 0, "noop": 0, "failed": 0}
        self.create_timer(1.0, self._tick)

    def _on_state(self, m):
        try:
            self.st = json.loads(m.data)
        except json.JSONDecodeError:
            pass

    def _on_phase(self, m):
        try:
            p = A.Phase(json.loads(m.data)["phase"])
        except (ValueError, KeyError, json.JSONDecodeError):
            return
        if p != self.phase:
            self.phase = p
            self.edge = p in A.S2_TRIGGER_PHASES

    def _health(self):
        try:
            with urllib.request.urlopen(LLM_URL + "/health", timeout=2) as r:
                return r.status == 200
        except Exception:  # noqa: BLE001
            return False

    def _tick(self):
        if self.busy or self.st is None or self.phase not in A.S2_TRIGGER_PHASES:
            return
        now = time.monotonic()
        if now - self.last_call < MIN_GAP_S or not (self.edge or now - self.last_call >= PERIOD_S):
            return
        if not self.healthy:
            self.healthy = self._health()
            return
        st, phase = self.st, self.phase
        cands = enumerate_candidates(st, build_obs(st), phase, "S2")[:9]
        self.edge = False
        if not cands:
            return
        self.busy, self.last_call = True, now
        threading.Thread(target=self._run, args=(st, phase, cands), daemon=True).start()

    def _run(self, st, phase, cands):
        t0 = time.monotonic()
        rec = {"source": "S2", "id": self.cmd_id + 1, "t": st["t"], "trigger": phase.value, "verdict": "FALLBACK",
               "reason": "", "reasoning": "", "tool": None, "args": None, "latency_ms": None}
        try:
            user = (observation(st, phase) + "\nOPTIONS:\n0: NO_ACTION\n"
                    + "\n".join(_opt(i + 1, a) for i, a in enumerate(cands)) + "\nAnswer:")
            prompt = ("<|im_start|>system\n%s<|im_end|>\n<|im_start|>user\n%s<|im_end|>\n<|im_start|>assistant\n"
                      % (SYSTEM.format(me=st["me"], n=len(st["peers"]) + 1), user))
            body = {"prompt": prompt, "grammar": choice_grammar(len(cands)), "n_predict": 80, "temperature": 0.2,
                    "top_k": 20, "top_p": 0.9, "seed": 42, "cache_prompt": True, "stop": ["<|im_end|>"]}
            req = urllib.request.Request(LLM_URL + "/completion", json.dumps(body).encode(),
                                         {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                res = json.loads(r.read())
            rec["latency_ms"] = int((time.monotonic() - t0) * 1000)
            out = json.loads(res["content"])
            idx, rec["reasoning"] = int(out["choice"]), str(out.get("reasoning", ""))[:100]
            self.stats["calls"] += 1
            if idx == 0:
                rec["verdict"] = "NOOP"
                self.stats["noop"] += 1
            else:
                act = cands[idx - 1]
                self.cmd_id += 1
                rec.update(id=self.cmd_id, verdict="ACCEPTED", tool=act.tool, args=act.args.model_dump())
                self.stats["accepted"] += 1
                self.pub_cmd.publish(String(data=json.dumps({
                    "id": self.cmd_id, "source": "S2", "phase": phase.value, "t_mono": t0,
                    "tool": act.tool, "args": act.args.model_dump()})))
        except Exception as e:  # noqa: BLE001
            rec["reason"] = ("%s: %s" % (type(e).__name__, e))[:120]
            rec["latency_ms"] = int((time.monotonic() - t0) * 1000)
            self.stats["failed"] += 1
            self.healthy = self._health()
        rec["stats"] = dict(self.stats)
        self.pub_log.publish(String(data=json.dumps(rec)))
        self.busy = False


def main():
    rclpy.init()
    node = Planner()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
