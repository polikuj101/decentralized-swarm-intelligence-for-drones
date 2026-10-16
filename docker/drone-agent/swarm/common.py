"""swarm/common.py — конфигурация, геометрия, детерминированная таблица задач (одинакова на всех бортах)."""
import json
import math
import os
import time
from dataclasses import dataclass

DRONE_ID = int(os.environ.get("DRONE_ID", "1"))
SWARM_SIZE = int(os.environ.get("SWARM_SIZE", "5"))
PX4_NS = os.environ.get("PX4_NS", f"px4_{DRONE_ID}")
MISSION_FILE = os.environ.get("MISSION_FILE", "/opt/swarm/config/mission.json")
SCENE_FILE = os.environ.get("SCENE_FILE", "/opt/swarm/scene/scene.json")
TASK_COUNT = 22          # 0..5 секторы, 6..9 цели, 10..21 дочерние сектора (10 + 2p + (q-1))
NONE = 255


def load_json(path, wait_s=90.0):
    t0 = time.monotonic()
    while True:
        try:
            with open(path) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            if time.monotonic() - t0 > wait_s:
                raise
            time.sleep(1.0)


MISSION = load_json(MISSION_FILE, 5)
SCENE = load_json(SCENE_FILE)
AREA = MISSION["search_area"]
COLS, ROWS = MISSION["grid"]["cols"], MISSION["grid"]["rows"]
assert COLS * ROWS <= 6, "поддерживается не более 6 базовых секторов"
SPAWN = next((d["x_m"], d["y_m"]) for d in SCENE["drones"] if d["id"] == DRONE_ID)
HOME = SPAWN
ALT = MISSION["base_alt_m"] + MISSION["alt_step_m"] * (DRONE_ID - 1)
OBSTACLES = SCENE.get("obstacles", [])


def world_to_ned(x, y):
    """мир ENU (x восток, y север) -> локальная NED PX4 (north, east); начало в точке старта борта"""
    return y - SPAWN[1], x - SPAWN[0]


def ned_to_world(n, e):
    return SPAWN[0] + e, SPAWN[1] + n


@dataclass(frozen=True)
class Task:
    id: int
    kind: str          # SECTOR | TARGET
    name: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def cx(self):
        return (self.x0 + self.x1) / 2.0

    @property
    def cy(self):
        return (self.y0 + self.y1) / 2.0

    @property
    def area(self):
        return abs((self.x1 - self.x0) * (self.y1 - self.y0))


def build_tasks(split2=0, split3=0):
    """split2 — битовая маска разделённых базовых секторов; split3 — из них разделённых на 3 части (иначе на 2)."""
    tasks = {}
    cw = (AREA["x_max"] - AREA["x_min"]) / COLS
    rh = (AREA["y_max"] - AREA["y_min"]) / ROWS
    for r in range(ROWS):
        for c in range(COLS):
            p = r * COLS + c
            x0, y0 = AREA["x_min"] + c * cw, AREA["y_min"] + r * rh
            x1, y1 = x0 + cw, y0 + rh
            parts = (3 if (split3 >> p) & 1 else 2) if (split2 >> p) & 1 else 1
            along_x = (x1 - x0) >= (y1 - y0)
            for q in range(parts):
                tid = p if q == 0 else 10 + 2 * p + (q - 1)
                if along_x:
                    box = (x0 + q * (x1 - x0) / parts, y0, x0 + (q + 1) * (x1 - x0) / parts, y1)
                else:
                    box = (x0, y0 + q * (y1 - y0) / parts, x1, y0 + (q + 1) * (y1 - y0) / parts)
                tasks[tid] = Task(tid, "SECTOR", f"S{p}" if parts == 1 else f"S{p}.{q}", *box)
    for i, t in enumerate(SCENE.get("targets", [])[:4]):
        tasks[6 + i] = Task(6 + i, "TARGET", f"T{i}", t["x_m"], t["y_m"], t["x_m"], t["y_m"])
    return tasks


def task_waypoints(t):
    """цель — одна точка; сектор — «газонокосилка» с шагом lane_spacing_m вдоль длинной стороны"""
    if t.kind == "TARGET":
        return [(t.x0, t.y0)]
    lane, m = MISSION["lane_spacing_m"], 1.0
    w, h = t.x1 - t.x0, t.y1 - t.y0
    pts = []
    if h >= w:
        n = max(1, math.ceil(w / lane))
        ya, yb = t.y0 + m, t.y1 - m
        for i in range(n):
            x = t.x0 + (i + 0.5) * w / n
            pts += [(x, ya), (x, yb)] if i % 2 == 0 else [(x, yb), (x, ya)]
    else:
        n = max(1, math.ceil(h / lane))
        xa, xb = t.x0 + m, t.x1 - m
        for i in range(n):
            y = t.y0 + (i + 0.5) * h / n
            pts += [(xa, y), (xb, y)] if i % 2 == 0 else [(xb, y), (xa, y)]
    return [(round(px, 2), round(py, 2)) for px, py in pts]
