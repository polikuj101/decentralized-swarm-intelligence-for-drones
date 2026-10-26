#!/usr/bin/env python3
"""range-sim: дальномеры бортов (кольцевой и нисходящий) считаются аналитически по геометрии мира, а не рендером.

Почему (HANDOFF п. 2): на WSL2 рендер Mesa D3D12 тянет ~260 проходов в секунду на все сенсоры, а два gpu_lidar на борт требуют ~85 % из
~525 нужных (кольцо 360° — 4 грани куба + проход сведения, нисходящий — 1 + 1). Камеры получали 40-60 % заданной частоты, дальномеры
тоже. Мир состоит из пола, коробок-препятствий, дисков-целей и бортов — всё это известно точно (scene.json), поэтому пересечение лучей
считается на CPU без потери точности. Рендер остаётся только камерам.

Топики и формат те же, что у gpu_lidar (/swarm/N/lidar_ring/scan, /swarm/N/lidar_down/scan, gz.msgs.LaserScan, время симуляции),
мост агента не меняется. Позы бортов — из /world/<world>/dynamic_pose/info (SceneBroadcaster, время симуляции в заголовке).
Нет попадания в пределах дальности или ближе минимальной — +inf, как у gpu_lidar (на земле нисходящий дальномер тоже даёт inf)."""
import json
import logging
import os
import pathlib
import random
import threading

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s range-sim %(message)s")
log = logging.getLogger("range-sim")

WORLD = os.environ.get("GZ_WORLD", "swarm_world")
TOPIC_PREFIX = os.environ.get("RANGE_SIM_PREFIX", "/swarm")     # другой префикс — для сверки с gpu_lidar в одном мире
FORCE = os.environ.get("RANGE_SIM_FORCE") == "1"
SCENE = json.loads(pathlib.Path(os.environ.get("SCENE_FILE", "/opt/swarm/scene/scene.json")).read_text())
RING, DOWN = SCENE["sensors"]["lidar_ring"], SCENE["sensors"]["lidar_down"]
NOISE_M = 0.02                                   # как <noise> у gpu_lidar в gen_swarm.py
RING_OFFSET, DOWN_OFFSET = np.array([0.0, 0.0, 0.08]), np.array([0.0, 0.0, -0.06])   # позы сенсоров на base_link
DRONE_R, DRONE_DZ = 0.35, 0.2                    # соседний борт — сфера (рама x500 ~0,7 м по диагонали) над началом модели
DISC_TOP = 0.02
_g = SCENE.get("ground")                         # видимый пол: за его краем gpu_lidar ничего не видел (inf)
GROUND = None if _g is None else (_g["x_m"] - _g["size_m"][0] / 2, _g["x_m"] + _g["size_m"][0] / 2,
                                  _g["y_m"] - _g["size_m"][1] / 2, _g["y_m"] + _g["size_m"][1] / 2)
EPS = 2e-3                                       # допуск сравнения времени симуляции, с

BOX_MIN = np.array([[o["x_m"] - o["size_m"][0] / 2, o["y_m"] - o["size_m"][1] / 2, 0.0] for o in SCENE["obstacles"]]).reshape(-1, 3)
BOX_MAX = np.array([[o["x_m"] + o["size_m"][0] / 2, o["y_m"] + o["size_m"][1] / 2, o["size_m"][2]] for o in SCENE["obstacles"]]).reshape(-1, 3)
DISCS = np.array([[t["x_m"], t["y_m"], t["radius_m"]] for t in SCENE["targets"]]).reshape(-1, 3)
_step = 2.0 * np.pi / RING["samples"]
RING_ANGLES = -np.pi + _step * np.arange(RING["samples"])          # те же углы, что ring_xml в gen_swarm.py
RING_DIRS = np.stack([np.cos(RING_ANGLES), np.sin(RING_ANGLES), np.zeros_like(RING_ANGLES)], axis=1)


def rotate(q, v):
    """Поворот векторов v (N×3) кватернионом q = (w, x, y, z)."""
    w, u = q[0], np.asarray(q[1:])
    t = 2.0 * np.cross(u, v)
    return v + w * t + np.cross(u, t)


