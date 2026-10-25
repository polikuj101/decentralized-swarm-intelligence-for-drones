#!/usr/bin/env python3
"""flight_ctrl: offboard-управление PX4 по скорости, навигация по целям от coordinator, обход, монитор GPS."""
import json
import math
import os
import time
from collections import deque

import rclpy
from geometry_msgs.msg import PointStamped
from px4_msgs.msg import (OffboardControlMode, SensorGps, TrajectorySetpoint, VehicleCommand,
                          VehicleLocalPosition, VehicleStatus)
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from sensor_msgs.msg import LaserScan
from std_msgs.msg import String

from swarm import common
from swarm.supervise import DisarmWatch

MS = common.MISSION
VMAX, WP_R, HOME, ALT = MS["cruise_speed_mps"], MS["wp_radius_m"], common.HOME, common.ALT
KP, KZ, VZ_UP, VZ_DN = 0.8, 1.0, 1.2, 0.8
AUTO_REARM = os.environ.get("FLIGHT_AUTO_REARM", "0") == "1"       # по умолчанию борт после разоружения/посадки сам не взлетает
R_AVOID, R_STOP, K_REP = 4.0, 1.3, 2.0
PX4_QOS = QoSProfile(reliability=ReliabilityPolicy.BEST_EFFORT, durability=DurabilityPolicy.TRANSIENT_LOCAL,
                     history=HistoryPolicy.KEEP_LAST, depth=1)


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


