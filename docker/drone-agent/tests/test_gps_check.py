"""tests/test_gps_check.py — детектор подмены GPS по скоростям (HANDOFF п. 3) на синтетических трассах."""
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from swarm.gps_check import SpoofDetector, latlon_to_enu  # noqa: E402

DT = 0.2


def fly(duration, drift=None, drift_from=1e9, vio_scale=1.12, vio_angle_deg=5.0, gps_noise=0.3, speed=3.0, seed=1, vio_drop=None):
    """Борт летает «змейкой» со скоростью speed; VIO 15 Гц с ошибкой масштаба и угла (хуже, чем измерено на стенде), vio_drop(t) — кадр
    отброшен; GPS 5 Гц с шумом и, начиная с drift_from, с подмешанным дрейфом drift (м/с, вектор).
    -> (время первой тревоги, привязка, истинная позиция в момент начала дрейфа)."""
    rnd = random.Random(seed)
    det = SpoofDetector()
    x = y = vx_i = vy_i = 0.0
    a = math.radians(vio_angle_deg)
    off_e = off_n = 0.0
    truth_at_drift = None
    t = 0.0
    sub = DT / 3.0
    while t < duration:
        for k in range(3):                                   # VIO 15 Гц
            ts = t + k * sub
            hdg = 0.5 * math.sin(ts / 15.0) + (math.pi / 2 if int(ts / 40) % 2 else 0.0)
            ve, vn = speed * math.cos(hdg), speed * math.sin(hdg)
            x, y = x + ve * sub, y + vn * sub
            vve = vio_scale * (ve * math.cos(a) - vn * math.sin(a)) + rnd.gauss(0, 0.1)
            vvn = vio_scale * (ve * math.sin(a) + vn * math.cos(a)) + rnd.gauss(0, 0.1)
            if vio_drop is None or not vio_drop(ts):
                vx_i, vy_i = vx_i + vve * sub, vy_i + vvn * sub
                det.add_vio(ts, vve, vvn)
        if drift and t >= drift_from:
            if truth_at_drift is None:
                truth_at_drift = (x, y)
            off_e, off_n = off_e + drift[0] * DT, off_n + drift[1] * DT
        ge, gn = x + off_e + rnd.gauss(0, gps_noise), y + off_n + rnd.gauss(0, gps_noise)
        anchor = det.update(t, ge, gn, vx_i, vy_i, ge, gn)     # EKF ≈ GPS (худший случай: EKF сразу следует за подменой)
        if anchor:
            return t, anchor, truth_at_drift
        t += DT
    return None, None, truth_at_drift


def test_no_false_alarm_over_ten_minutes_of_honest_gps():
    for seed in range(5):
        assert fly(600.0, seed=seed)[0] is None


def test_drift_2mps_detected_within_5_s():
    t, anchor, truth = fly(200.0, drift=(1.4, 1.4), drift_from=60.0)
    assert t is not None and t - 60.0 < 5.0


def test_anchor_is_taken_before_the_drift_started():
    t, anchor, truth = fly(200.0, drift=(2.0, 0.0), drift_from=60.0)
    assert math.hypot(anchor[0] - truth[0], anchor[1] - truth[1]) < 3.0 + 3.0 * 2.0   # не дальше окна до начала дрейфа (скорость 3 м/с)


def test_slow_drift_is_accumulated():
    """0,8 м/с на скорости 1 м/с — около допуска одного окна, но копится (прежний монитор такой дрейф не видел совсем)."""
    t, _, _ = fly(300.0, drift=(0.0, 0.8), drift_from=60.0, speed=1.0)
    assert t is not None and t - 60.0 < 45.0


def test_gap_in_data_does_not_raise_an_alarm():
    det = SpoofDetector()
    for k in range(50):
        det.add_vio(k * DT, 5.0, 0.0)
        assert det.update(k * DT, k * DT * 5.0, 0.0, k * DT * 5.0, 0.0, 0.0, 0.0) is None
    # 20 с без GPS, затем GPS «прыгает» на пройденный путь — разрыв истории не даёт ложной скорости
    assert det.update(30.0, 150.0 + 0.5, 0.0, 150.0, 0.0, 0.0, 0.0) is None


def test_vio_dropping_frames_in_bursts_is_not_a_spoof():
    """VIO отбрасывает пачки кадров (чужой борт в кадре, край пола): 30 % времени по 1,5 с. Живой прогон: две ложные тревоги без подмены."""
    for seed in range(3):
        assert fly(400.0, seed=seed, vio_drop=lambda ts: (ts % 5.0) < 1.5)[0] is None


def test_spoof_still_detected_while_vio_drops_frames():
    t, _, _ = fly(200.0, drift=(1.4, 1.4), drift_from=60.0, vio_drop=lambda ts: (ts % 5.0) < 1.5)
    assert t is not None and t - 60.0 < 8.0


def test_latlon_to_enu_roundtrip_scale():
    e, n = latlon_to_enu(47.0 + 10 / 111320.0, 8.0 + 10 / (111320.0 * math.cos(math.radians(47.0))), 47.0, 8.0)
    assert abs(e - 10.0) < 1e-6 and abs(n - 10.0) < 1e-6
