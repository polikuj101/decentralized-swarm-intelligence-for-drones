"""tests/test_bump_gate.py — когда координатор повышает эпоху после смены состава живых бортов."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from swarm.bump_gate import BumpGate  # noqa: E402


def test_nothing_pending_nothing_due():
    assert not BumpGate().due(100.0, last_bump=-99.0)


def test_peer_loss_is_urgent_once_guard_passed():
    g = BumpGate()
    g.observe(joined=False, left=True, now=10.0)
    assert g.due(10.0, last_bump=-99.0)                 # поведение как раньше: сразу


def test_peer_loss_still_respects_guard_interval():
    g = BumpGate()
    g.observe(False, True, now=10.0)
    assert not g.due(11.0, last_bump=9.0)               # прошла 1 с из 3
    assert g.due(12.1, last_bump=9.0)


def test_arrival_waits_until_the_alive_set_stops_growing():
    g = BumpGate()
    g.observe(True, False, now=10.0)
    assert not g.due(10.5, last_bump=-99.0)
    assert g.due(11.0, last_bump=-99.0)


def test_each_new_arrival_restarts_the_quiet_period():
    g = BumpGate()
    g.observe(True, False, now=10.0)
    g.observe(True, False, now=10.8)
    assert not g.due(11.2, last_bump=-99.0)
    assert not g.due(11.7, last_bump=-99.0)
    assert g.due(11.8, last_bump=-99.0)


def test_loss_inside_the_waiting_window_makes_it_urgent():
    g = BumpGate()
    g.observe(True, False, now=10.0)
    g.observe(False, True, now=10.2)
    assert g.due(10.2, last_bump=-99.0)


def test_arrival_still_respects_guard_interval():
    g = BumpGate()
    g.observe(True, False, now=10.0)
    assert not g.due(11.5, last_bump=9.0)               # тишина есть, но с прошлого bump'а < 3 с
    assert g.due(12.1, last_bump=9.0)


def test_fire_resets_state_for_the_next_change():
    g = BumpGate()
    g.observe(True, False, now=10.0)
    g.fire()
    assert not g.pending and not g.due(50.0, last_bump=-99.0)
    g.observe(True, False, now=60.0)
    assert not g.due(60.5, last_bump=-99.0) and g.due(61.0, last_bump=-99.0)


def simulate(gate, joins, last_bump, until=30.0, step=0.05):
    """joins: моменты прибытия пиров; возвращает моменты bump'ов при опросе каждые step секунд."""
    bumps, t, pending = [], 0.0, sorted(joins)
    while t < until:
        while pending and pending[0] <= t:
            gate.observe(True, False, pending.pop(0))
        if gate.due(t, last_bump):
            gate.fire()
            last_bump = t
            bumps.append(round(t, 2))
        t += step
    return bumps


def test_merge_wave_gives_exactly_one_bump():
    """Регрессия: пиры после слияния сети узнаются волнами (в трассе — на 0,00 / 0,10 / 0,50 с и 0,25-0,40 с на другой
    стороне). Раньше: bump сразу и ещё один через 3 с; теперь — один, после затишья."""
    waves = [10.94, 11.04, 11.14, 11.44]
    bumps = simulate(BumpGate(), waves, last_bump=3.4)
    assert len(bumps) == 1 and 12.4 <= bumps[0] <= 12.6


def test_legacy_behaviour_bumped_twice_on_the_same_wave():
    """Прежнее поведение (затишье 0 с, guard 3 с): bump на первом прибытии, остальные пиры ставят новый pending и дают
    второй bump через guard — ровно то, что было видно в трассе (эпохи 162 -> 164 у одной стороны, 163 -> 165 у другой)."""
    legacy = BumpGate(join_settle_s=0.0)
    bumps = simulate(legacy, [10.94, 11.04, 11.14, 11.44], last_bump=3.4)
    assert len(bumps) == 2 and bumps[0] < 11.0 and 13.9 <= bumps[1] <= 14.1


# ───────────── absorb: приняли чужую эпоху ─────────────

def test_absorb_cancels_a_pending_arrival_bump():
    g = BumpGate()
    g.observe(True, False, now=10.0)
    g.absorb()
    assert not g.pending and not g.due(20.0, last_bump=-99.0)


def test_absorb_does_not_cancel_an_urgent_bump_after_peer_loss():
    g = BumpGate()
    g.observe(False, True, now=10.0)
    g.absorb()
    assert g.pending and g.due(10.0, last_bump=-99.0)


def test_absorb_without_pending_is_a_noop():
    g = BumpGate()
    g.absorb()
    assert not g.pending


def test_next_change_after_absorb_starts_a_fresh_wait():
    g = BumpGate()
    g.observe(True, False, now=10.0)
    g.absorb()
    g.observe(True, False, now=12.0)
    assert not g.due(12.5, last_bump=-99.0) and g.due(13.0, last_bump=-99.0)


def test_four_drones_with_staggered_timers_produce_one_epoch_not_four():
    """Регрессия по трассе: D2, D5, D3, D1 бампили с интервалом 0,05 с (эпохи 152/153/154). Модель: у каждого борта свой
    gate; кто первый бампит — остальные принимают его эпоху (absorb) и свой bump отменяют."""
    gates = {i: BumpGate() for i in range(1, 5)}
    last = {i: -99.0 for i in gates}
    arrivals = {1: 10.00, 2: 10.02, 3: 10.05, 4: 10.07}
    bumps, t, seen = [], 9.9, set()
    while t < 14.0:
        for i, at in arrivals.items():
            if t >= at and i not in seen:
                gates[i].observe(True, False, t)
                seen.add(i)
        for i, gate in gates.items():
            if gate.due(t, last[i]):
                gate.fire()
                last[i] = t
                bumps.append((round(t, 2), i))
                for j, other in gates.items():
                    if j != i:
                        other.absorb()
        t += 0.01
    assert len(bumps) == 1, bumps
