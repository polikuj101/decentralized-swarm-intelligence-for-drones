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


def takeoff_complete(zc, zsrc, alt, elapsed_s, ekf_fallback_s=30.0) -> bool:
    """Взлёт завершён, когда высота достигнута по дальномеру. Оценку EKF сразу после включения моторов принимаем только если дальномер
    так и не появился за ekf_fallback_s секунд: в живом прогоне EKF показывал -8 м на земле (lp_z), и борт 12 с «летел» (FLY) у земли."""
    if zc <= alt - 0.3:
        return False
    return zsrc == "lidar" or elapsed_s >= ekf_fallback_s


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


def smooth_alpha(dt_s, tau_s=0.3):
    """Коэффициент экспоненциального сглаживания по времени, а не по числу сообщений (при другой частоте дальномера лаг не меняется)."""
    if dt_s <= 0:
        return 0.0
    return 1.0 - math.exp(-dt_s / tau_s)
