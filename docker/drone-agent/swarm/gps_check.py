"""swarm/gps_check.py — обнаружение подмены GPS по сравнению с VIO (чистые функции, без ROS).

Прежний монитор (flight_ctrl._monitor) сравнивал смещение позиции EKF со смещением VIO за окно 6-10 с (порог 3 м + 15 %, 3 раза подряд).
EKF PX4 сглаживает GPS и сначала «сопротивляется» подмене, поэтому при дрейфе 2 м/с подозрение поднималось через ~40 с в одном прогоне
и не поднималось за 75 с в другом, а привязка VIO бралась на уже смещённой позиции (ошибка ~70 м, HANDOFF п. 3).

Три проверки сырого GPS (не EKF) против VIO — независимой оценки:
1. Скорость за 2 с: превышение рассогласования над допуском копится (CUSUM) — быстрый уход ловится за 2-4 с.
2. Смещение за 20 с: GPS против проинтегрированной скорости VIO с ограниченным порогом — медленный уход, который тонет в допуске
   по скорости (автоматическая проверка безопасности 2026-10-26: дрейф < 1 м/с на крейсерской скорости не ловился).
3. Скачок через разрыв: пропадание GPS (или выход из устойчивого полёта) не обнуляет детектор, а запоминает последнюю хорошую пару;
   когда GPS возвращается, его смещение за разрыв сверяется со смещением VIO (иначе подмена «уронить фикс и вернуть со сдвигом»
   проходила бы незамеченной).
Привязка — к последнему заведомо хорошему состоянию."""
import math
from collections import deque

M_PER_DEG_LAT = 111_320.0


def latlon_to_enu(lat, lon, lat0, lon0):
    """Широта/долгота -> восток/север, м, относительно начала мира (равнопромежуточная проекция; мир — 100 м)."""
    return (lon - lon0) * M_PER_DEG_LAT * math.cos(math.radians(lat0)), (lat - lat0) * M_PER_DEG_LAT