class Flight(Node):
    def __init__(self):
        super().__init__("flight_ctrl")
        ns = f"/{common.PX4_NS}/fmu"
        self.p_mode = self.create_publisher(OffboardControlMode, f"{ns}/in/offboard_control_mode", PX4_QOS)
        self.p_sp = self.create_publisher(TrajectorySetpoint, f"{ns}/in/trajectory_setpoint", PX4_QOS)
        self.p_cmd = self.create_publisher(VehicleCommand, f"{ns}/in/vehicle_command", PX4_QOS)
        self.p_status = self.create_publisher(String, "/swarm/local/nav_status", 10)
        self.create_subscription(VehicleLocalPosition, f"{ns}/out/vehicle_local_position", self._on_lpos, qos_profile_sensor_data)
        self.create_subscription(VehicleStatus, f"{ns}/out/vehicle_status", self._on_vs, qos_profile_sensor_data)
        self.create_subscription(SensorGps, f"{ns}/out/vehicle_gps_position", self._on_gps, qos_profile_sensor_data)
        self.create_subscription(LaserScan, "/sensors/lidar_ring/scan", self._on_scan, qos_profile_sensor_data)
        self.create_subscription(PointStamped, "/swarm/local/vio_disp", self._on_vio, 10)
        self.create_subscription(String, "/swarm/local/nav_goal", self._on_goal, 10)

        self.lp = self.vs = self.scan = self.vio = self.anc = None
        self.gps_fix, self.scan_t = 0, 0.0
        self.px, self.py = common.SPAWN
        self.pz, self.hdg, self.ve, self.vn, self.sigma, self.src = 0.0, 0.0, 0.0, 0.0, 5.0, "NONE"
        self.suspect, self.bad, self.good, self.hist = False, 0, 0, deque()
        self.state, self.label = "WAIT", "IDLE"
        self.disarm = DisarmWatch()
        self.ready_t, self.t_cmd = None, 0.0
        self.goal, self.seq, self.idx, self.done, self.dwell_t0 = None, None, 0, False, None
        self.obst_min = 99.0
        self._t_last = self._t_mon = self._t_stat = time.monotonic()
        self.create_timer(0.05, self._tick)

    # ───────── подписки ─────────
    def _on_lpos(self, m):
        self.lp = m

    def _on_vs(self, m):
        self.vs = m

    def _on_gps(self, m):
        self.gps_fix = int(m.fix_type)

    def _on_scan(self, m):
        self.scan, self.scan_t = m, time.monotonic()

    def _on_vio(self, m):
        self.vio = (m.point.x, m.point.y, time.monotonic())

    def _on_goal(self, msg):
        try:
            g = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        if g.get("seq") == self.seq:
            return
        self.goal, self.seq, self.idx, self.done, self.dwell_t0 = g, g["seq"], 0, False, None
        if AUTO_REARM and self.state == "LANDED" and g.get("kind") == "WAYPOINTS":      # только по явному разрешению оператора
            self.get_logger().info("new waypoints after landing -> WAIT (re-arm and take off)")
            self.state, self.label, self.ready_t = "WAIT", "IDLE", None

    def _ts(self):
        return int(self.get_clock().now().nanoseconds / 1000)

    def _cmd(self, command, p1=0.0, p2=0.0):
        m = VehicleCommand()
        m.timestamp, m.command, m.param1, m.param2 = self._ts(), command, float(p1), float(p2)
        m.target_system, m.target_component = common.DRONE_ID, 1
        m.source_system, m.source_component, m.from_external = 1, 1, True
        self.p_cmd.publish(m)

    # ───────── оценка позиции ─────────
    def _estimate(self, now, dt):
        lp = self.lp
        if lp is None:
            return False
        wx, wy = common.ned_to_world(lp.x, lp.y)
        self.pz, self.hdg, self.ve, self.vn = -lp.z, lp.heading, lp.vy, lp.vx
        vio_fresh = self.vio is not None and now - self.vio[2] < 1.0
        if lp.xy_valid and not self.suspect:
            self.px, self.py, self.src = wx, wy, "PX4"
            self.sigma = float(lp.eph) if math.isfinite(lp.eph) else 5.0
            if vio_fresh:
                self.anc = (wx, wy, self.vio[0], self.vio[1])
        elif vio_fresh and self.anc is not None:
            self.px = self.anc[0] + self.vio[0] - self.anc[2]
            self.py = self.anc[1] + self.vio[1] - self.anc[3]
            self.sigma = min(100.0, self.sigma + (0.02 * math.hypot(self.ve, self.vn) + 0.02) * dt)
            self.src = "VIO"
        elif lp.v_xy_valid:
            self.px, self.py = self.px + self.ve * dt, self.py + self.vn * dt
            self.sigma = min(100.0, self.sigma + (0.05 * math.hypot(self.ve, self.vn) + 0.1) * dt)
            self.src = "DR"
        else:
            self.src = "NONE"
        return self.src != "NONE"

    def _monitor(self, now):
        lp = self.lp
        if not (lp and lp.xy_valid and self.vio and now - self.vio[2] < 2.0):
            self.bad = self.good = 0
            return
        wx, wy = common.ned_to_world(lp.x, lp.y)
        self.hist.append((now, wx, wy, self.vio[0], self.vio[1]))
        while self.hist and now - self.hist[0][0] > 20.0:
            self.hist.popleft()
        ref = next((h for h in self.hist if now - h[0] <= 10.0), None)
        if ref is None or now - ref[0] < 6.0:
            return
        dvo = (self.vio[0] - ref[3], self.vio[1] - ref[4])
        diff = math.hypot((wx - ref[1]) - dvo[0], (wy - ref[2]) - dvo[1])
        if diff > 3.0 + 0.15 * math.hypot(*dvo):
            self.bad, self.good = self.bad + 1, 0
        else:
            self.good, self.bad = self.good + 1, 0
        if not self.suspect and self.bad >= 3:
            self.suspect = True
            old = [h for h in self.hist if now - h[0] >= 8.0]
            h = old[-1] if old else self.hist[0]
            self.anc = (h[1], h[2], h[3], h[4])
            self.get_logger().warning(f"GPS SUSPECT: px4-vio mismatch {diff:.1f} m -> nav source VIO")
        elif self.suspect and self.good >= 8:
            self.suspect = False
            self.get_logger().info("GPS consistent again -> nav source PX4")

    # ───────── обход препятствий ─────────
    def _avoid(self, vgx, vgy, now):
        sc = self.scan
        if sc is None or now - self.scan_t > 0.5:
            self.obst_min = 99.0
            return vgx, vgy
        psi = math.pi / 2.0 - self.hdg          # курс NED -> yaw ENU
        rx = ry = 0.0
        dmin = 99.0
        for i, r in enumerate(sc.ranges):
            if not math.isfinite(r) or r >= R_AVOID:
                continue
            r = max(r, sc.range_min, 0.1)
            a = psi + sc.angle_min + i * sc.angle_increment
            w = 1.0 / r - 1.0 / R_AVOID
            rx -= w * math.cos(a)
            ry -= w * math.sin(a)
            dmin = min(dmin, r)
        self.obst_min = dmin
        vx, vy = vgx, vgy
        if dmin < R_AVOID:
            g = math.hypot(vgx, vgy)
            k = clamp((dmin - 0.5) / (R_STOP - 0.5), 0.0, 1.0) if dmin < R_STOP else 1.0
            vx, vy = vgx * k + K_REP * rx, vgy * k + K_REP * ry
            mag = math.hypot(rx, ry)
            if g > 1e-3 and mag > 1e-3:
                vx += 0.5 * K_REP * mag * (-vgy / g)     # тангенциальная составляющая (обход влево)
                vy += 0.5 * K_REP * mag * (vgx / g)
        n = math.hypot(vx, vy)
        if n > VMAX:
            vx, vy = vx * VMAX / n, vy * VMAX / n
        return vx, vy

    # ───────── главный цикл ─────────
    def _tick(self):
        now = time.monotonic()
        dt = clamp(now - self._t_last, 0.0, 0.5)
        self._t_last = now
        mode = OffboardControlMode()
        mode.timestamp = self._ts()
        mode.position = mode.acceleration = mode.attitude = mode.body_rate = False
        mode.velocity = True
        self.p_mode.publish(mode)

        have = self._estimate(now, dt)
        if now - self._t_mon >= 1.0:
            self._t_mon = now
            self._monitor(now)

        vx = vy = vz = 0.0
        st = self.state
        if st in ("TAKEOFF", "FLY") and self.vs is not None and self.disarm.update(self.vs.arming_state == 2, now):
            # PX4 разоружился после взлёта: раньше борт оставался в FLY и продолжал показывать NAV, сидя на земле
            # Разоружение могло быть аварийным (failsafe, kill switch): сами заново не взводим, если оператор не включил FLIGHT_AUTO_REARM=1
            self.get_logger().warning("PX4 disarmed after takeoff -> " + ("re-arm" if AUTO_REARM else "GROUNDED, no automatic re-arm"))
            self.state, self.label, self.done, self.ready_t = ("WAIT" if AUTO_REARM else "GROUNDED"), "GROUNDED", False, None
            self.disarm.reset()
            st = self.state
        if st == "WAIT":
            ok = (self.lp is not None and self.lp.z_valid and (self.lp.xy_valid or self.lp.v_xy_valid))
            if ok:
                self.ready_t = self.ready_t or now
                if now - self.ready_t > 3.0:
                    self.state, self.t_cmd = "ARMING", 0.0
            else:
                self.ready_t = None
        elif st == "ARMING":
            if self.vs and self.vs.arming_state == 2:
                self.state, self.label = "TAKEOFF", "TAKEOFF"
            elif now - self.t_cmd > 1.0:
                self.t_cmd = now
                self._cmd(VehicleCommand.VEHICLE_CMD_DO_SET_MODE, 1.0, 6.0)
                self._cmd(VehicleCommand.VEHICLE_CMD_COMPONENT_ARM_DISARM, 1.0)
        elif st == "TAKEOFF":
            vz = clamp(KZ * (ALT - self.pz), -VZ_DN, VZ_UP)
            if self.pz > ALT - 0.3:
                self.state, self.label = "FLY", "HOLD"
        elif st == "FLY":
            vx, vy, vz = self._fly(now, have)
        elif st == "LAND":
            vz = -VZ_DN * 0.75
            if self.pz < 0.2:
                self._cmd(VehicleCommand.VEHICLE_CMD_COMPONENT_ARM_DISARM, 0.0)
                self.state, self.label = "LANDED", "LANDED"

        sp = TrajectorySetpoint()
        sp.timestamp = self._ts()
        nan = float("nan")
        sp.position, sp.acceleration, sp.jerk = [nan] * 3, [nan] * 3, [nan] * 3
        sp.velocity = [float(vy), float(vx), float(-vz)]       # ENU -> NED: vN = vy, vE = vx, vD = -vz
        sp.yaw, sp.yawspeed = nan, 0.0
        self.p_sp.publish(sp)

        if now - self._t_stat >= 0.2:
            self._t_stat = now
            self._publish_status()

    def _fly(self, now, have):
        g = self.goal
        alt = g["alt"] if g else ALT
        vz = clamp(KZ * (alt - self.pz), -VZ_DN, VZ_UP)
        if not have or g is None or g["kind"] == "HOLD" or (self.done and g["kind"] == "WAYPOINTS"):
            self.label = "HOLD"
            return 0.0, 0.0, vz
        wps = g["wps"]
        if self.idx >= len(wps):
            self.done = True
            if g["kind"] == "HOME":
                self.state, self.label = "LAND", "RTB"
            return 0.0, 0.0, vz
        tx, ty = wps[self.idx]
        dx, dy = tx - self.px, ty - self.py
        dist = math.hypot(dx, dy)
        self.label = "RTB" if g["kind"] == "HOME" else "NAV"
        if dist < WP_R:
            last = self.idx == len(wps) - 1
            if last and g.get("dwell_s", 0.0) > 0.0:
                self.label = "DWELL"
                self.dwell_t0 = self.dwell_t0 or now
                if now - self.dwell_t0 < g["dwell_s"]:
                    return 0.0, 0.0, vz
            self.idx, self.dwell_t0 = self.idx + 1, None
            return 0.0, 0.0, vz
        v = min(VMAX, max(0.5, KP * dist))
        vx, vy = self._avoid(dx / dist * v, dy / dist * v, now)
        return vx, vy, vz

    def _publish_status(self):
        n = self.lp
        st = {"seq": self.seq, "idx": self.idx, "done": bool(self.done), "state": self.label, "phase": self.state,
              "x": round(self.px, 2), "y": round(self.py, 2), "z": round(self.pz, 2),
              "vx": round(self.ve, 2), "vy": round(self.vn, 2), "hdg": round(math.degrees(self.hdg), 1),
              "src": self.src, "sigma": round(self.sigma, 1), "gps_fix": self.gps_fix, "gps_suspect": self.suspect,
              "vio_ok": bool(self.vio and time.monotonic() - self.vio[2] < 1.0),
              "xy_valid": bool(n and n.xy_valid), "armed": bool(self.vs and self.vs.arming_state == 2),
              "obst_min": round(self.obst_min, 2),
              "capture": bool(self.goal.get("capture", True)) if self.goal else True}
        self.p_status.publish(String(data=json.dumps(st)))


def main():
    rclpy.init()
    node = Flight()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
