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


def withdraw_clearable(withdrawn: bool, phase_is_nominal: bool, nominal_for_s: float, need_s=WITHDRAW_CLEAR_S) -> bool:
    """Отзыв (RETURN_HOME) снимается, когда условия, вызвавшие его, исчезли и фаза NOMINAL держится need_s секунд.
    Раньше withdrawn был вечным: после одной ложной тревоги борт не возвращался в работу до перезапуска."""
    return withdrawn and phase_is_nominal and nominal_for_s >= need_s
