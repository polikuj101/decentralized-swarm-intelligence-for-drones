#!/usr/bin/env python3
"""coordinator: P2P-слой роя (Heartbeat, liveness, CBAA, исполнение миссии, команды LLM)."""
import json
import os
import threading
import time
from collections import deque

import rclpy
from rclpy.node import Node
from std_msgs.msg import String
import math
from collections import deque
from swarm import actions as A, common, wire
from swarm.bump_gate import BumpGate
from swarm.cbaa import CBAA
from swarm.manifest_check import manifest_problem
from swarm.mesh import Mesh

ME, N, M = common.DRONE_ID, common.SWARM_SIZE, common.TASK_COUNT
T_DEAD, T_SUSPECT, SETTLE_S = 3.5, 3.0, 2.0
STATE_CODE = {"IDLE": 0, "TAKEOFF": 1, "NAV": 2, "HOLD": 3, "DWELL": 4, "RTB": 5, "LANDED": 6}
CODE_STATE = {v: k for k, v in STATE_CODE.items()}


def sgt(a, b):
    """a новее b в арифметике u8"""
    return 1 <= ((a - b) & 0xFF) <= 127


class Peer:
    __slots__ = ("last_rx", "seq", "q", "x", "y", "z", "state", "flags", "sigma", "task", "epoch",
                 "link_q", "heard", "heard_rx", "hb_s", "rx")

    def __init__(self):
        self.last_rx = None
        self.seq = None
        self.q = 0.5
        self.x = self.y = self.z = 0.0
        self.state, self.flags, self.sigma, self.task = 0, 0, 0.0, wire.NONE
        self.epoch, self.link_q, self.heard, self.heard_rx = 0, 0, 0, None
        self.hb_s, self.rx = 0.5, 0


