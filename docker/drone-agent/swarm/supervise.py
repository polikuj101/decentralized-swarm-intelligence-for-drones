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


def suspect_may_clear(good_count, offset_m, need=8, max_offset_m=8.0) -> bool:
    """Подозрение на GPS снимается, только когда PX4 и VIO согласованы по движению (good_count) И абсолютное расхождение позиций мало.
    Живой прогон: после подмены GPS (дрейф 2 м/с) EKF сделал скачок на ~80 м, смещения за окно снова совпали, подозрение сняли,
    и борт вернулся на PX4-позицию с ошибкой 81 м."""
    return good_count >= need and offset_m <= max_offset_m


def withdraw_clearable(withdrawn: bool, phase_is_nominal: bool, nominal_for_s: float, need_s=WITHDRAW_CLEAR_S) -> bool:
    """Отзыв (RETURN_HOME) снимается, когда условия, вызвавшие его, исчезли и фаза NOMINAL держится need_s секунд.
    Раньше withdrawn был вечным: после одной ложной тревоги борт не возвращался в работу до перезапуска."""
    return withdrawn and phase_is_nominal and nominal_for_s >= need_s
