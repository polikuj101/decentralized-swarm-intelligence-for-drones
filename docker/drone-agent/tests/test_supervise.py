"""tests/test_supervise.py — надзор за состоянием борта после взлёта и снятие отзыва."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from swarm.supervise import DisarmWatch, OffboardWatch, speed_cap, suspect_may_clear, withdraw_clearable  # noqa: E402


def test_armed_never_trips():
    w = DisarmWatch(2.0)
    assert not any(w.update(True, t) for t in range(10))


def test_short_disarm_blip_is_ignored():
    w = DisarmWatch(2.0)
    assert not w.update(False, 0.0)
    assert not w.update(False, 1.5)
    assert not w.update(True, 1.6)          # снова взведён — отсчёт сброшен
    assert not w.update(False, 3.0)
    assert not w.update(False, 4.9)


def test_continuous_disarm_trips_after_grace():
    w = DisarmWatch(2.0)
    assert not w.update(False, 10.0)
    assert w.update(False, 12.0)


def test_reset_restarts_the_clock():
    w = DisarmWatch(2.0)
    w.update(False, 0.0)
    w.reset()
    assert not w.update(False, 5.0)


def test_withdraw_needs_nominal_held_long_enough():
    assert not withdraw_clearable(True, True, 14.9)
    assert withdraw_clearable(True, True, 15.0)


def test_withdraw_not_cleared_while_phase_degraded_or_not_withdrawn():
    assert not withdraw_clearable(True, False, 100.0)
    assert not withdraw_clearable(False, True, 100.0)


def test_gps_suspect_not_cleared_while_gps_is_offset_from_vio():
    """Регрессия: после дрейфа GPS позиции совпали по движению, но PX4 смещён на 81 м — оставаться на VIO."""
    assert not suspect_may_clear(good_count=10, offset_m=81.0)


def test_gps_suspect_cleared_when_consistent_and_close():
    assert suspect_may_clear(good_count=8, offset_m=2.0)


def test_gps_suspect_needs_enough_consistent_checks():
    assert not suspect_may_clear(good_count=7, offset_m=0.5)


def test_speed_cap_is_cruise_when_gps_navigation_is_healthy():
    assert speed_cap("PX4", 3, False, 3.0) == 3.0


def test_speed_cap_drops_without_gps_or_on_vio_dr_or_suspect():
    assert speed_cap("PX4", 0, False, 3.0) == 1.0
    assert speed_cap("VIO", 3, False, 3.0) == 1.0
    assert speed_cap("DR", 3, False, 3.0) == 1.0
    assert speed_cap("PX4", 3, True, 3.0) == 1.0
    assert speed_cap("NONE", 0, False, 3.0) == 1.0


def test_speed_cap_never_exceeds_cruise():
    assert speed_cap("VIO", 0, True, 0.6) == 0.6


def test_offboard_watch_stays_quiet_in_offboard_or_when_disarmed():
    w = OffboardWatch()
    assert not any(w.update(True, 14, t) for t in range(10))
    assert not any(w.update(False, 1, t) for t in range(10, 20))
    assert not w.update(True, None, 30.0)


def test_offboard_watch_asks_for_reentry_after_grace_and_retries_slowly():
    w = OffboardWatch(grace_s=1.0, retry_s=2.0)
    assert not w.update(True, 1, 0.0)
    assert not w.update(True, 1, 0.5)
    assert w.update(True, 1, 1.0)
    assert not w.update(True, 1, 2.0)        # ещё рано повторять
    assert w.update(True, 1, 3.1)


def test_offboard_watch_resets_when_back_in_offboard():
    w = OffboardWatch()
    w.update(True, 1, 0.0)
    w.update(True, 14, 0.5)
    assert not w.update(True, 1, 0.8)        # отсчёт начался заново


def test_offboard_watch_never_reenters_from_land_rtl_descend_termination_or_manual():
    """Замечание проверки безопасности: нельзя отменять посадку, возврат или аварийное завершение."""
    for state in (0, 5, 12, 13, 18, 3, 23):
        w = OffboardWatch()
        assert not any(w.update(True, state, t) for t in range(0, 20)), state


def test_offboard_watch_reenters_from_benign_fallbacks_only():
    for state in (1, 2, 4):
        w = OffboardWatch()
        w.update(True, state, 0.0)
        assert w.update(True, state, 1.5), state


def test_offboard_watch_gives_up_after_the_retry_cap_and_recovers_after_success():
    w = OffboardWatch(grace_s=0.0, retry_s=1.0, max_tries=3)
    fired = [w.update(True, 1, t) for t in range(0, 10)]
    assert sum(fired) == 3
    w.update(True, 14, 20.0)                  # вернулись в OFFBOARD — счётчик сброшен
    assert w.update(True, 1, 21.0) or w.update(True, 1, 22.5)
