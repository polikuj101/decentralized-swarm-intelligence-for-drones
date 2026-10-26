"""swarm/supervise.py — чистые правила надзора за бортом (без ROS), чтобы их можно было проверить тестами."""

DISARM_GRACE_S = 2.0
WITHDRAW_CLEAR_S = 15.0


class DisarmWatch:
    """PX4 разоружился в воздухе (failsafe, посадка, авария), а flight_ctrl продолжал публиковать NAV/HOLD."""

    def __init__(self, grace_s=DISARM_GRACE_S):
        self.grace_s, self.since = grace_s, None

    def update(self, armed: bool, now: float) -> bool:
        """True, когда борт разоружён непрерывно дольше grace_s."""
        if armed:
            self.since = None
            return False
        if self.since is None:
            self.since = now
        return now - self.since >= self.grace_s

    def reset(self):
        self.since = None


DEGRADED_SPEED_MPS = 1.0
OFFBOARD_NAV_STATE = 14


def speed_cap(src, gps_fix, suspect, cruise_mps, degraded_mps=DEGRADED_SPEED_MPS) -> float:
    """Потолок скорости: без GPS (источник не PX4, gps_fix < 3 или подозрение на GPS) навигация идёт по оптическому потоку, который при редких кадрах
    (рендер отдаёт камеру 1-5 Гц) на крейсерской скорости теряет совпадение изображений: в репетиции демо ошибка позиции росла до 44-106 м."""
    degraded = src != "PX4" or (gps_fix is not None and gps_fix < 3) or bool(suspect)
    return min(cruise_mps, degraded_mps) if degraded else cruise_mps


class OffboardWatch:
    """PX4 может выйти из OFFBOARD (потеря оценки позиции — аварийный режим), а flight_ctrl продолжал слать уставки в пустоту:
    борт спускался и лежал на земле в HOLD. Возвращает True, когда борт взведён, но не в OFFBOARD дольше grace_s (и не чаще retry_s)."""

    def __init__(self, grace_s=1.0, retry_s=2.0):
        self.grace_s, self.retry_s, self.since, self.last = grace_s, retry_s, None, -1e9

    def update(self, armed: bool, nav_state, now: float) -> bool:
        if not armed or nav_state == OFFBOARD_NAV_STATE or nav_state is None:
            self.since = None
            return False
        if self.since is None:
            self.since = now
        if now - self.since >= self.grace_s and now - self.last >= self.retry_s:
            self.last = now
            return True
        return False


def suspect_may_clear(good_count, offset_m, need=8, max_offset_m=8.0) -> bool:
    """Подозрение на GPS снимается, только когда PX4 и VIO согласованы по движению (good_count) И абсолютное расхождение позиций мало.
    Живой прогон: после подмены GPS (дрейф 2 м/с) EKF сделал скачок на ~80 м, смещения за окно снова совпали, подозрение сняли,
    и борт вернулся на PX4-позицию с ошибкой 81 м."""
    return good_count >= need and offset_m <= max_offset_m


def withdraw_clearable(withdrawn: bool, phase_is_nominal: bool, nominal_for_s: float, need_s=WITHDRAW_CLEAR_S) -> bool:
    """Отзыв (RETURN_HOME) снимается, когда условия, вызвавшие его, исчезли и фаза NOMINAL держится need_s секунд.
    Раньше withdrawn был вечным: после одной ложной тревоги борт не возвращался в работу до перезапуска."""
    return withdrawn and phase_is_nominal and nominal_for_s >= need_s
