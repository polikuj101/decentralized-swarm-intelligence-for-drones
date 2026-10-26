"""tests/test_altitude.py — высота по дальномеру с запасным EKF и отбраковка выбросов VIO."""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from swarm.altitude import fused_height, object_below, smooth_alpha, vio_jump, vio_outlier  # noqa: E402


def test_fresh_lidar_wins_over_a_biased_ekf():
    assert fused_height(4.6, 2.4, 0.1) == (2.4, "lidar")


def test_stale_missing_or_out_of_range_lidar_falls_back_to_ekf():
    assert fused_height(4.6, 2.4, 0.9) == (4.6, "ekf")
    assert fused_height(4.6, None, None) == (4.6, "ekf")
    assert fused_height(4.6, 0.05, 0.1) == (4.6, "ekf")          # ниже минимальной дальности
    assert fused_height(4.6, 31.0, 0.1) == (4.6, "ekf")
    assert fused_height(4.6, float("inf"), 0.1) == (4.6, "ekf")
    assert fused_height(4.6, float("nan"), 0.1) == (4.6, "ekf")


def test_plausible_vio_increment_is_kept():
    assert not vio_outlier(2.8, 0.1, px4_vn=3.0, px4_ve=0.0, px4_valid=True)


def test_near_zero_vio_while_px4_moves_is_an_outlier():
    """Окно с масштабом VIO/истина 0,03 при движении 3 м/с."""
    assert vio_outlier(0.1, 0.0, px4_vn=3.0, px4_ve=0.0, px4_valid=True)


def test_wrong_direction_vio_is_an_outlier():
    assert vio_outlier(-2.5, 1.5, px4_vn=3.0, px4_ve=0.0, px4_valid=True)


def test_absurd_speed_is_an_outlier_even_without_px4_reference():
    assert vio_outlier(9.0, 0.0)
    assert not vio_outlier(2.0, 1.0)
    assert vio_outlier(float("nan"), 0.0)


def test_px4_reference_ignored_when_not_valid():
    assert not vio_outlier(0.1, 0.0, px4_vn=3.0, px4_ve=0.0, px4_valid=False)


def test_smoothing_depends_on_time_not_on_message_count():
    a_fast, a_slow = smooth_alpha(0.05), smooth_alpha(0.2)
    assert 0 < a_fast < a_slow < 1
    assert math.isclose(1 - (1 - a_fast) ** 4, a_slow, rel_tol=1e-9)       # четыре быстрых шага = один медленный
    assert smooth_alpha(0.0) == 0.0


def test_takeoff_needs_lidar_height_not_a_glitchy_ekf():
    from swarm.altitude import takeoff_complete
    assert not takeoff_complete(8.3, "ekf", 5.4, elapsed_s=3.0)          # EKF показывает 8 м на земле
    assert takeoff_complete(5.3, "lidar", 5.4, elapsed_s=3.0)
    assert not takeoff_complete(2.0, "lidar", 5.4, elapsed_s=3.0)       # ещё не набрали высоту


def test_takeoff_falls_back_to_ekf_when_the_rangefinder_never_appears():
    from swarm.altitude import takeoff_complete
    assert takeoff_complete(5.3, "ekf", 5.4, elapsed_s=31.0)
    assert not takeoff_complete(2.0, "ekf", 5.4, elapsed_s=31.0)


# -- HeightFuser: дальномер + счисление по вертикальной скорости, без погони за EKF

def _fuser():
    from swarm.altitude import HeightFuser
    return HeightFuser()


def test_fresh_lidar_is_used_directly():
    f = _fuser(); f.update_lidar(3.1, 10.0)
    assert f.height(7.0, 10.5) == (3.1, "lidar")


def test_between_slow_lidar_samples_height_follows_vertical_speed():
    f = _fuser(); f.update_lidar(3.0, 10.0); f.update_vz(0.5, 11.9)
    z, src = f.height(9.0, 12.0)             # дальномеру 2 с: «свежим» не считается, держимся на его отсчёте и скорости
    assert src == "lidar+vz" and abs(z - 4.0) < 1e-9


