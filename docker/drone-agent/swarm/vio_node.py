#!/usr/bin/env python3
"""vio_node: оптический поток нижней камеры + высота -> VehicleOdometry (скорость, NED) для EKF2."""
import json
import math
import os
import time
from collections import deque

import numpy as np
import rclpy
from geometry_msgs.msg import PointStamped, Vector3Stamped
from px4_msgs.msg import SensorGps, VehicleAttitude, VehicleLocalPosition, VehicleOdometry
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from sensor_msgs.msg import CameraInfo, Image, Range
from std_msgs.msg import String

from swarm import common
from swarm.altitude import object_below, smooth_alpha, vio_jump, vio_outlier
from swarm.flow import validated_shift

RESP_MIN = float(os.environ.get("VIO_RESP_MIN", "0.08"))
FLIP_X = float(os.environ.get("VIO_FLIP_X", "1"))
FLIP_Y = float(os.environ.get("VIO_FLIP_Y", "1"))
# Поворот борта между кадрами сдвигает картинку так же, как перемещение на высота × угол. Прогон 2026-10-26 (5 бортов, 2 761-2 781 кадр
# на борт, docs/gz_leak/vio_eval.py): остаток ошибки ровно +1,0 × h·Δтангаж вперёд и −1,0 × h·Δкрен вправо; с поправкой −1 покадровая
# ошибка p90 падает с 12-28 см до 1,5-10 см. 0 — без поправки (для сравнения).
DEROT = float(os.environ.get("VIO_DEROT", "-1"))
FRAME_LOG = os.environ.get("VIO_FRAME_LOG", "")
RECENT_S = 1.0
UNDER_HOLD_S = 0.5          # после «под бортом объект» столько секунд кадры в EKF не отдаются
PX4_QOS = QoSProfile(reliability=ReliabilityPolicy.BEST_EFFORT, durability=DurabilityPolicy.TRANSIENT_LOCAL,
                     history=HistoryPolicy.KEEP_LAST, depth=1)
NAN = float("nan")