class Coordinator(Node):
    def __init__(self):
        super().__init__("coordinator")
        now = time.monotonic()
        self.t0 = now
        self.lock = threading.RLock()
        self.peers = {i: Peer() for i in range(1, N + 1) if i != ME}
        self.epoch, self.done, self.split2, self.split3 = 0, 0, 0, 0
        self.tasks = common.build_tasks()
        self.cbaa = CBAA(ME, M)
        self.force_dead, self.prefer = set(), {}
        self.withdrawn = False
        self.nav = {}
        self.goal, self.goal_seq = None, int(time.time()) % 1000000
        self.exec_task, self.override, self.mission_done_sent = None, None, False
        self.exec_dist_min, self.exec_dist_t = 999.0, now
        self.alive_prev = {ME}
        self.last_bump, self.bump_gate = -99.0, BumpGate()
        self.vec_changed, self.claim_dirty = 0.0, False
        self._x_last, self.x_since = None, now
        self.seqs = {wire.T_HB: 0, wire.T_CLAIM: 0, wire.T_ALLOC: 0, wire.T_FAIL: 0}
        self.t_hb = self.t_claim = self.t_alloc = self.t_claim_last = 0.0
        self.alloc_now, self.fail_tx = False, []
        self.tokens, self.t_refill = 600.0, now
        self.dropped = {"hb": 0, "claim": 0, "alloc": 0, "fail": 0}
        self.bad_tag = 0
        self.events, self.last_cmd = deque(maxlen=40), None
        self.phase, self.phase_t = A.Phase.NOMINAL, time.monotonic()
        self.crumbs, self.t_crumb = deque(maxlen=150), 0.0           # (t, x, y): позиции с хорошей связью
        self.llm_ok_t, self.t_state, self.t_goal_pub = 0.0, 0.0, 0.0

        peers_ips = [ip for ip in os.environ.get("PEERS", "").split(",") if ip]
        self.mesh = Mesh(ME, os.environ["SELF_MESH_IP"], peers_ips, self._on_wire)
        self.pub_goal = self.create_publisher(String, "/swarm/local/nav_goal", 10)
        self.pub_state = self.create_publisher(String, "/swarm/local/state", 10)
        self.create_subscription(String, "/swarm/local/nav_status", self._on_nav, 10)
        self.create_subscription(String, "/swarm/local/phase", self._on_phase, 10)
        self.create_subscription(String, "/swarm/local/planner_cmd", self._on_cmd, 10)
        self.create_subscription(String, "/swarm/local/planner_log", self._on_plog, 10)
        self.manifest_file = os.environ.get("MANIFEST_FILE", "/opt/swarm/config/manifest.json")
        self.manifest_wait_timeout_s = 90.0
        self.active_zones, self.manifest, self.manifest_wait_t0 = None, None, now
        self.log("ожидание Task Manifest (NL-приказ оператора)...")
        self.create_timer(0.1, self.tick)
        self.log("coordinator started")

    # ───────── утилиты ─────────
    def log(self, text):
        self.events.append((round(time.monotonic() - self.t0, 1), text))
        self.get_logger().info(text)

    def pos(self):
        n = self.nav
        return n.get("x", common.SPAWN[0]), n.get("y", common.SPAWN[1]), n.get("z", 0.0)

    def nav_degraded(self):
        n = self.nav
        return bool(n) and (n.get("gps_fix", 3) < 3 or n.get("gps_suspect", False))

    def peer_q(self, p, now):
        if p.last_rx is None:
            return 0.0
        return p.q * (0.9 ** ((now - p.last_rx) / max(0.5, p.hb_s)))

    def link_quality(self, now):
        qs = [self.peer_q(p, now) for p in self.peers.values()
              if p.last_rx is not None and now - p.last_rx < T_DEAD]
        return sum(qs) / len(qs) if qs else 0.0

    def periods(self, q):
        if q >= 0.7:
            return 0.5, 1.0, 2.0, 1500.0
        if q >= 0.4:
            return 1.0, 2.0, 4.0, 800.0
        return 2.0, 3.0, 6.0, 450.0

    def alive_set(self, now):
        alive = {ME}
        for j, p in self.peers.items():
            if j in self.force_dead:
                continue
            if p.last_rx is not None and now - p.last_rx < T_DEAD:
                alive.add(j)
                continue
            for k, pk in self.peers.items():
                if (k != j and k not in self.force_dead and pk.heard_rx is not None
                        and now - pk.heard_rx < T_DEAD and (pk.heard >> (j - 1)) & 1):
                    alive.add(j)
                    break
        return alive

    def heard_mask(self, now):
        m = 0
        for j, p in self.peers.items():
            if p.last_rx is not None and now - p.last_rx < max(2.5, 2.0 * p.hb_s):
                m |= 1 << (j - 1)
        return m

    def _try_load_manifest(self, now):
        try:
            with open(self.manifest_file, "r", encoding="utf-8") as f:
                m = json.load(f)
            bad = manifest_problem(m, common.COLS * common.ROWS)
            if bad:
                if bad != getattr(self, "_manifest_bad", None):      # не засорять лог каждым тактом ожидания
                    self._manifest_bad = bad
                    self.log(f"manifest отклонён: {bad}")
                return False
            self.active_zones, self.manifest = set(m["active_sectors"]), m
            self.log(f"manifest загружен: zones={m.get('zones_named')} sectors={sorted(self.active_zones)} on_target={m.get('on_target')}")
            return True
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            return False

    def _zone_of(self, j):
        if j < common.COLS * common.ROWS:
            return j
        if j in getattr(common, "TARGET_PARENT_SECTOR", {}):
            return common.TARGET_PARENT_SECTOR[j]
        return (j - 10) // 2

    def open_tasks(self):
        if self.active_zones is None:
            return []
        return [j for j in sorted(self.tasks) if not (self.done >> j) & 1 and self._zone_of(j) in self.active_zones]

    # ───────── ставки и CBAA ─────────
    def bid_for(self, j, now):
        if self.withdrawn:
            return 0
        t = self.tasks[j]
        x, y, _ = self.pos()
        dx, dy = t.cx - x, t.cy - y
        dist = math.hypot(dx, dy)
        s = (1.0 if t.kind == "TARGET" else 0.8) / (1.0 + dist / 25.0)

        # Векторное выравнивание по скорости (устраняет слепые зоны на границах секторов)
        vx = float(self.nav.get("vx", 0.0))
        vy = float(self.nav.get("vy", 0.0))
        vmag = math.hypot(vx, vy)
        if vmag > 0.5 and dist > 2.0:
            cos_theta = (vx * dx + vy * dy) / (vmag * dist)
            s *= (1.0 + 0.25 * cos_theta)

        if self.nav_degraded():
            s *= 0.6
        if j == self.exec_task:
            s *= 1.4
        if self.prefer.get(j, 0.0) > now:
            s *= 1.5
        return max(1, int(round(min(1.5, s) * 2700)))

    def _track_x(self, now):
        if self.cbaa.x != self._x_last:
            self._x_last, self.x_since = self.cbaa.x, now

    def reset_cbaa(self, now):
        avail = set(self.open_tasks())
        self.cbaa.reset({j: self.bid_for(j, now) for j in avail}, avail)
        self.cbaa.select()
        self.vec_changed, self.claim_dirty = now, True
        self._track_x(now)

    def adopt(self, epoch, now, why):
        self.epoch = epoch & 0xFF
        self.reset_cbaa(now)
        self.bump_gate.absorb()             # чужой bump уже перезапустил аукцион; свой отложенный bump по прибытию лишний
        self.log(f"epoch -> {self.epoch} ({why})")

    def bump(self, why, now):
        self.epoch = (self.epoch + 1) & 0xFF
        self.last_bump, self.alloc_now = now, True
        self.reset_cbaa(now)
        self.log(f"epoch -> {self.epoch} bump: {why}")

    def mark_done(self, j, now):
        self.done |= 1 << j
        self.exec_task = None
        self.log(f"task {self.tasks[j].name} DONE")
        self.bump("task-done", now)

    # ───────── приём из сети (поток Zenoh) ─────────
    def _on_wire(self, data):
        m = wire.unseal(data)
        if m is None:
            self.bad_tag += 1
            return
        mtype, s, seq, _t, body = m
        if s == ME or not 1 <= s <= N:
            return
        try:
            with self.lock:
                now = time.monotonic()
                if mtype == wire.T_HB:
                    self._on_hb(s, seq, body, now)
                elif mtype == wire.T_CLAIM:
                    self._on_claim(s, body, now)
                elif mtype == wire.T_ALLOC:
                    self._on_alloc(s, body, now)
                elif mtype == wire.T_FAIL:
                    self._on_fail(s, body, now)
        except Exception as e:  # noqa: BLE001
            self.get_logger().warning(f"rx error type={mtype} from {s}: {e}")

    def _on_hb(self, s, seq, body, now):
        d, p = wire.dec_hb(body), self.peers[s]
        if p.seq is not None:
            gap = (seq - p.seq) & 0xFFFF
            if gap == 0:
                return
            if gap <= 200:
                for _ in range(gap - 1):
                    p.q *= 0.9
            p.q = p.q * 0.9 + 0.1
        p.seq, p.last_rx, p.rx = seq, now, p.rx + 1
        p.x, p.y, p.z, p.state, p.flags = d["x"], d["y"], d["z"], d["state"], d["flags"]
        p.sigma, p.task, p.epoch, p.link_q = d["sigma"], d["task"], d["epoch"], d["link_q"]
        p.heard, p.heard_rx, p.hb_s = d["heard"], now, max(0.5, d["hb_s"])
        self.force_dead.discard(s)
        if sgt(d["epoch"], self.epoch):
            self.adopt(d["epoch"], now, f"hb from {s}")

    def _on_claim(self, s, body, now):
        d = wire.dec_claim(body)
        if sgt(d["epoch"], self.epoch):
            self.adopt(d["epoch"], now, f"claim from {s}")
        if d["epoch"] != self.epoch:
            return
        y = (d["y"] + [0] * M)[:M]
        z = (d["z"] + [0] * M)[:M]
        if self.cbaa.merge(y, z):
            self.vec_changed, self.claim_dirty = now, True
            self._track_x(now)

    def _on_alloc(self, s, body, now):
        d = wire.dec_alloc(body)
        if sgt(d["epoch"], self.epoch):
            self.adopt(d["epoch"], now, f"alloc from {s}")
        new_done = d["done"] & ~self.done
        new_split = (d["split2"] & ~self.split2) or (d["split3"] & ~self.split3)
        self.done |= d["done"]
        self.split2 |= d["split2"]
        self.split3 |= d["split3"]
        if new_split:
            self.tasks = common.build_tasks(self.split2, self.split3)
            self.reset_cbaa(now)
        elif new_done:
            self.cbaa.drop([j for j in range(M) if (new_done >> j) & 1])
            self.claim_dirty = True
            self._track_x(now)

    def _on_fail(self, s, body, now):
        d = wire.dec_fail(body)
        if sgt(d["epoch"], self.epoch):
            self.adopt(d["epoch"], now, f"failover from {s}")
        f = d["failed"]
        if f in self.peers and f != s:
            self.force_dead.add(f)
            self.cbaa.drop_peer(f)
            self.reset_cbaa(now)
            self.log(f"peer {f} declared FAILED by {s}")

    # ───────── ROS-вход ─────────
    def _on_nav(self, msg):
        try:
            with self.lock:
                self.nav = json.loads(msg.data)
        except json.JSONDecodeError:
            pass

    def _on_phase(self, msg):
        try:
            p = A.Phase(json.loads(msg.data)["phase"])
        except (ValueError, KeyError, json.JSONDecodeError):
            return
        with self.lock:
            if p != self.phase:
                self.phase, self.phase_t = p, time.monotonic()
                self.log(f"phase -> {p.value}")

    def _on_plog(self, msg):
        try:
            d = json.loads(msg.data)
            if d.get("verdict") in ("ACCEPTED", "REJECTED", "NOOP"):
                with self.lock:
                    self.llm_ok_t = time.monotonic()
        except json.JSONDecodeError:
            pass

    def _on_cmd(self, msg):
        try:
            meta, act = A.parse_cmd(json.loads(msg.data))
        except Exception as e:  # noqa: BLE001
            with self.lock:
                self.last_cmd = {"id": None, "tool": None, "source": None, "ok": False, "msg": f"schema: {str(e)[:80]}"}
            self.get_logger().warning(f"bad cmd: {str(e)[:120]}")
            return
        with self.lock:
            now = time.monotonic()
            age = now - meta.t_mono
            if age > A.MAX_AGE_S[meta.source]:
                ok, text = False, f"stale {age:.1f}s"
            elif meta.phase != self.phase and now - self.phase_t > 1.0:
                ok, text = False, f"phase mismatch {meta.phase.value}!={self.phase.value}"
            elif not A.permitted(self.phase, meta.source, act.tool):
                ok, text = False, f"{act.tool} not permitted in {self.phase.value}"
            else:
                ok, text = self._apply_cmd({"tool": act.tool, "args": act.args.model_dump()}, now)
            self.last_cmd = {"id": meta.id, "tool": act.tool, "source": meta.source, "ok": ok, "msg": text}
            self.log(f"cmd {meta.source}:{act.tool} -> {'OK' if ok else 'REJECT'}: {text}")

    def _apply_cmd(self, cmd, now):
        tool, a = cmd.get("tool"), cmd.get("args", {})
        if tool in ("HOLD_SECTOR", "EVADE_JAMMING", "NAVIGATE_TO") and self.override:
            return False, "busy: override active"
        if tool == "HOLD_SECTOR":
            j = self.exec_task if self.exec_task is not None else self.cbaa.x
            if j is None:
                return False, "no sector to hold"
            t = self.tasks[j]
            self._send_goal("WAYPOINTS", [(t.cx, t.cy)])
            self.override = {"seq": self.goal_seq, "kind": "HOLD_SECTOR", "stage": "reach",
                             "hold_s": float(a.get("hold_s", 20)), "until": now + 90.0}
            return True, f"hold {t.name} {a.get('hold_s', 20)}s"
        if tool == "EVADE_JAMMING":
            x, y, _ = self.pos()
            md = float(a.get("min_dist_m", 15.0))
            c = next((c for c in reversed(self.crumbs) if math.hypot(c[1] - x, c[2] - y) >= md), None)
            if c is None:
                return False, "no good-link breadcrumb"
            self._send_goal("WAYPOINTS", [(c[1], c[2])])
            self.override = {"seq": self.goal_seq, "kind": "EVADE", "stage": "reach",
                             "hold_s": float(a.get("hold_s", 30)), "until": now + 90.0}
            return True, f"evade to ({c[1]:.0f},{c[2]:.0f})"
        if tool == "NAVIGATE_TO":
            self._send_goal("WAYPOINTS", [(float(a["x"]), float(a["y"]))])
            self.override = {"seq": self.goal_seq, "until": now + 90.0, "kind": "WAYPOINTS"}
            return True, f"override to ({a['x']},{a['y']})"
        if tool == "SCAN_TARGET":
            tid = 6 + int(a["target"])
            if tid not in self.tasks or (self.done >> tid) & 1:
                return False, "target not open"
            self.prefer[tid] = now + 60.0
            self.bump("scan-target", now)
            return True, f"prefer {self.tasks[tid].name}"
        if tool == "SPLIT_SECTOR":
            p, parts = int(a["sector"]), int(a["parts"])
            if not 0 <= p < common.COLS * common.ROWS or parts not in (2, 3):
                return False, "bad sector/parts"
            if (self.done >> p) & 1 or (self.split2 >> p) & 1:
                return False, "sector done or already split"
            self.split2 |= 1 << p
            if parts == 3:
                self.split3 |= 1 << p
            self.tasks = common.build_tasks(self.split2, self.split3)
            self.bump("split-sector", now)
            return True, f"S{p} split into {parts}"
        if tool == "DECLARE_FAILOVER":
            f = int(a["failed"])
            p = self.peers.get(f)
            if p is None:
                return False, "unknown peer"
            if f in self.alive_set(now):
                return False, "peer still alive"
            self.force_dead.add(f)
            self.cbaa.drop_peer(f)
            reason = wire.REASONS.get(a.get("reason", "NO_HEARTBEAT"), 0)
            self.bump("failover", now)
            self.fail_tx = [(now + i * 1.0, f, reason) for i in range(5)]
            return True, f"peer {f} declared failed"
        if tool == "HOLD_POSITION":
            self._send_goal("HOLD")
            self.override = {"seq": self.goal_seq, "until": now + 15.0, "kind": "HOLD"}
            return True, "hold 15 s"
        if tool == "RETURN_HOME":
            self.withdrawn = True
            self.override = None
            self.exec_task = None
            self._send_goal("HOME", [common.HOME])
            self.bump("withdraw", now)
            return True, "returning home"
        return False, "unknown tool"

    # ───────── цели полёта ─────────
    def _send_goal(self, kind, wps=None, dwell=0.0, task=wire.NONE, alt=None):
        self.goal_seq += 1
        self.goal = {"seq": self.goal_seq, "kind": kind, "wps": [list(w) for w in (wps or [])],
                     "dwell_s": dwell, "task": task, "alt": alt if alt is not None else common.ALT}
        self._pub_goal()

    def _pub_goal(self):
        if self.goal:
            self.pub_goal.publish(String(data=json.dumps(self.goal)))
            self.t_goal_pub = time.monotonic()

    def _start_task(self, j, now):
        t = self.tasks[j]
        dwell = common.MISSION["target_dwell_s"] if t.kind == "TARGET" else 0.0
        self._send_goal("WAYPOINTS", common.task_waypoints(t), dwell, j)
        self.exec_task = j
        x, y, _ = self.pos()
        self.exec_dist_min = math.hypot(t.cx - x, t.cy - y)
        self.exec_dist_t = now
        self.log(f"start task {t.name} ({len(self.goal['wps'])} wps)")

    def preempt_task(self, j, reason, now):
        """Аварийный сброс захвата цели (Preemption Lease) для мгновенного подхвата ведомым."""
        t = self.tasks[j]
        self.log(f"task {t.name} PREEMPTED: {reason} (released to peers)")
        self.exec_task = None
        self.prefer[j] = 0.0
        self.cbaa.drop_task(j)
        self._send_goal("HOLD")
        self.claim_dirty = True
        self.bump(f"preempt-{reason}", now)

    # ───────── главный цикл ─────────
    def tick(self):
        with self.lock:
            now = time.monotonic()
            if self.active_zones is None:
                got = self._try_load_manifest(now)
                if not got and now - self.manifest_wait_t0 <= self.manifest_wait_timeout_s:
                    return   # борт на земле/HOLD, манифест ещё не пришёл
                if self.active_zones is None:   # таймаут без приказа — safety net, не демо-путь
                    self.active_zones = set(range(common.COLS * common.ROWS))
                    self.log("manifest timeout -> fallback: все сектора активны")
                self.reset_cbaa(now)
            self._liveness(now)
            self._exec(now)
            self._tx(now)
            if now - self.t_crumb >= 2.0:
                self.t_crumb = now
                x, y, z = self.pos()
                if z > 1.0 and self.link_quality(now) >= 0.7 and self.heard_mask(now):
                    self.crumbs.append((now, x, y))
            if self.goal and now - self.t_goal_pub > 2.0:
                self._pub_goal()
            if now - self.t_state >= 0.2:
                self.t_state = now
                self._publish_state(now)

    def _liveness(self, now):
        alive = self.alive_set(now)
        if alive != self.alive_prev:
            left, joined = self.alive_prev - alive, alive - self.alive_prev
            for g in sorted(left):
                self.log(f"peer {g} DEAD")
            for g in sorted(joined):
                self.log(f"peer {g} ALIVE")
            self.alive_prev = alive
            self.bump_gate.observe(bool(joined), bool(left), now)
        if self.bump_gate.due(now, self.last_bump):
            self.bump_gate.fire()
            self.bump("alive-set", now)

    def _exec(self, now):
        if not self.nav:
            return
        if self.exec_task is not None and (self.done >> self.exec_task) & 1:
            self.exec_task = None
            self._send_goal("HOLD")
        ov = self.override
        if ov:
            if ov["kind"] in ("HOLD_SECTOR", "EVADE"):
                if ov["stage"] == "reach" and self.nav.get("seq") == ov["seq"] and self.nav.get("done"):
                    ov["stage"], ov["until"] = "hold", now + ov["hold_s"]
                    self._send_goal("HOLD")
                if now > ov["until"]:
                    self.override, self.exec_task = None, None
                    self.log(f"{ov['kind']} finished")
                return
            fin = ov["kind"] == "WAYPOINTS" and self.nav.get("seq") == ov["seq"] and self.nav.get("done")
            if fin or now > ov["until"]:
                self.override, self.exec_task = None, None
                self.log("override finished")
            else:
                return
        if (self.exec_task is not None and self.goal and self.goal["kind"] == "WAYPOINTS"
                and self.nav.get("seq") == self.goal_seq and self.nav.get("done")):
            self.mark_done(self.exec_task, now)
            return
        if self.withdrawn:
            return
        if self.exec_task is not None and not self.override:
            t = self.tasks[self.exec_task]
            x, y, _ = self.pos()
            cur_dist = math.hypot(t.cx - x, t.cy - y)
            if cur_dist < self.exec_dist_min - 1.0:
                self.exec_dist_min = cur_dist
                self.exec_dist_t = now
            elif now - self.exec_dist_t > 8.0 and t.kind == "TARGET" and self.nav_degraded():
                self.preempt_task(self.exec_task, "stalled_degraded", now)
                return
        if not self.open_tasks():
            if not self.mission_done_sent:
                self.mission_done_sent, self.exec_task = True, None
                self._send_goal("HOME", [common.HOME])
                self.log("mission complete, returning home")
            return
        x = self.cbaa.x
        if now - self.x_since < SETTLE_S:
            return
        if x is None:
            if self.exec_task is not None:
                self.exec_task = None
                self._send_goal("HOLD")
        elif x != self.exec_task:
            self._start_task(x, now)

    def _tx(self, now):
        q = self.link_quality(now)
        hb_p, cl_p, al_p, rate = self.periods(q)
        self.tokens = min(600.0, self.tokens + rate * (now - self.t_refill))
        self.t_refill = now
        while self.fail_tx and self.fail_tx[0][0] <= now:
            _, f, r = self.fail_tx.pop(0)
            self._send("fail", wire.T_FAIL, wire.enc_fail(f, r, self.epoch), force=True)
        if now >= self.t_hb:
            self.t_hb = now + hb_p
            x, y, z = self.pos()
            n = self.nav
            fl = ((wire.FL_GPS_OK if n.get("gps_fix", 0) >= 3 else 0)
                  | (wire.FL_VIO_OK if n.get("vio_ok") else 0)
                  | (wire.FL_NAV_DEG if self.nav_degraded() else 0)
                  | (wire.FL_LLM_OK if now - self.llm_ok_t < 90.0 else 0)
                  | (wire.FL_AIRBORNE if z > 0.5 else 0)
                  | (wire.FL_GPS_SUSPECT if n.get("gps_suspect") else 0))
            body = wire.enc_hb(x, y, z, STATE_CODE.get(n.get("state", "IDLE"), 0), fl, n.get("sigma", 0.0),
                               self.exec_task if self.exec_task is not None else wire.NONE,
                               self.epoch, q * 100.0, self.heard_mask(now), hb_p)
            self._send("hb", wire.T_HB, body)
        unstable = now - self.vec_changed < 8.0
        if now >= self.t_claim or (self.claim_dirty and now - self.t_claim_last >= cl_p / 2.0):
            self.t_claim, self.t_claim_last, self.claim_dirty = now + (cl_p if unstable else 3 * cl_p), now, False
            self._send("claim", wire.T_CLAIM, wire.enc_claim(self.epoch, self.cbaa.y, self.cbaa.z))
        if now >= self.t_alloc or self.alloc_now:
            self.t_alloc, self.alloc_now = now + al_p, False
            alive = self.alive_set(now)
            assign = [wire.NONE] * 8
            for a in range(1, N + 1):
                if a == ME:
                    assign[a - 1] = self.exec_task if self.exec_task is not None else wire.NONE
                elif a in alive:
                    assign[a - 1] = self.peers[a].task
            amask = sum(1 << (a - 1) for a in alive)
            self._send("alloc", wire.T_ALLOC,
                       wire.enc_alloc(self.epoch, self.done, self.split2, self.split3, assign, amask))

    def _send(self, kind, mtype, body, force=False):
        cost = (len(body) + wire.HDR.size + wire.TAG_LEN + 48) * (N - 1)
        if self.tokens < cost and not force:
            self.dropped[kind] += 1
            return
        self.tokens -= cost
        self.seqs[mtype] = (self.seqs[mtype] + 1) & 0xFFFF
        data = wire.seal(mtype, ME, self.seqs[mtype], int(time.monotonic() * 1000), body)
        self.mesh.send(kind, data)

    def _publish_state(self, now):
        alive = self.alive_set(now)
        q = self.link_quality(now)
        hb_p, cl_p, al_p, rate = self.periods(q)
        peers = {}
        for j, p in self.peers.items():
            silent = now - (p.last_rx if p.last_rx is not None else self.t0)
            peers[str(j)] = {
                "alive": j in alive, "declared_failed": j in self.force_dead, "silent_s": round(silent, 1),
                "q": round(self.peer_q(p, now), 2), "x": p.x, "y": p.y, "z": p.z,
                "task": p.task, "state": wire.NONE if p.last_rx is None else p.state,
                "flags": p.flags, "epoch": p.epoch,
                "heard": [k for k in range(1, 9) if (p.heard >> (k - 1)) & 1]}
        opn = [{"id": j, "name": self.tasks[j].name, "kind": self.tasks[j].kind,
                "cx": round(self.tasks[j].cx, 1), "cy": round(self.tasks[j].cy, 1),
                "area": round(self.tasks[j].area),
                "splittable": j < common.COLS * common.ROWS and not (self.split2 >> j) & 1}
               for j in self.open_tasks()]
        x, y, z = self.pos()
        st = {"t": round(now - self.t0, 1), "me": ME, "epoch": self.epoch, "pos": [x, y, z], "nav": self.nav,
              "task": self.exec_task, "task_name": self.tasks[self.exec_task].name if self.exec_task is not None else None,
              "cbaa_x": self.cbaa.x, "winners": {str(j): self.cbaa.z[j] for j in range(M) if self.cbaa.z[j]},
              "done": [j for j in range(M) if (self.done >> j) & 1], "total_tasks": len(self.tasks),
              "open": opn, "alive": sorted(alive), "link_q": round(q, 2),
              "periods": {"hb": hb_p, "claim": cl_p, "alloc": al_p, "rate_Bps": rate},
              "heard": [k for k in range(1, 9) if (self.heard_mask(now) >> (k - 1)) & 1],
              "peers": peers, "events": list(self.events)[-10:], "last_cmd": self.last_cmd,
              "override": self.override["kind"] if self.override else None, "withdrawn": self.withdrawn,
              "phase": self.phase.value,
              "mesh": {"ready": self.mesh.ready, "tuned": self.mesh.tuned, "zenoh_peers": self.mesh.peers_connected(),
                       "dropped": self.dropped, "bad_tag": self.bad_tag, "tx_bytes": self.mesh.tx_bytes}}
        payload = json.dumps(st)
        self.pub_state.publish(String(data=payload))
        try:
            with open("/dev/shm/swarm_state.json", "w") as f:
                f.write(payload)
        except Exception:
            pass


def main():
    rclpy.init()
    node = Coordinator()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