def test_without_any_reference_height_is_none_instead_of_chasing_the_ekf():
    f = _fuser()
    assert f.height(5.9, 100.0) == (None, "none")
    f.update_lidar(3.0, 10.0)
    assert f.height(5.9, 20.0) == (None, "none")            # отсчёт давно устарел


def test_out_of_range_lidar_means_high_and_commands_descent():
    f = _fuser(); f.update_lidar(18.0, 10.0); f.update_over_range(11.0)
    z, src = f.height(2.0, 11.5)
    assert src == "over" and z > 20.0


def test_new_valid_lidar_cancels_the_over_range_flag():
    f = _fuser(); f.update_over_range(10.0); f.update_lidar(12.0, 11.0)
    assert f.height(2.0, 11.2) == (12.0, "lidar")


def test_invalid_lidar_samples_are_ignored():
    f = _fuser()
    for bad in (0.05, 31.0, float("inf"), float("nan"), None):
        f.update_lidar(bad, 1.0)
    assert f.height(4.0, 1.1) == (None, "none")


def test_takeoff_accepts_dead_reckoned_lidar_height():
    from swarm.altitude import takeoff_complete
    assert takeoff_complete(5.3, "lidar+vz", 5.4, elapsed_s=3.0)
    assert not takeoff_complete(5.3, "none", 5.4, elapsed_s=3.0)


def test_inf_on_the_ground_is_not_treated_as_high():
    """Регрессия: дальномер на земле (ближе минимальной дальности) отдаёт inf; борт 'снижался' в землю и PX4 разоружал его."""
    f = _fuser(); f.update_over_range(5.0)
    assert f.height(1.5, 5.2) == (None, "none")


def test_inf_after_being_high_means_even_higher():
    f = _fuser(); f.update_lidar(12.0, 10.0); f.update_over_range(10.5)
    assert f.height(2.0, 10.8)[1] == "over"


def test_inf_with_ekf_far_above_ground_means_high():
    f = _fuser(); f.update_over_range(10.0)
    z, src = f.height(30.0, 10.2)
    assert src == "over" and z > 20.0


def test_vio_without_history_is_not_a_jump():
    assert not vio_jump(2.0, 0.0, [], now=10.0)


def test_vio_consistent_with_recent_accepted_is_kept():
    recent = [(9.8, 1.0, 0.2), (9.9, 1.1, 0.1), (10.0, 1.0, 0.0)]
    assert not vio_jump(1.3, 0.1, recent, now=10.07)


def test_vio_jump_away_from_recent_accepted_is_rejected():
    """Ложный пик фазовой корреляции: 3 м/с в другую сторону за один кадр."""
    recent = [(9.8, 1.0, 0.2), (9.9, 1.1, 0.1), (10.0, 1.0, 0.0)]
    assert vio_jump(-1.5, 2.5, recent, now=10.07)


def test_old_reference_allows_larger_change():
    """После паузы в кадрах борт мог разогнаться: допуск растёт с возрастом опоры."""
    recent = [(9.0, 0.0, 0.0)]
    assert vio_jump(2.5, 0.0, recent, now=9.1)
    assert not vio_jump(2.5, 0.0, recent, now=9.5)


def test_drone_passing_below_is_an_object_not_ground():
    assert object_below(3.2, 6.2, 0.0, 0.05)          # D2 под D5, прогон 2026-10-26


def test_normal_descent_is_not_an_object():
    assert not object_below(5.6, 6.2, -1.0, 0.5)      # снижение 1 м/с: прогноз 5,7
    assert not object_below(6.0, 6.2, 0.0, 0.05)      # шум


def test_low_altitude_needs_at_least_a_metre_jump():
    assert not object_below(1.3, 2.0, 0.0, 0.05)
    assert object_below(0.8, 2.0, 0.0, 0.05)