class Vio(Node):
    def __init__(self):
        super().__init__("vio_node")
        ns = f"/{common.PX4_NS}/fmu"
        self.pub_odo = self.create_publisher(VehicleOdometry, f"{ns}/in/vehicle_visual_odometry", PX4_QOS)
        self.pub_disp = self.create_publisher(PointStamped, "/swarm/local/vio_disp", 10)
        self.pub_vel = self.create_publisher(Vector3Stamped, "/swarm/local/vio_vel", 10)    # скорость принятого кадра (x = E, y = N)
        self.create_subscription(Image, "/sensors/cam_down/image_raw", self._on_image, qos_profile_sensor_data)
        self.create_subscription(CameraInfo, "/sensors/cam_down/camera_info", self._on_info, qos_profile_sensor_data)
        self.create_subscription(Range, "/sensors/lidar_down/range", self._on_range, qos_profile_sensor_data)
        self.create_subscription(VehicleLocalPosition, f"{ns}/out/vehicle_local_position", self._on_lpos, qos_profile_sensor_data)
        self.create_subscription(VehicleAttitude, f"{ns}/out/vehicle_attitude", self._on_att, qos_profile_sensor_data)
        self.create_subscription(SensorGps, f"{ns}/out/vehicle_gps_position", self._on_gps, qos_profile_sensor_data)
        self.create_subscription(String, "/swarm/local/nav_status", self._on_nav, 10)
        self.fx, self.h, self.hdg = None, None, None
        self.prev, self.prev_t, self.h_prev, self.vz = None, None, None, 0.0
        self.E = self.N = 0.0
        self.h_t, self.lp = 0.0, None
        self.rp, self.prev_rp = None, None             # (крен, тангаж) последнего сообщения и кадра-предшественника
        self.gps_fix, self.gps_t, self.suspect = 0, 0.0, False
        self.under_t = -1e9                            # когда дальномер последний раз видел объект под бортом
        self.recent = deque()                          # принятые (t, vn, ve) за последнюю секунду
        self.rejected, self.accepted, self._log_t, self.skipped = 0, 0, 0.0, {}
        self.log = open(FRAME_LOG, "a", buffering=1) if FRAME_LOG else None

    def _on_info(self, m):
        self.fx = m.k[0]

    def _on_att(self, m):
        w, x, y, z = (float(v) for v in m.q)
        self.rp = (math.atan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y)), math.asin(max(-1.0, min(1.0, 2.0 * (w * y - z * x)))))

    def _on_gps(self, m):
        self.gps_fix, self.gps_t = int(m.fix_type), time.monotonic()

    def _on_nav(self, m):
        try:
            self.suspect = bool(json.loads(m.data).get("gps_suspect"))
        except (ValueError, AttributeError):
            pass

    def _gps_ok(self):
        return self.gps_fix >= 3 and time.monotonic() - self.gps_t < 1.0 and not self.suspect

    def _on_range(self, m):
        if m.min_range <= m.range <= m.max_range:
            now = time.monotonic()
            if self.h is not None and now - self.h_t <= 1.0:
                vz_up = -float(self.lp.vz) if self.lp is not None and math.isfinite(self.lp.vz) else 0.0
                if object_below(m.range, self.h, vz_up, now - self.h_t):     # под бортом другой борт или край крыши: держим прогноз
                    self.h, self.h_t, self.under_t = self.h + vz_up * (now - self.h_t), now, now
                    return
            # сглаживание по времени (а не по числу сообщений): при другой частоте дальномера лаг масштаба не меняется
            self.h = m.range if self.h is None or now - self.h_t > 1.0 else self.h + smooth_alpha(now - self.h_t) * (m.range - self.h)
            self.h_t = now

    def _on_lpos(self, m):
        self.hdg = m.heading
        self.lp = m

    def _on_image(self, m):
        if m.encoding != "mono8" or m.width * m.height != len(m.data):
            return
        img = np.frombuffer(m.data, np.uint8).reshape(m.height, m.width).astype(np.float32) / 255.0
        t = m.header.stamp.sec + m.header.stamp.nanosec * 1e-9
        if t <= 0.0:
            t = time.monotonic()
        prev, prev_t, prev_rp = self.prev, self.prev_t, self.prev_rp
        self.prev, self.prev_t, self.prev_rp = img, t, self.rp
        if prev is None or not (self.fx and self.h and self.hdg is not None):
            return
        dt = t - prev_t
        if not 0.01 < dt < 0.5:
            return
        if self.h is None or time.monotonic() - self.h_t > 1.0:       # высота устарела: масштаб смещения неизвестен
            return
        if time.monotonic() - self.under_t < UNDER_HOLD_S:            # в кадре посторонний объект под бортом
            return
        # сдвиг по всему кадру, проверенный плитками: без текстуры (край пола, крыша) и с посторонним объектом в кадре кадр не годится
        dx, dy, resp, agree, why = validated_shift(prev, img)
        if why is None and resp < RESP_MIN:
            why = "низкий отклик"
        if why is not None:
            self.skipped[why] = self.skipped.get(why, 0) + 1
            if self.log:
                self.log.write(json.dumps({"t": round(t, 4), "skip": why}, ensure_ascii=False) + "\n")
            return
        s = self.h / self.fx                       # м на пиксель
        f, r = FLIP_Y * dy * s, -FLIP_X * dx * s   # смещение вперёд / вправо, м
        d_roll = d_pitch = 0.0
        if self.rp is not None and prev_rp is not None:
            d_roll, d_pitch = self.rp[0] - prev_rp[0], self.rp[1] - prev_rp[1]
        f, r = f + DEROT * self.h * d_pitch, r - DEROT * self.h * d_roll
        c, sn = math.cos(self.hdg), math.sin(self.hdg)
        dN, dE = f * c - r * sn, f * sn + r * c
        lp, now = self.lp, time.monotonic()
        while self.recent and now - self.recent[0][0] > RECENT_S:
            self.recent.popleft()
        gps_ok = self._gps_ok()
        if gps_ok:       # скорость EKF опирается на GPS — по ней и проверяем
            bad = vio_outlier(dN / dt, dE / dt, getattr(lp, "vx", None), getattr(lp, "vy", None), bool(lp and lp.v_xy_valid))
        else:            # без GPS скорость EKF сама из VIO: опора — недавние принятые измерения VIO
            bad = vio_outlier(dN / dt, dE / dt) or vio_jump(dN / dt, dE / dt, list(self.recent), now)
        if self.log:
            self.log.write(json.dumps({"t": round(t, 4), "dt": round(dt, 4), "h": round(self.h, 3), "resp": round(resp, 3), "agree": round(agree, 2), "dx": round(dx, 2),
                                       "dy": round(dy, 2), "hdg": round(self.hdg, 4), "droll": round(d_roll, 4), "dpitch": round(d_pitch, 4),
                                       "dN": round(dN, 4), "dE": round(dE, 4), "gps_ok": gps_ok, "bad": bad,
                                       "px4_vn": None if lp is None else round(float(lp.vx), 3),
                                       "px4_ve": None if lp is None else round(float(lp.vy), 3),
                                       "px4_n": None if lp is None else round(float(lp.x), 3),
                                       "px4_e": None if lp is None else round(float(lp.y), 3),
                                       "xy_valid": None if lp is None else bool(lp.xy_valid)}) + "\n")
        if bad:
            self.rejected += 1
            if now - self._log_t > 10.0:
                self._log_t = now
                self.get_logger().warning(f"VIO outlier rejected ({self.rejected} rejected / {self.accepted} accepted, gps_ok={gps_ok})")
            return
        self.recent.append((now, dN / dt, dE / dt))
        self.accepted += 1
        if self.h_prev is not None:
            self.vz = 0.5 * self.vz + 0.5 * (self.h - self.h_prev) / dt   # вверх
        self.h_prev = self.h
        self.E += dE
        self.N += dN
        now_us = int(self.get_clock().now().nanoseconds / 1000)            # U7: время ROS для PX4
        o = VehicleOdometry()
        o.timestamp = o.timestamp_sample = now_us
        o.pose_frame, o.velocity_frame = VehicleOdometry.POSE_FRAME_NED, VehicleOdometry.VELOCITY_FRAME_NED
        o.position, o.q, o.angular_velocity = [NAN] * 3, [NAN] * 4, [NAN] * 3
        # Вертикальную скорость из разности высот по дальномеру в EKF не отдаём: на скачках рельефа (края препятствий) и при редких отсчётах
        # она даёт ложные всплески, а без GPS это единственная вертикальная «помощь» EKF — борты раскачивались по высоте на 0-30 м
        # (репетиция демо 2026-10-26). z-скорость с большой дисперсией (EKF2_EV_NOISE_MD=1), высоту держит барометр.
        o.velocity = [dN / dt, dE / dt, 0.0]
        o.position_variance, o.orientation_variance = [NAN] * 3, [NAN] * 3
        o.velocity_variance = [0.0225, 0.0225, 25.0]
        o.reset_counter, o.quality = 0, int(min(100, resp * 100))
        self.pub_odo.publish(o)
        d = PointStamped()
        d.header.stamp = self.get_clock().now().to_msg()
        d.point.x, d.point.y, d.point.z = self.E, self.N, self.h
        self.pub_disp.publish(d)
        v = Vector3Stamped()
        v.header.stamp = d.header.stamp
        v.vector.x, v.vector.y = dE / dt, dN / dt
        self.pub_vel.publish(v)


def main():
    rclpy.init()
    node = Vio()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
