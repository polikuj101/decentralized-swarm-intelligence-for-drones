"""tests/test_target_detect.py — детектор диска на реальных кадрах нижней камеры (HANDOFF п. 4).

Кадры сняты 2026-10-26 в отдельном контейнере simulator-core: мир без гравитации, борт поставлен set_pose над целью,
над крышей препятствия или над пустым полом (x, y, z — ENU-позиция борта, yaw — курс модели в Gazebo, 0 = нос на восток)."""
import math
import sys
from pathlib import Path

import cv2
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from swarm.target_detect import detect_disc, pixel_to_world  # noqa: E402

FRAMES = Path(__file__).parent / "data" / "frames"
FX = 160 / math.tan(0.6)          # 320 px, hfov 1,2 рад (gen_swarm.py CAM)
CAM_BELOW_BODY_M = 0.07           # камера на 0,05 м ниже центра борта, диск толщиной 0,02 м
TARGETS = {"A": (40, 15), "B": (45, -12), "C": (20, 30)}
# имя: (x, y, z, yaw_deg)
DISCS = {"A_c_h8": (40, 15, 8, 0), "A_edge_h5": (40, 17.8, 5, 0), "A_off2_h5": (42, 16, 5, 0), "A_off3_h8": (37, 13, 8, 90),
         "B_c_h5": (45, -12, 5, 0), "B_off_h6": (46.5, -11, 6, 30), "C_c_h8": (20, 30, 8, 0), "C_off_h6": (18.5, 31, 6, -45)}
EMPTY = {"obst1_h9": 9, "obst2_h8": 8, "obst3_h7": 7, "ground_2": 8, "ground_5": 4, "ground_6": 12}


def load(name):
    img = cv2.imread(str(FRAMES / f"{name}.png"), cv2.IMREAD_GRAYSCALE)
    assert img is not None, name
    return img


@pytest.mark.parametrize("name", sorted(DISCS))
def test_disc_found_and_placed_within_half_a_metre(name):
    x, y, z, yaw = DISCS[name]
    img = load(name)
    det = detect_disc(img, z - CAM_BELOW_BODY_M, FX)
    assert det is not None
    assert det["tone"] == ("светлый" if name.startswith("A") else "тёмный")   # L8 = красный канал: красная цель белая, зелёная и синяя чёрные
    heading = math.radians(90 - yaw)                                         # курс PX4 (NED) из курса модели Gazebo (ENU)
    ex, ey = pixel_to_world(det["cx"], det["cy"], img.shape[1], img.shape[0], z - CAM_BELOW_BODY_M, FX, heading, x, y)
    tx, ty = TARGETS[name[0]]
    assert math.hypot(ex - tx, ey - ty) < 0.5


@pytest.mark.parametrize("name", sorted(DISCS))
def test_disc_found_without_height(name):
    assert detect_disc(load(name)) is not None


@pytest.mark.parametrize("name", sorted(EMPTY))
def test_no_disc_on_obstacle_roofs_or_bare_ground(name):
    img = load(name)
    assert detect_disc(img, EMPTY[name] - CAM_BELOW_BODY_M, FX) is None
    assert detect_disc(img) is None


def test_wrong_height_rejects_the_disc():
    """Радиус диска не согласуется с высотой (дальномер врёт вдвое) — не обнаружение."""
    assert detect_disc(load("A_c_h8"), 16.0, FX) is None
