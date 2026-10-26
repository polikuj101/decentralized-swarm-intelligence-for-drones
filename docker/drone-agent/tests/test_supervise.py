"""tests/test_supervise.py — надзор за состоянием борта после взлёта и снятие отзыва."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from swarm.supervise import DisarmWatch, suspect_may_clear, withdraw_clearable  # noqa: E402


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
