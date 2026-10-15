#!/usr/bin/env python3
"""gz-sensor-bridge: сенсоры борта N (Gazebo gz-transport) -> ROS 2.
Подписывается только на /swarm/<DRONE_ID>/...; штампы сохраняются как есть (время симуляции = время PX4)."""
import math
import os

import rclpy
from gz.msgs10.image_pb2 import Image as GzImage
from gz.msgs10.laserscan_pb2 import LaserScan as GzScan
from gz.transport13 import Node as GzNode
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CameraInfo, Image, LaserScan, Range

N = int(os.environ["DRONE_ID"])
HFOV = float(os.environ.get("CAM_HFOV_RAD", "1.2"))   # = CAM.hfov в gen_swarm.py


class Bridge(Node):
    def __init__(self):
        super().__init__("gz_sensor_bridge")
        self.p_img = self.create_publisher(Image, "/sensors/cam_down/image_raw", qos_profile_sensor_data)
        self.p_info = self.create_publisher(CameraInfo, "/sensors/cam_down/camera_info", qos_profile_sensor_data)
        self.p_ring = self.create_publisher(LaserScan, "/sensors/lidar_ring/scan", qos_profile_sensor_data)
        self.p_down = self.create_publisher(Range, "/sensors/lidar_down/range", qos_profile_sensor_data)
        self.gz = GzNode()
        for topic, typ, cb in ((f"/swarm/{N}/cam_down/image", GzImage, self.on_image),
                               (f"/swarm/{N}/lidar_ring/scan", GzScan, self.on_ring),
                               (f"/swarm/{N}/lidar_down/scan", GzScan, self.on_down)):
            if not self.gz.subscribe(typ, topic, cb):
                raise RuntimeError(f"gz subscribe failed: {topic}")
        self.get_logger().info(f"bridging /swarm/{N}/* -> /sensors/*")

    @staticmethod
    def stamp(msg, header):
        header.stamp.sec = int(msg.header.stamp.sec)
        header.stamp.nanosec = int(msg.header.stamp.nsec)

    def on_image(self, g: GzImage):
        w, h, n = int(g.width), int(g.height), len(g.data)
        out = Image()
        self.stamp(g, out.header)
        out.header.frame_id = "cam_down_link"
        out.width, out.height = w, h
        if n == w * h:
            out.encoding, out.step = "mono8", w
        elif n == 3 * w * h:
            out.encoding, out.step = "rgb8", 3 * w
        else:
            return
        out.data = bytes(g.data)
        self.p_img.publish(out)
        info = CameraInfo()
        info.header = out.header
        info.width, info.height = w, h
        fx = w / (2.0 * math.tan(HFOV / 2.0))
        info.k = [fx, 0.0, w / 2.0, 0.0, fx, h / 2.0, 0.0, 0.0, 1.0]
        info.p = [fx, 0.0, w / 2.0, 0.0, 0.0, fx, h / 2.0, 0.0, 0.0, 0.0, 1.0, 0.0]
        info.r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        info.distortion_model = "plumb_bob"
        info.d = [0.0, 0.0, 0.0, 0.0, 0.0]
        self.p_info.publish(info)

    def on_ring(self, g: GzScan):
        out = LaserScan()
        self.stamp(g, out.header)
        out.header.frame_id = "lidar_ring_link"
        out.angle_min, out.angle_max = float(g.angle_min), float(g.angle_max)
        out.angle_increment = float(g.angle_step)
        out.range_min, out.range_max = float(g.range_min), float(g.range_max)
        out.scan_time = 0.1
        out.ranges = [float(r) for r in g.ranges]
        self.p_ring.publish(out)

    def on_down(self, g: GzScan):
        if not g.ranges:
            return
        out = Range()
        self.stamp(g, out.header)
        out.header.frame_id = "lidar_down_link"
        out.radiation_type = Range.INFRARED
        out.field_of_view = 0.03
        out.min_range, out.max_range = float(g.range_min), float(g.range_max)
        out.range = float(g.ranges[0])
        self.p_down.publish(out)


def main():
    rclpy.init()
    node = Bridge()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
