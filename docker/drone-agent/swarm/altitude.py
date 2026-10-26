"""swarm/altitude.py — высота для управления и отбраковка выбросов VIO (чистые функции, без ROS).

Живые замеры 2026-10-26 (оценка nav_z против позы модели в Gazebo): высота EKF PX4 завышена в среднем на 0,4-1,1 м, местами на 2-4 м,
поэтому борт удерживал «нужную» высоту, летя на 1-3 м ниже и иногда касаясь земли, оставаясь в HOLD с armed=True.
Нисходящий дальномер измеряет высоту над землёй напрямую; EKF остаётся запасным источником."""
import math

LIDAR_MIN_M, LIDAR_MAX_M, LIDAR_MAX_AGE_S = 0.15, 20.0, 0.5
VIO_VMAX_MPS, VIO_TOL_ABS, VIO_TOL_REL = 6.0, 1.0, 0.35


def fused_height(pz_ekf, lidar_h, lidar_age_s):
    """-> (высота для управления, источник "lidar"|"ekf"). Дальномер используется, пока он свежий и в пределах рабочего диапазона."""
    if (lidar_h is None or lidar_age_s is None or lidar_age_s > LIDAR_MAX_AGE_S or not math.isfinite(lidar_h)
            or not LIDAR_MIN_M <= lidar_h <= LIDAR_MAX_M):
        return pz_ekf, "ekf"
    return lidar_h, "lidar"


class HeightFuser:
    """Высота над землёй для управления: дальномер, между его редкими отсчётами — счисление по вертикальной скорости EKF.

    Живая репетиция демо 2026-10-26 при полном отказе GPS: рендер отдавал дальномер лишь 1-3 раза в секунду, свежесть 0,5 с постоянно
    нарушалась, управление уходило на высоту EKF (без GPS она уплывает на 5-100 м): борты то садились на землю, считая, что высоко,
    то один улетел на 68 м. Теперь нет надёжной высоты — нет вертикального движения (src="none"), а не погоня за оценкой EKF."""

    FRESH_S, DR_S, OVER_S = 1.5, 4.0, 1.5

    def __init__(self):
        self.h = self.h_t = self.over_t = None
        self.vz_up, self.vz_t = 0.0, None

    def update_lidar(self, h, now):
        if h is not None and math.isfinite(h) and LIDAR_MIN_M <= h <= LIDAR_MAX_M:
            self.h, self.h_t = h, now

    def update_over_range(self, now):
        self.over_t = now                      # дальномер ничего не видит в пределах максимальной дальности: борт высоко

    def update_vz(self, vz_up, now):
        if vz_up is not None and math.isfinite(vz_up):
            self.vz_up, self.vz_t = vz_up, now

    def height(self, pz_ekf, now):
        """-> (высота, источник): "lidar" | "lidar+vz" | "over" | "none" (высота None — опорной высоты нет)."""
        over_newer = self.over_t is not None and (self.h_t is None or self.over_t >= self.h_t)
        # «ничего не вижу» означает «высоко» только если борт перед этим был высоко (или EKF уверен, что высоко). На земле дальномер тоже
        # возвращает inf (расстояние меньше минимальной дальности 0,1 м): репетиция 2026-10-26, борты «снижались» в землю и PX4 разоружал их.
        was_high = (self.h is not None and self.h > 5.0 and now - self.h_t <= 3.0) or (pz_ekf is not None and pz_ekf > 12.0)
        if over_newer and was_high and now - self.over_t <= self.OVER_S:
            return LIDAR_MAX_M + 1.0, "over"
        if self.h is not None and now - self.h_t <= self.FRESH_S:
            return self.h, "lidar"
        if self.h is not None and now - self.h_t <= self.DR_S:
            return max(0.0, self.h + self.vz_up * (now - self.h_t)), "lidar+vz"
        return None, "none"


def takeoff_complete(zc, zsrc, alt, elapsed_s, ekf_fallback_s=30.0) -> bool:
    """Взлёт завершён, когда высота достигнута по дальномеру. Оценку EKF сразу после включения моторов принимаем только если дальномер
    так и не появился за ekf_fallback_s секунд: в живом прогоне EKF показывал -8 м на земле (lp_z), и борт 12 с «летел» (FLY) у земли."""
    if zc <= alt - 0.3:
        return False
    return zsrc in ("lidar", "lidar+vz") or elapsed_s >= ekf_fallback_s


def vio_outlier(vn, ve, px4_vn=None, px4_ve=None, px4_valid=False) -> bool:
    """Приращение VIO отбрасывается, если скорость нереальна или сильно расходится со скоростью PX4 (когда она достоверна).
    Фазовая корреляция иногда возвращает почти нулевой сдвиг на однородной текстуре или ложный пик: масштаб окна 0,03-0,15 и ошибки угла до 134°."""
    speed = math.hypot(vn, ve)
    if not math.isfinite(speed) or speed > VIO_VMAX_MPS:
        return True
    if px4_valid and px4_vn is not None and px4_ve is not None and math.isfinite(px4_vn) and math.isfinite(px4_ve):
        ref = math.hypot(px4_vn, px4_ve)
        if math.hypot(vn - px4_vn, ve - px4_ve) > VIO_TOL_ABS + VIO_TOL_REL * ref:
            return True
    return False


VIO_ACC_MAX = 4.0           # м/с²: быстрее борт скорость не меняет (x500, наклон до ~20°)


def vio_jump(vn, ve, recent, now, tol_abs=VIO_TOL_ABS, acc_max=VIO_ACC_MAX) -> bool:
    """Проверка VIO без GPS: скорость не может скачком уйти от недавно принятых. recent — [(t, vn, ve)] принятых за последнюю секунду.
    Сравнивать без GPS со скоростью EKF нельзя: она сама строится на VIO, и когда EKF уплывает, отбраковка верных измерений не даёт ему
    вернуться (HANDOFF п. 1: PX4 терял xy_valid и садил борт). Опора — медиана недавних принятых, допуск растёт с возрастом опоры."""
    if not recent:
        return False
    ts, ns, es = zip(*recent)
    ref_n, ref_e = sorted(ns)[len(ns) // 2], sorted(es)[len(es) // 2]
    age = max(0.0, now - max(ts))
    return math.hypot(vn - ref_n, ve - ref_e) > tol_abs + acc_max * age


def object_below(range_m, h_prev, vz_up, dt_s) -> bool:
    """Дальномер резко показал ближе, чем земля по прогнозу (прежняя высота + вертикальная скорость EKF), — под бортом посторонний
    объект (другой борт, край крыши), а не земля. Прогон 2026-10-26: под D5 прошёл D2, дальномер дал 3,2-4,8 м вместо 6,2,
    и масштаб VIO исказился в разы. Высоту для масштаба в такие моменты держим по прогнозу."""
    if h_prev is None or range_m is None or not math.isfinite(range_m):
        return False
    pred = h_prev + (vz_up or 0.0) * dt_s
    return range_m < pred - max(1.0, 0.25 * pred)


def smooth_alpha(dt_s, tau_s=0.3):
    """Коэффициент экспоненциального сглаживания по времени, а не по числу сообщений (при другой частоте дальномера лаг не меняется)."""
    if dt_s <= 0:
        return 0.0
    return 1.0 - math.exp(-dt_s / tau_s)
