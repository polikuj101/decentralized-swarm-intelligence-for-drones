"""docker/web-hud/tests/test_convergence.py — чистая логика сходимости web-hud (convergence.py). Запуск: make test-hud."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import convergence as cv  # noqa: E402

N = 5


def st(epoch=7, winners=None, alive=N, rx=100.0):
    return {"epoch": epoch, "winners": {"0": 1} if winners is None else winners,
            "alive": list(range(1, alive + 1)), "rx": rx}


def swarm(**over):
    return {i: st(**over) for i in range(1, N + 1)}


# ───────────── check() ─────────────

def test_converged_single_epoch_no_dup_all_alive():
    r = cv.check(swarm(), N, now=100.5)
    assert r.converged and r.epochs == [7] and r.dup == [] and r.all_alive and r.stale == []


def test_different_epochs_not_converged():
    s = swarm()
    s[4]["epoch"] = 9
    r = cv.check(s, N, now=100.5)
    assert not r.converged and r.epochs == [7, 9]


def test_alive_less_than_n_not_converged():
    s = swarm()
    s[2]["alive"] = [1, 2, 3]
    r = cv.check(s, N, now=100.5)
    assert not r.converged and not r.all_alive


def test_same_task_two_winners_is_duplicate():
    s = swarm()
    s[1]["winners"] = {"3": 1}
    s[4]["winners"] = {"3": 4}
    r = cv.check(s, N, now=100.5)
    assert r.dup == ["3"] and not r.converged


def test_same_winner_everywhere_is_not_duplicate():
    r = cv.check(swarm(winners={"3": 2}), N, now=100.5)
    assert r.dup == [] and r.converged


def test_stale_packet_blocks_convergence():
    s = swarm()
    s[3]["rx"] = 90.0                                   # 10.5 с назад > MAX_AGE_S
    r = cv.check(s, N, now=100.5)
    assert not r.converged and r.stale == [3]


def test_missing_drone_or_no_packet_is_stale():
    s = swarm()
    del s[5]
    s[2]["rx"] = None
    r = cv.check(s, N, now=100.5)
    assert r.stale == [2, 5] and not r.converged


def test_since_rejects_pre_heal_packets():
    r = cv.check(swarm(rx=100.0), N, now=100.5, since=100.2)
    assert not r.converged and r.stale == [1, 2, 3, 4, 5]


def test_packet_received_exactly_at_heal_is_not_counted():
    r = cv.check(swarm(rx=100.2), N, now=100.5, since=100.2)
    assert not r.converged and r.stale == [1, 2, 3, 4, 5]


def test_empty_swarm_state_is_safe():
    s = {i: {"epoch": None, "winners": None, "alive": None, "rx": 100.0} for i in range(1, N + 1)}
    r = cv.check(s, N, now=100.5)
    assert not r.converged and r.epochs == []


# ───────────── Tracker ─────────────

def test_idle_ignores_updates_and_heal_without_split():
    t = cv.Tracker()
    t.update(swarm(), N, now=100.5)
    t.mark_heal(101.0)
    assert t.phase == "idle" and t.converged_s is None


def test_full_cycle_split_heal_converge():
    t = cv.Tracker()
    t.mark_split(10.0)
    s = swarm(rx=20.0)
    s[1]["winners"], s[4]["winners"] = {"3": 1}, {"3": 4}
    t.update(s, N, now=20.5)                            # дубликат во время разделения
    assert t.phase == "split" and t.dup_split == {"3"}
    t.mark_heal(30.0)
    assert t.phase == "merging"
    t.update(swarm(rx=29.0), N, now=30.5)               # пакеты ДО Heal: не считаются
    assert t.phase == "merging" and t.converged_s is None
    t.update(swarm(rx=33.0), N, now=33.2)
    assert t.phase == "converged" and t.converged_s == pytest.approx(3.2)
    snap = t.snapshot(40.0)
    assert snap["merge_s"] == 3.2 and snap["dup_split"] == ["3"] and snap["dup_merge"] == []


def test_epochs_seen_during_merge_are_reported():
    t = cv.Tracker()
    t.mark_split(0.0)
    t.mark_heal(1.0)
    s = swarm(rx=2.0)
    s[5]["epoch"] = 8
    t.update(s, N, now=2.1)
    assert t.phase == "merging" and t.epochs_merge == {7, 8}
    t.update(swarm(epoch=8, rx=3.0), N, now=3.1)
    assert t.phase == "converged" and t.epochs_merge == {7, 8}


def test_flap_after_convergence_keeps_first_result():
    t = cv.Tracker()
    t.mark_split(0.0)
    t.mark_heal(1.0)
    t.update(swarm(rx=2.0), N, now=2.0)
    first = t.converged_s
    s = swarm(rx=3.0)
    s[1]["epoch"] = 99
    t.update(s, N, now=3.0)
    assert t.phase == "converged" and t.converged_s == first


def test_new_split_resets_previous_run():
    t = cv.Tracker()
    t.mark_split(0.0)
    t.mark_heal(1.0)
    t.update(swarm(rx=2.0), N, now=2.0)
    t.mark_split(10.0)
    assert t.phase == "split" and t.converged_s is None and t.dup_split == set()


def test_snapshot_idle_and_merging():
    t = cv.Tracker()
    assert t.snapshot(5.0)["phase"] == "idle"
    t.mark_split(0.0)
    t.mark_heal(1.0)
    s = t.snapshot(3.5)
    assert s["phase"] == "merging" and s["elapsed_s"] == 2.5 and s["merge_s"] is None
