#!/usr/bin/env python3
"""swarm/evidence.py — захват keyframe при входе в DWELL, сжатие под байтовый бюджет, отправка BDA-карточки
на web-hud. Не меняет flight_ctrl/coordinator — независимый подписчик существующих топиков."""
import json
import os
import threading
import time
import urllib.request

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image
from std_msgs.msg import String

from swarm import common

HUD_URL = os.environ.get("HUD_URL", "http://172.33.0.5:8000/api/ingest").rsplit("/api/", 1)[0] + "/api/evidence"
EW_TOKEN = os.environ.get("EW_TOKEN", "")
JPEG_BUDGET_BYTES = int(os.environ.get("EVIDENCE_JPEG_BUDGET", "5120"))
THUMB_W, THUMB_H = 160, 120
WORLD_LAT0, WORLD_LON0 = 47.397971057728974, 8.546163739800146   # тот же origin, что в п. 3.5.2
M_PER_DEG_LAT = 111_320.0


def enu_to_approx_latlon(x, y):
    """ТОЛЬКО для читаемости карточки: перевод локальных ENU-метров (VIO/dead-reckoning) в приближённые
    широту/долготу через известный origin симулируемого мира. Это НЕ GPS-фикс — GPS в момент съёмки может
    быть denied, координата получена без спутниковой навигации."""
    lat = WORLD_LAT0 + y / M_PER_DEG_LAT
    lon = WORLD_LON0 + x / (M_PER_DEG_LAT * max(0.01, np.cos(np.radians(WORLD_LAT0))))
    return round(lat, 6), round(lon, 6)


def encode_jpeg_budget(img, budget: int) -> bytes:
    """бинарный поиск по качеству JPEG под байтовый бюджет; без жёсткой гарантии на очень сложных кадрах —
    тогда возвращается минимально достижимое качество (q=5)."""
    lo, hi, best = 5, 95, None
    while lo <= hi:
        q = (lo + hi) // 2
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
        if ok and len(buf) <= budget:
            best, lo = buf.tobytes(), q + 1
        else:
            hi = q - 1
    if best is None:
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 5])
        best = buf.tobytes()
    return best


def nearest_target(x, y):
    best, bd = None, 1e9
    for i, t in enumerate(common.SCENE.get("targets", [])[:4]):
        d = ((t["x_m"] - x) ** 2 + (t["y_m"] - y) ** 2) ** 0.5
        if d < bd:
            best, bd = i, d
    return best, round(bd, 2)


class Evidence(Node):
    def __init__(self):
        super().__init__("evidence")
        self.frame, self.was_dwell = None, False
        self.create_subscription(Image, "/sensors/cam_down/image_raw", self._on_image, qos_profile_sensor_data)
        self.create_subscription(String, "/swarm/local/nav_status", self._on_nav, 10)

    def _on_image(self, m):
        try:
            if m.encoding in ("mono8", "8UC1") and m.width * m.height == len(m.data):
                self.frame = np.frombuffer(m.data, np.uint8).reshape(m.height, m.width)
            elif m.encoding in ("bgr8", "rgb8") and m.width * m.height * 3 == len(m.data):
                raw = np.frombuffer(m.data, np.uint8).reshape(m.height, m.width, 3)
                self.frame = cv2.cvtColor(raw, cv2.COLOR_RGB2GRAY if m.encoding == "rgb8" else cv2.COLOR_BGR2GRAY)
        except Exception:
            pass

    def _on_nav(self, m):
        try:
            nav = json.loads(m.data)
        except json.JSONDecodeError:
            return
        is_dwell = nav.get("state") == "DWELL"
        if is_dwell and not self.was_dwell and self.frame is not None and nav.get("capture", True):    # DWELL_ONLY: без снимка
            threading.Thread(target=self._capture, args=(dict(nav),), daemon=True).start()
        self.was_dwell = is_dwell

    def _capture(self, nav):
        try:
            img = cv2.resize(self.frame, (THUMB_W, THUMB_H), interpolation=cv2.INTER_AREA)
            jpg = encode_jpeg_budget(img, JPEG_BUDGET_BYTES)
            lat, lon = enu_to_approx_latlon(nav.get("x", 0.0), nav.get("y", 0.0))
            tid, dist = nearest_target(nav.get("x", 0.0), nav.get("y", 0.0))
            card = {
                "drone": common.DRONE_ID, "t_mono": time.monotonic(), "wall_ts": time.time(),
                "local_enu_m": {"x": round(nav.get("x", 0.0), 2), "y": round(nav.get("y", 0.0), 2), "z": round(nav.get("z", 0.0), 2)},
                "approx_latlon": {"lat": lat, "lon": lon,
                                  "note": "вычислено из локальной VIO/лидар-позиции относительно known world origin; НЕ спутниковый фикс, GPS мог быть denied"},
                "nav_source": nav.get("src"), "nav_sigma_m": nav.get("sigma"), "gps_fix": nav.get("gps_fix"),
                "target_id": tid, "dist_to_target_m": dist,
                "image_bytes": len(jpg), "image_jpeg_b64": jpg.hex(),
            }
            req = urllib.request.Request(f"{HUD_URL}/{common.DRONE_ID}", json.dumps(card).encode(),
                                         {"Content-Type": "application/json", "Authorization": f"Bearer {EW_TOKEN}"})
            urllib.request.urlopen(req, timeout=4).read()
            self.get_logger().info(f"evidence captured: {len(jpg)}B target={tid} dist={dist}m")
        except Exception as e:  # noqa: BLE001
            self.get_logger().warning(f"evidence upload failed: {e!r}")


def main():
    rclpy.init()
    node = Evidence()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
