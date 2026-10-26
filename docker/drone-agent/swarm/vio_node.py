#!/usr/bin/env python3
"""vio_node: оптический поток нижней камеры + высота -> VehicleOdometry (скорость, NED) для EKF2."""
import math
import os
import time

import cv2
import numpy as np
import rclpy
from geometry_msgs.msg import PointStamped
from px4_msgs.msg import VehicleLocalPosition, VehicleOdometry
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from sensor_msgs.msg import CameraInfo, Image, Range

from swarm import common
from swarm.altitude import smooth_alpha, vio_outlier

RESP_MIN = float(os.environ.get("VIO_RESP_MIN", "0.08"))
FLIP_X = float(os.environ.get("VIO_FLIP_X", "1"))
FLIP_Y = float(os.environ.get("VIO_FLIP_Y", "1"))
PX4_QOS = QoSProfile(reliability=ReliabilityPolicy.BEST_EFFORT, durability=DurabilityPolicy.TRANSIENT_LOCAL,
                     history=HistoryPolicy.KEEP_LAST, depth=1)
NAN = float("nan")


class Vio(Node):
    def __init__(self):
        super().__init__("vio_node")
        ns = f"/{common.PX4_NS}/fmu"
        self.pub_odo = self.create_publisher(VehicleOdometry, f"{ns}/in/vehicle_visual_odometry", PX4_QOS)
        self.pub_disp = self.create_publisher(PointStamped, "/swarm/local/vio_disp", 10)
        self.create_subscription(Image, "/sensors/cam_down/image_raw", self._on_image, qos_profile_sensor_data)
        self.create_subscription(CameraInfo, "/sensors/cam_down/camera_info", self._on_info, qos_profile_sensor_data)
        self.create_subscription(Range, "/sensors/lidar_down/range", self._on_range, qos_profile_sensor_data)
        self.create_subscription(VehicleLocalPosition, f"{ns}/out/vehicle_local_position", self._on_lpos, qos_profile_sensor_data)
        self.fx, self.h, self.hdg, self.win = None, None, None, None
        self.prev, self.prev_t, self.h_prev, self.vz = None, None, None, 0.0
        self.E = self.N = 0.0
        self.h_t, self.lp = 0.0, None
        self.rejected, self.accepted, self._log_t = 0, 0, 0.0

    def _on_info(self, m):
        self.fx = m.k[0]

    def _on_range(self, m):
        if m.min_range <= m.range <= m.max_range:
            now = time.monotonic()
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
        if self.win is None:
            self.win = cv2.createHanningWindow((m.width, m.height), cv2.CV_32F)
        prev, prev_t = self.prev, self.prev_t
        self.prev, self.prev_t = img, t
        if prev is None or not (self.fx and self.h and self.hdg is not None):
            return
        dt = t - prev_t
        if not 0.01 < dt < 0.5:
            return
        if self.h is None or time.monotonic() - self.h_t > 1.0:       # высота устарела: масштаб смещения неизвестен
            return
        (dx, dy), resp = cv2.phaseCorrelate(prev, img, self.win)
        if resp < RESP_MIN:
            return
        s = self.h / self.fx                       # м на пиксель
        f, r = FLIP_Y * dy * s, -FLIP_X * dx * s   # смещение вперёд / вправо, м
        c, sn = math.cos(self.hdg), math.sin(self.hdg)
        dN, dE = f * c - r * sn, f * sn + r * c
        lp = self.lp
        if vio_outlier(dN / dt, dE / dt, getattr(lp, "vx", None), getattr(lp, "vy", None), bool(lp and lp.v_xy_valid)):
            self.rejected += 1
            if time.monotonic() - self._log_t > 10.0:
                self._log_t = time.monotonic()
                self.get_logger().warning(f"VIO outlier rejected ({self.rejected} rejected / {self.accepted} accepted)")
            return
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
        o.velocity = [dN / dt, dE / dt, -self.vz]
        o.position_variance, o.orientation_variance = [NAN] * 3, [NAN] * 3
        o.velocity_variance = [0.0225] * 3
        o.reset_counter, o.quality = 0, int(min(100, resp * 100))
        self.pub_odo.publish(o)
        d = PointStamped()
        d.header.stamp = self.get_clock().now().to_msg()
        d.point.x, d.point.y, d.point.z = self.E, self.N, self.h
        self.pub_disp.publish(d)


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
