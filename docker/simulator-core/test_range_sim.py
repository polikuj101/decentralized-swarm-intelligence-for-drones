"""Геометрия range_sim.py без gz: python -m pytest docker/simulator-core/test_range_sim.py"""
import json
import math
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

SCENE = {"drones": [{"id": 1, "x_m": 0, "y_m": 0}],
         "obstacles": [{"name": "b", "x_m": 10.0, "y_m": 0.0, "size_m": [2.0, 4.0, 5.0]}],
         "targets": [{"name": "t", "x_m": 0.0, "y_m": 10.0, "radius_m": 1.5}],
         "sensors": {"lidar_ring": {"samples": 36, "rmin": 0.2, "rmax": 15.0, "rate": 10},
                     "lidar_down": {"rmin": 0.1, "rmax": 30.0, "rate": 20}},
         "range_sensors": "analytic"}
_f = Path(tempfile.mkdtemp()) / "scene.json"
_f.write_text(json.dumps(SCENE))
os.environ["SCENE_FILE"] = str(_f)
sys.path.insert(0, str(Path(__file__).parent))

import range_sim as rs  # noqa: E402

LEVEL = (1.0, 0.0, 0.0, 0.0)
np.random.seed(0)
TOL = 0.1            # шум σ = 0,02 м


def down(pos, q=LEVEL, others=()):
    return rs.ray_ranges(np.array(pos) + rs.rotate(q, rs.DOWN_OFFSET[None, :])[0], rs.rotate(q, np.array([[0.0, 0.0, -1.0]])),
                         list(others), 0.1, 30.0)[0]


def test_down_over_ground_box_and_disc():
    assert abs(down([0, 0, 5.06]) - 5.0) < TOL
    assert abs(down([10, 0, 8.06]) - 3.0) < TOL                    # крыша коробки на 5 м
    assert abs(down([0, 10, 5.06]) - (5.0 - rs.DISC_TOP)) < TOL     # диск-цель толщиной 0,02 м


def test_down_on_ground_and_out_of_range_is_inf():
    assert math.isinf(down([0, 0, 0.1]))                           # ближе минимальной 0,1 м — как у gpu_lidar на земле
    assert math.isinf(down([0, 0, 40.0]))


def test_down_tilted_reads_slant_range():
    a = math.radians(20)
    q = (math.cos(a / 2), math.sin(a / 2), 0.0, 0.0)               # крен 20°
    r = down([0, 0, 5.06], q)
    assert abs(r - 5.0 / math.cos(a)) < 0.15


def test_ring_sees_box_ahead_and_neighbour_drone():
    o = np.array([4.0, 0.0, 2.0])
    r = rs.ray_ranges(o, rs.RING_DIRS, [np.array([4.0, 3.0, 2.0])], 0.2, 15.0)
    east = int(np.argmin(np.abs(rs.RING_ANGLES - 0.0)))
    north = int(np.argmin(np.abs(rs.RING_ANGLES - math.pi / 2)))
    west = int(np.argmin(np.abs(np.abs(rs.RING_ANGLES) - math.pi)))
    assert abs(r[east] - 5.0) < TOL                                # грань коробки x = 9
    assert abs(r[north] - (3.0 - rs.DRONE_R)) < TOL                # соседний борт в 3 м к северу
    assert math.isinf(r[west])                                     # пусто в пределах 15 м


def test_ring_angles_match_gen_swarm():
    assert len(rs.RING_ANGLES) == 36 and abs(rs.RING_ANGLES[0] + math.pi) < 1e-9
    assert abs(rs.RING_ANGLES[1] - rs.RING_ANGLES[0] - 2 * math.pi / 36) < 1e-9


class _D:
    t_down = t_ring = 0.0


def test_scan_rate_is_exact_on_60hz_pose_ticks():
    d, n20, n10 = _D(), 0, 0
    for k in range(1, 601):                       # 10 с тактами 1/60
        t = k / 60.0
        n20 += rs.due(d, "t_down", t, 20)
        n10 += rs.due(d, "t_ring", t, 10)
    assert 199 <= n20 <= 201 and 99 <= n10 <= 101


def test_world_reset_restarts_schedule():
    d = _D()
    for k in range(1, 300):
        rs.due(d, "t_down", k / 60.0, 20)
    assert rs.due(d, "t_down", 0.02, 20)          # время симуляции с нуля — скан сразу


def test_beyond_the_visible_ground_is_inf(monkeypatch):
    monkeypatch.setattr(rs, "GROUND", (-25.0, 75.0, -50.0, 50.0))
    assert abs(down([0, 0, 5.06]) - 5.0) < TOL
    assert math.isinf(down([0, 60, 5.06]))                         # за краем пола (y > 50) — небо, как у gpu_lidar
