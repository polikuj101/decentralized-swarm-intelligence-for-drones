"""tests/test_gps_check.py — детектор подмены GPS по скоростям (HANDOFF п. 3) на синтетических трассах."""
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from swarm.gps_check import SpoofDetector, latlon_to_enu  # noqa: E402

DT = 0.2


def fly(duration, drift=None, drift_from=1e9, vio_scale=1.03, vio_angle_deg=2.0, gps_noise=0.3, speed=3.0, seed=1, vio_drop=None,
        gps_gap=None, jump=None):
    """Борт летает «змейкой» со скоростью speed; VIO 15 Гц с ошибкой масштаба и угла (3 % и 2° — с запасом к измеренному на стенде:
    за 20 с ошибка интегрирования VIO до ~4 м), vio_drop(t) — кадр отброшен; GPS 5 Гц с шумом и, начиная с drift_from, с подмешанным
    дрейфом drift (м/с, вектор); gps_gap=(t0, t1) — GPS пропадает, jump=(de, dn) — и возвращается со сдвигом.
    -> (время первой тревоги, привязка, истинная позиция в момент начала дрейфа, оценка позиции от привязки по VIO, истинная позиция)."""
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
            hdg = 0.5 * math.sin(ts / 15.0) + 0.8 * math.sin(ts / 40.0)     # непрерывный курс: борт не разворачивается мгновенно
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
        if gps_gap and gps_gap[0] <= t < gps_gap[1]:
            det.pause()
            t += DT
            continue
        if gps_gap and jump and t >= gps_gap[1]:
            off_e, off_n = jump
        ge, gn = x + off_e + rnd.gauss(0, gps_noise), y + off_n + rnd.gauss(0, gps_noise)
        anchor = det.update(t, ge, gn, vx_i, vy_i, ge, gn)     # EKF ≈ GPS (худший случай: EKF сразу следует за подменой)
        if anchor:
            est = (anchor[0] + vx_i - anchor[2], anchor[1] + vy_i - anchor[3])   # как flight_ctrl._estimate при подозрении
            return t, anchor, truth_at_drift, est, (x, y)
        t += DT
    return None, None, truth_at_drift, None, None


def test_no_false_alarm_over_ten_minutes_of_honest_gps():
    for seed in range(5):
        assert fly(600.0, seed=seed)[0] is None


def test_drift_2mps_detected_within_5_s():
    t = fly(200.0, drift=(1.4, 1.4), drift_from=60.0)[0]
    assert t is not None and t - 60.0 < 5.0


def test_position_from_the_anchor_is_correct_after_the_alarm():
    """После тревоги борт идёт по привязке + VIO: оценка должна совпасть с истиной, а не с подменённым GPS."""
    for drift in ((2.0, 0.0), (0.0, 1.0)):
        t, anchor, _, est, truth = fly(300.0, drift=drift, drift_from=60.0)
        assert t is not None and math.hypot(est[0] - truth[0], est[1] - truth[1]) < 4.0


def test_slow_drift_at_cruise_speed_is_caught_by_the_20s_check():
    """0,5 м/с на крейсерской 3 м/с тонет в допуске по скорости (0,4 + 25 %), но копится в смещении за 20 с (автоматическая проверка
    безопасности 2026-10-26)."""
    for seed in range(3):
        t = fly(300.0, drift=(0.0, 1.0), drift_from=60.0, seed=seed)[0]
        assert t is not None and t - 60.0 < 25.0


def test_fix_dropped_and_returned_shifted_is_caught():
    """Подмена «уронить фикс на 5 с и вернуть со сдвигом 8 м»: раньше пропадание GPS обнуляло детектор."""
    t, anchor = fly(200.0, gps_gap=(60.0, 65.0), jump=(6.0, 5.0))[:2]
    assert t is not None and 65.0 <= t < 66.0


def test_long_gap_without_shift_is_not_a_spoof():
    for seed in range(3):
        assert fly(300.0, gps_gap=(60.0, 120.0), seed=seed)[0] is None


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
    t = fly(200.0, drift=(1.4, 1.4), drift_from=60.0, vio_drop=lambda ts: (ts % 5.0) < 1.5)[0]
    assert t is not None and t - 60.0 < 8.0


def test_latlon_to_enu_roundtrip_scale():
    e, n = latlon_to_enu(47.0 + 10 / 111320.0, 8.0 + 10 / (111320.0 * math.cos(math.radians(47.0))), 47.0, 8.0)
    assert abs(e - 10.0) < 1e-6 and abs(n - 10.0) < 1e-6


def test_gap_without_vio_coverage_is_not_compared():
    """Разрыв, за который VIO почти не отдавал кадров (взлёт, низко), — сверять не с чем: тревоги нет даже при сдвиге GPS."""
    t = fly(200.0, gps_gap=(60.0, 65.0), jump=(6.0, 5.0), vio_drop=lambda ts: 60.0 <= ts < 65.0)[0]
    assert t is None or t > 70.0
