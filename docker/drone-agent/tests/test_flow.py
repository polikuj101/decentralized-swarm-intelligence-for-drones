"""tests/test_flow.py — сдвиг кадра по плиткам устойчив к постороннему движущемуся объекту (swarm/flow.py)."""
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from swarm.flow import robust_shift  # noqa: E402

W, H = 320, 240


def ground(seed=3, size=600):
    """Текстура как у пола мира: сумма сглаженных шумов разного масштаба."""
    rng = np.random.default_rng(seed)
    acc = np.zeros((size, size), np.float32)
    for k, wgt in ((8, 1.0), (32, 0.6), (128, 0.4), (300, 0.2)):
        acc += wgt * cv2.resize(rng.random((k, k)).astype(np.float32), (size, size), interpolation=cv2.INTER_CUBIC)
    return (acc - acc.min()) / (acc.max() - acc.min())


def view(tex, x, y):
    m = np.float32([[1, 0, -x], [0, 1, -y]])
    return cv2.warpAffine(tex, m, (W, H), flags=cv2.INTER_LINEAR)


def test_plain_shift_is_measured():
    tex = ground()
    a, b = view(tex, 100.0, 100.0), view(tex, 103.0, 98.0)
    dx, dy, resp, frac = robust_shift(a, b)
    assert abs(dx - (-3.0)) < 0.3 and abs(dy - 2.0) < 0.3 and frac > 0.8


def test_moving_drone_in_view_does_not_drag_the_estimate():
    """Фон неподвижен (борт висит), через кадр проходит тёмный «борт» 80×60 пикселей со сдвигом 12 пикселей за кадр."""
    tex = ground()
    a, b = view(tex, 100.0, 100.0), view(tex, 100.0, 100.0)
    a[90:150, 100:180] = 0.05
    b[90:150, 112:192] = 0.05
    whole = cv2.phaseCorrelate(a, b, cv2.createHanningWindow((W, H), cv2.CV_32F))[0]
    dx, dy, resp, frac = robust_shift(a, b)
    assert abs(dx) < 0.5 and abs(dy) < 0.5
    assert abs(whole[0]) > 2.0 or frac < 1.0          # по всему кадру объект заметно сбивает сдвиг или часть плиток не согласна


def test_no_common_motion_is_flagged():
    rng = np.random.default_rng(0)
    a = rng.random((H, W)).astype(np.float32)
    b = rng.random((H, W)).astype(np.float32)
    r = robust_shift(a, b)
    assert r is None or r[3] < 0.6


def test_uniform_view_gives_no_motion_instead_of_a_confident_zero():
    """За краем пола — однородное небо: прежде это давало «стою» с откликом 0,93 и улёт борта."""
    from swarm.flow import validated_shift
    a = np.full((H, W), 0.8, np.float32)
    r = validated_shift(a, a.copy())
    assert r[4] == "нет текстуры"


def test_validated_shift_measures_whole_frame_and_rejects_dragged_frames():
    from swarm.flow import validated_shift
    tex = ground()
    a, b = view(tex, 100.0, 100.0), view(tex, 104.0, 97.0)
    dx, dy, resp, agree, why = validated_shift(a, b)
    assert why is None and abs(dx + 4.0) < 0.2 and abs(dy - 3.0) < 0.2
    a2, b2 = view(tex, 100.0, 100.0), view(tex, 100.0, 100.0)
    a2[40:200, 60:260] = 0.05                                   # крупный борт прямо под камерой, сдвигается на 15 пикселей
    b2[40:200, 75:275] = 0.05
    assert validated_shift(a2, b2)[4] is not None