def ray_ranges(origin, dirs, others, rmin, rmax):
    """Расстояние до первого пересечения каждого луча (dirs — единичные, N×3) с полом, коробками, дисками и соседними бортами."""
    o = np.asarray(origin, float)
    best = np.full(len(dirs), np.inf)
    dz = dirs[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(dz < 0, -o[2] / dz, np.inf)                       # пол z = 0 в пределах видимого пола
        if GROUND is not None:
            gx, gy = o[0] + t * dirs[:, 0], o[1] + t * dirs[:, 1]
            t = np.where((gx >= GROUND[0]) & (gx <= GROUND[1]) & (gy >= GROUND[2]) & (gy <= GROUND[3]), t, np.inf)
        best = np.minimum(best, np.where(t > 0, t, np.inf))
        if len(DISCS):                                                 # верх дисков-целей
            t = np.where(dz < 0, (DISC_TOP - o[2]) / dz, np.inf)
            hit = o[None, :2] + t[:, None] * dirs[:, :2]
            inside = (np.hypot(hit[:, None, 0] - DISCS[None, :, 0], hit[:, None, 1] - DISCS[None, :, 1]) <= DISCS[None, :, 2]).any(axis=1)
            best = np.minimum(best, np.where((t > 0) & inside, t, np.inf))
        if len(BOX_MIN):                                               # коробки: метод плит
            inv = 1.0 / dirs
            t1 = (BOX_MIN[None, :, :] - o[None, None, :]) * inv[:, None, :]
            t2 = (BOX_MAX[None, :, :] - o[None, None, :]) * inv[:, None, :]
            tnear = np.nanmax(np.minimum(t1, t2), axis=2)
            tfar = np.nanmin(np.maximum(t1, t2), axis=2)
            t = np.where((tfar >= tnear) & (tfar > 0), np.where(tnear > 0, tnear, 0.0), np.inf).min(axis=1)
            best = np.minimum(best, t)
        for c in others:                                               # соседние борта: сферы
            oc = o - c
            b = dirs @ oc
            disc = b * b - (oc @ oc - DRONE_R ** 2)
            t = np.where(disc >= 0, -b - np.sqrt(np.maximum(disc, 0.0)), np.inf)
            best = np.minimum(best, np.where(t > 0, t, np.inf))
    best = np.where(np.isfinite(best), best + np.random.normal(0.0, NOISE_M, len(best)), best)
    return np.where((best >= rmin) & (best <= rmax), best, np.inf)


def due(d, attr, t, rate):
    """Пора ли публиковать скан: срок d.<attr> сдвигается на период; отстали больше чем на период или мир сброшен (время назад) —
    срок от текущего момента."""
    period, t_next = 1.0 / rate, getattr(d, attr)
    if t < t_next - 2.0 * period:            # время симуляции пошло назад: мир перезапущен
        t_next = t
    if t < t_next - EPS:
        return False
    t_next += period
    if t_next < t:
        t_next = t + period
    setattr(d, attr, t_next)
    return True


class Drone:
    def __init__(self, n, node):
        from gz.msgs10.laserscan_pb2 import LaserScan
        self.n = n
        self.pub_ring = node.advertise(f"{TOPIC_PREFIX}/{n}/lidar_ring/scan", LaserScan)
        self.pub_down = node.advertise(f"{TOPIC_PREFIX}/{n}/lidar_down/scan", LaserScan)
        self.t_ring = self.t_down = 0.0          # срок следующего скана (время симуляции)

    def scan(self, stamp, frame, ranges, amin, amax, step, rmin, rmax):
        from gz.msgs10.laserscan_pb2 import LaserScan
        m = LaserScan()
        m.header.stamp.sec, m.header.stamp.nsec = stamp.sec, stamp.nsec
        m.frame = frame
        m.angle_min, m.angle_max, m.angle_step = amin, amax, step
        m.range_min, m.range_max, m.count = rmin, rmax, len(ranges)
        m.vertical_count = 1
        m.ranges.extend(float(r) for r in ranges)
        return m


class RangeSim:
    def __init__(self):
        from gz.msgs10.pose_v_pb2 import Pose_V
        from gz.transport13 import Node
        self.node = Node()
        self.drones = {d["id"]: Drone(d["id"], self.node) for d in SCENE["drones"]}
        self.lock = threading.Lock()
        self.count = 0
        topic = f"/world/{WORLD}/dynamic_pose/info"
        if not self.node.subscribe(Pose_V, topic, self.on_poses):
            raise RuntimeError(f"subscribe failed: {topic}")
        log.info("ring %d rays %.1f-%.1f m @ %s Hz, down %.1f-%.1f m @ %s Hz, %d obstacles, %d targets, drones %s",
                 RING["samples"], RING["rmin"], RING["rmax"], RING["rate"], DOWN["rmin"], DOWN["rmax"], DOWN["rate"],
                 len(BOX_MIN), len(DISCS), sorted(self.drones))

    def on_poses(self, msg):
        poses = {}
        for p in msg.pose:
            if p.name.startswith("x500_swarm_"):
                try:
                    n = int(p.name.rsplit("_", 1)[1])
                except ValueError:
                    continue
                poses[n] = (np.array([p.position.x, p.position.y, p.position.z]),
                            (p.orientation.w, p.orientation.x, p.orientation.y, p.orientation.z))
        if not poses:
            return
        stamp = msg.header.stamp
        t = stamp.sec + stamp.nsec * 1e-9
        with self.lock:
            for n, (pos, q) in poses.items():
                d = self.drones.get(n)
                if d is None:
                    continue
                others = [p + np.array([0.0, 0.0, DRONE_DZ]) for k, (p, _) in poses.items() if k != n]
                # срок следующего скана, а не «прошло ли период с прошлого»: позы приходят тактами 1/60 с, и 0,04999 < 0,05 съедало такт
                # (20 Гц превращались в 15-17)
                if due(d, "t_down", t, DOWN["rate"]):
                    o = pos + rotate(q, DOWN_OFFSET[None, :])[0]
                    r = ray_ranges(o, rotate(q, np.array([[0.0, 0.0, -1.0]])), others, DOWN["rmin"], DOWN["rmax"])
                    d.pub_down.publish(d.scan(stamp, f"x500_swarm_{n}::base_link::lidar_down", r, 0.0, 0.0, 0.0, DOWN["rmin"], DOWN["rmax"]))
                if due(d, "t_ring", t, RING["rate"]):
                    o = pos + rotate(q, RING_OFFSET[None, :])[0]
                    r = ray_ranges(o, rotate(q, RING_DIRS), others, RING["rmin"], RING["rmax"])
                    d.pub_ring.publish(d.scan(stamp, f"x500_swarm_{n}::base_link::lidar_ring", r, float(RING_ANGLES[0]),
                                              float(RING_ANGLES[-1]), _step, RING["rmin"], RING["rmax"]))
            self.count += 1


if __name__ == "__main__":
    if SCENE.get("range_sensors", "gpu") != "analytic" and not FORCE:   # мир собран с gpu_lidar: второй издатель на тех же топиках не нужен
        log.info("range_sensors=%s in scene.json, idle", SCENE.get("range_sensors"))
    else:
        sim = RangeSim()
    threading.Event().wait()
