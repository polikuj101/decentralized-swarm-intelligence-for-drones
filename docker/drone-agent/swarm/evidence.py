#!/usr/bin/env python3
"""swarm/evidence.py — при входе в DWELL ищет мишень-диск на кадрах нижней камеры (swarm/target_detect.py), сжимает кадр
под байтовый бюджет и отправляет BDA-карточку на web-hud. Номер цели в карточке — только если обнаруженный диск лёг рядом
с известной целью; иначе карточка честно говорит «в кадре не обнаружено». Не меняет flight_ctrl/coordinator."""
import json
import math
import os
import threading
import time
import urllib.request

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CameraInfo, Image, Range
from std_msgs.msg import String

from swarm import common
from swarm.target_detect import detect_disc, pixel_to_world

HUD_URL = os.environ.get("HUD_URL", "http://172.33.0.5:8000/api/ingest").rsplit("/api/", 1)[0] + "/api/evidence"
EW_TOKEN = os.environ.get("EW_TOKEN", "")
JPEG_BUDGET_BYTES = int(os.environ.get("EVIDENCE_JPEG_BUDGET", "5120"))
THUMB_W, THUMB_H = 160, 120
DETECT_WINDOW_S = float(os.environ.get("EVIDENCE_DETECT_S", "4.0"))   # сколько секунд зависания искать диск на свежих кадрах
MATCH_RADIUS_M = float(os.environ.get("EVIDENCE_MATCH_M", "5.0"))     # обнаружение ближе этого к известной цели — это она
RANGE_MAX_AGE_S = 1.0
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
        self.frame, self.frame_seq, self.was_dwell = None, 0, False
        self.fx, self.range_m, self.range_t, self.nav = None, None, 0.0, {}
        self.create_subscription(Image, "/sensors/cam_down/image_raw", self._on_image, qos_profile_sensor_data)
        self.create_subscription(CameraInfo, "/sensors/cam_down/camera_info", self._on_info, qos_profile_sensor_data)
        self.create_subscription(Range, "/sensors/lidar_down/range", self._on_range, qos_profile_sensor_data)
        self.create_subscription(String, "/swarm/local/nav_status", self._on_nav, 10)

    def _on_info(self, m):
        self.fx = m.k[0] or None

    def _on_range(self, m):
        if m.min_range <= m.range <= m.max_range:
            self.range_m, self.range_t = m.range, time.monotonic()

    def _on_image(self, m):
        try:
            if m.encoding in ("mono8", "8UC1") and m.width * m.height == len(m.data):
                self.frame = np.frombuffer(m.data, np.uint8).reshape(m.height, m.width)
            elif m.encoding in ("bgr8", "rgb8") and m.width * m.height * 3 == len(m.data):
                raw = np.frombuffer(m.data, np.uint8).reshape(m.height, m.width, 3)
                self.frame = cv2.cvtColor(raw, cv2.COLOR_RGB2GRAY if m.encoding == "rgb8" else cv2.COLOR_BGR2GRAY)
            else:
                return
            self.frame_seq += 1
        except Exception:
            pass

    def _on_nav(self, m):
        try:
            nav = json.loads(m.data)
        except json.JSONDecodeError:
            return
        self.nav = nav
        is_dwell = nav.get("state") == "DWELL"
        if is_dwell and not self.was_dwell and self.frame is not None and nav.get("capture", True):    # DWELL_ONLY: без снимка
            threading.Thread(target=self._capture, args=(dict(nav),), daemon=True).start()
        self.was_dwell = is_dwell

    def _height(self):
        return self.range_m if self.range_m is not None and time.monotonic() - self.range_t <= RANGE_MAX_AGE_S else None

    def _search(self):
        """Ищет диск на свежих кадрах, пока идёт зависание (не дольше DETECT_WINDOW_S) -> (кадр, обнаружение|None, высота)."""
        t_end, seen, frame, h = time.monotonic() + DETECT_WINDOW_S, -1, self.frame, self._height()
        while True:
            if self.frame_seq != seen:
                seen, frame, h = self.frame_seq, self.frame, self._height()
                det = detect_disc(frame, h, self.fx)
                if det is not None:
                    return frame, det, h
            if time.monotonic() >= t_end or self.nav.get("state") != "DWELL":
                return frame, None, h
            time.sleep(0.1)

    def _capture(self, nav):
        try:
            frame, det, h = self._search()
            x, y = nav.get("x", 0.0), nav.get("y", 0.0)
            img = cv2.resize(frame, (THUMB_W, THUMB_H), interpolation=cv2.INTER_AREA)
            tid, dist, det_enu = None, None, None
            if det is not None:
                k = THUMB_W / frame.shape[1]
                cv2.circle(img, (int(det["cx"] * k), int(det["cy"] * k)), int(det["r_px"] * k) + 2, 255 if det["contrast"] < 0 else 0, 1)
                if h and self.fx and nav.get("hdg") is not None:
                    ex, ey = pixel_to_world(det["cx"], det["cy"], frame.shape[1], frame.shape[0], h, self.fx, math.radians(nav["hdg"]), x, y)
                    det_enu = {"x": round(ex, 2), "y": round(ey, 2)}
                    tid, dist = nearest_target(ex, ey)
                    if dist is None or dist > MATCH_RADIUS_M:
                        tid = None
            jpg = encode_jpeg_budget(img, JPEG_BUDGET_BYTES)
            lat, lon = enu_to_approx_latlon(x, y)
            near_id, near_dist = nearest_target(x, y)
            card = {
                "drone": common.DRONE_ID, "t_mono": time.monotonic(), "wall_ts": time.time(),
                "local_enu_m": {"x": round(x, 2), "y": round(y, 2), "z": round(nav.get("z", 0.0), 2)},
                "approx_latlon": {"lat": lat, "lon": lon,
                                  "note": "вычислено из локальной VIO/лидар-позиции относительно known world origin; НЕ спутниковый фикс, GPS мог быть denied"},
                "nav_source": nav.get("src"), "nav_sigma_m": nav.get("sigma"), "gps_fix": nav.get("gps_fix"),
                "detection": det, "detected_enu_m": det_enu, "height_m": None if h is None else round(h, 2),
                "target_id": tid, "dist_to_target_m": dist,
                "nearest_known_target": {"id": near_id, "dist_m": near_dist},
                "image_bytes": len(jpg), "image_jpeg_b64": jpg.hex(),
            }
            req = urllib.request.Request(f"{HUD_URL}/{common.DRONE_ID}", json.dumps(card).encode(),
                                         {"Content-Type": "application/json", "Authorization": f"Bearer {EW_TOKEN}"})
            urllib.request.urlopen(req, timeout=4).read()
            what = f"disc {det['tone']} r={det['r_px']}px at {det_enu} target={tid} dist={dist}m" if det else "no disc in frame"
            self.get_logger().info(f"evidence captured: {len(jpg)}B {what}")
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
