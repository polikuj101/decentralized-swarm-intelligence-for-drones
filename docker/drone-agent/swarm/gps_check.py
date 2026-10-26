"""swarm/gps_check.py — обнаружение подмены GPS по скоростям (чистые функции, без ROS).

Прежний монитор (flight_ctrl._monitor) сравнивал смещение позиции EKF со смещением VIO за окно 6-10 с (порог 3 м + 15 %, 3 раза подряд).
EKF PX4 сглаживает GPS и сначала «сопротивляется» подмене, поэтому при дрейфе 2 м/с подозрение поднималось через ~40 с в одном прогоне
и не поднималось за 75 с в другом, а привязка VIO бралась на уже смещённой позиции (ошибка ~70 м, HANDOFF п. 3).

Здесь сравнивается скорость сырого GPS (производная его позиции за короткое окно, а не скорость EKF) со скоростью VIO — независимой
оценкой. Превышение рассогласования над допуском накапливается (CUSUM): медленный уход тоже копится, единичный выброс — нет.
Привязка — к состоянию до начала роста суммы: последнее заведомо хорошее."""
import math
from collections import deque

M_PER_DEG_LAT = 111_320.0


def latlon_to_enu(lat, lon, lat0, lon0):
    """Широта/долгота -> восток/север, м, относительно начала мира (равнопромежуточная проекция; мир — 100 м)."""
    return (lon - lon0) * M_PER_DEG_LAT * math.cos(math.radians(lat0)), (lat - lat0) * M_PER_DEG_LAT


class SpoofDetector:
    WIN_S = 2.0          # окно оценки скоростей
    K0, K_REL = 0.4, 0.25  # допуск рассогласования: шум GPS/VIO + относительная ошибка VIO (масштаб, угол) на скорости
    H_M = 1.5            # сколько «лишних» метров накопить до тревоги
    HIST_S = 60.0
    CLEAR_S = 8.0        # столько секунд согласованности до снятия подозрения (плюс проверка абсолютного смещения в flight_ctrl)

    VIO_RATE_HZ = 15.0
    MIN_COVER = 0.5      # доля кадров VIO в окне: меньше — окно не оценивается (кадры с чужим бортом или без текстуры VIO не отдаёт)

    def __init__(self):
        self.hist = deque()                 # (t, gps_e, gps_n, vio_e, vio_n, ekf_e, ekf_n)
        self.vel = deque()                  # скорости принятых кадров VIO: (t, ve, vn)
        self.s, self.t_zero, self.t_last = 0.0, None, None
        self.innov, self.tol = 0.0, 0.0
        self.consistent_s = 0.0

    def reset(self):
        self.hist.clear()
        self.vel.clear()
        self.s, self.t_zero, self.t_last, self.consistent_s = 0.0, None, None, 0.0

    def add_vio(self, t, ve, vn):
        """Скорость одного принятого кадра VIO. Живой прогон 2026-10-26: по приращению накопленного смещения VIO скорость за окно
        занижалась там, где VIO отбрасывал кадры (3-11 % кадров), и детектор дважды поднял ложную тревогу без подмены."""
        self.vel.append((t, ve, vn))
        while self.vel and t - self.vel[0][0] > self.HIST_S:
            self.vel.popleft()

    def update(self, t, gps_e, gps_n, vio_e, vio_n, ekf_e, ekf_n):
        """-> None или привязка (ekf_e, ekf_n, vio_e, vio_n) на момент до начала рассогласования — тревога «подмена GPS»."""
        self.hist.append((t, gps_e, gps_n, vio_e, vio_n, ekf_e, ekf_n))
        while self.hist and t - self.hist[0][0] > self.HIST_S:
            self.hist.popleft()
        ref = None
        for h in reversed(self.hist):
            if t - h[0] >= self.WIN_S:
                ref = h
                break
        step = 0.0 if self.t_last is None else min(t - self.t_last, 1.0)
        self.t_last = t
        if ref is None or t - ref[0] > 2.0 * self.WIN_S:      # мало истории или разрыв (нет GPS/VIO) — оценивать нечем
            return None
        dt = t - ref[0]
        win = [v for v in self.vel if ref[0] < v[0] <= t]
        if len(win) < self.MIN_COVER * self.VIO_RATE_HZ * dt:  # VIO покрывает окно меньше чем наполовину — сравнивать не с чем
            return None
        vg = ((gps_e - ref[1]) / dt, (gps_n - ref[2]) / dt)
        vv = (sum(v[1] for v in win) / len(win), sum(v[2] for v in win) / len(win))
        self.innov = math.hypot(vg[0] - vv[0], vg[1] - vv[1])
        self.tol = self.K0 + self.K_REL * math.hypot(*vv)
        self.s = max(0.0, self.s + (self.innov - self.tol) * step)
        self.consistent_s = self.consistent_s + step if self.innov <= self.tol else 0.0
        if self.s == 0.0:
            self.t_zero = t
        if self.s < self.H_M:
            return None
        t_good = (self.t_zero if self.t_zero is not None else self.hist[0][0]) - self.WIN_S
        anchor = next((h for h in reversed(self.hist) if h[0] <= t_good), self.hist[0])
        self.s = 0.0
        return anchor[5], anchor[6], anchor[3], anchor[4]