class SpoofDetector:
    WIN_S = 2.0          # окно оценки скоростей
    K0, K_REL = 0.4, 0.25  # допуск рассогласования скоростей: шум GPS/VIO + относительная ошибка VIO на скорости
    H_M = 1.5            # сколько «лишних» метров накопить до тревоги
    LONG_S = 20.0        # окно абсолютной проверки
    # Допуск смещения: 5 м + 3 % пути. Проинтегрированная скорость VIO против Gazebo за 20-секундные окна (3 прогона по 5 бортов,
    # 2026-10-26, текущий код VIO): ошибка до ~4 м, в основном абсолютная, а не пропорциональная пути; худшее отношение к этому
    # допуску 0,82. Дрейф 0,5 м/с на крейсерской скорости набирает его за ~15-20 с.
    # Живой прогон 2026-10-26 с допуском 5 м: ложные тревоги на 5,5-6,6 м у бортов без подмены, поэтому 8 м (дрейф 0,5 м/с на пределе).
    ABS_TOL_M, ABS_REL = 8.0, 0.03
    GAP_TOL_M, GAP_REL, GAP_GROW = 5.0, 0.03, 0.02   # скачок через разрыв: то же + 2 см на секунду разрыва
    HIST_S = 60.0
    CLEAR_S = 8.0        # столько секунд согласованности до снятия подозрения (плюс проверка абсолютного смещения в flight_ctrl)

    VIO_RATE_HZ = 15.0
    MIN_COVER = 0.5      # доля кадров VIO в коротком окне: меньше — окно не оценивается (кадры с чужим бортом или без текстуры VIO не отдаёт)
    LONG_COVER = 0.7     # то же для окна 20 с
    VIO_FILL_S = 2.0     # пропуск кадров VIO (чужой борт в кадре, нет текстуры) до 2 с заполняется скоростью следующего кадра

    def __init__(self):
        self.hist = deque()                 # (t, gps_e, gps_n, vio_e, vio_n, ekf_e, ekf_n, int_e, int_n)
        self.vel = deque()                  # скорости принятых кадров VIO: (t, ve, vn)
        self.int_e = self.int_n = 0.0       # проинтегрированная скорость VIO, м
        self.t_vio = None
        self.gap = None                     # последняя хорошая запись перед разрывом
        self.s, self.t_zero, self.t_last = 0.0, None, None
        self.innov, self.tol, self.abs_err = 0.0, 0.0, 0.0
        self.consistent_s = 0.0
        self.reason = ""

    def reset(self):
        self.hist.clear()
        self.vel.clear()
        self.gap = None
        self.s, self.t_zero, self.t_last, self.consistent_s = 0.0, None, None, 0.0

    def pause(self):
        """GPS пропал или борт вышел из устойчивого полёта: сравнивать сейчас не с чем, но последняя хорошая запись сохраняется
        для проверки скачка, когда GPS вернётся. Интеграл VIO продолжает копиться (add_vio)."""
        if self.gap is None and self.hist:
            self.gap = self.hist[-1]
        self.hist.clear()
        self.s, self.t_zero, self.t_last, self.consistent_s = 0.0, None, None, 0.0

    def add_vio(self, t, ve, vn):
        """Скорость одного принятого кадра VIO. Живой прогон 2026-10-26: по приращению накопленного смещения VIO скорость за окно
        занижалась там, где VIO отбрасывал кадры (3-11 % кадров), и детектор дважды поднял ложную тревогу без подмены."""
        if self.t_vio is not None:
            dt = min(max(t - self.t_vio, 0.0), self.VIO_FILL_S)
            self.int_e, self.int_n = self.int_e + ve * dt, self.int_n + vn * dt
        self.t_vio = t
        self.vel.append((t, ve, vn))
        while self.vel and t - self.vel[0][0] > self.HIST_S:
            self.vel.popleft()

    def _alarm(self, anchor, reason):
        self.s, self.reason = 0.0, reason
        self.hist.clear()
        return anchor[5], anchor[6], anchor[3], anchor[4]

    def update(self, t, gps_e, gps_n, vio_e, vio_n, ekf_e, ekf_n):
        """-> None или привязка (ekf_e, ekf_n, vio_e, vio_n) на последнее заведомо хорошее состояние — тревога «подмена GPS»."""
        rec = (t, gps_e, gps_n, vio_e, vio_n, ekf_e, ekf_n, self.int_e, self.int_n)
        if self.gap is not None:                                # GPS вернулся после разрыва: сверка скачка
            g, self.gap = self.gap, None
            covered = sum(1 for v in self.vel if g[0] < v[0] <= t) >= self.LONG_COVER * self.VIO_RATE_HZ * (t - g[0])
        else:
            covered = False
        # Сверять можно, только если VIO покрывал разрыв: на взлёте и ниже 2 м VIO отдаёт мало кадров, и его смещение за разрыв неверно
        # (живой прогон 2026-10-26: три ложные тревоги «после разрыва 3-7 с» сразу после выхода в устойчивый полёт). Покрытие кадрами
        # от GPS не зависит, так что подмене «уронить фикс и вернуть со сдвигом» это лазейки не даёт.
        if covered:
            de_g, dn_g = gps_e - g[1], gps_n - g[2]
            de_v, dn_v = self.int_e - g[7], self.int_n - g[8]
            err = math.hypot(de_g - de_v, dn_g - dn_v)
            tol = self.GAP_TOL_M + self.GAP_REL * math.hypot(de_v, dn_v) + self.GAP_GROW * (t - g[0])
            if err > tol:
                self.abs_err = err
                return self._alarm(g, f"после разрыва {t - g[0]:.0f} с GPS сместился на {err:.1f} м иначе, чем VIO (допуск {tol:.1f})")
        self.hist.append(rec)
        while self.hist and t - self.hist[0][0] > self.HIST_S:
            self.hist.popleft()
        step = 0.0 if self.t_last is None else min(t - self.t_last, 1.0)
        self.t_last = t

        # 2. абсолютная проверка за LONG_S
        ref_l = next((h for h in self.hist if t - h[0] <= self.LONG_S), None)
        if ref_l is not None and t - ref_l[0] >= 0.9 * self.LONG_S:
            n = sum(1 for v in self.vel if ref_l[0] < v[0] <= t)
            if n >= self.LONG_COVER * self.VIO_RATE_HZ * (t - ref_l[0]):
                de_v, dn_v = self.int_e - ref_l[7], self.int_n - ref_l[8]
                self.abs_err = math.hypot((gps_e - ref_l[1]) - de_v, (gps_n - ref_l[2]) - dn_v)
                tol = self.ABS_TOL_M + self.ABS_REL * math.hypot(de_v, dn_v)
                if self.abs_err > tol:
                    return self._alarm(ref_l, f"за {t - ref_l[0]:.0f} с GPS сместился на {self.abs_err:.1f} м иначе, чем VIO (допуск {tol:.1f})")

        # 1. скорости за WIN_S, CUSUM
        ref = None
        for h in reversed(self.hist):
            if t - h[0] >= self.WIN_S:
                ref = h
                break
        if ref is None or t - ref[0] > 2.0 * self.WIN_S:
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
        return self._alarm(anchor, f"скорость GPS расходится с VIO на {self.innov:.1f} м/с (допуск {self.tol:.1f})")
