"""tests/test_s1_rules.py — таблица правил System 1: номера, срабатывание, поведение при дрейфе GPS."""
import dataclasses
import random

from swarm import actions as A
from swarm.features import build_obs
from swarm.rules import RULES, fired
from swarm.synth import synth_state

P = A.Phase


def _obs(**kw):
    base = build_obs(synth_state(random.Random(3)))
    base = dataclasses.replace(base, n_suspect=0, idle=False, n_open_targets=0, sigma=2.0, withdrawn=False,
                               override_active=False)
    return dataclasses.replace(base, **kw)


def test_rule_ids_unique_and_ordered():
    ids = [r.id for r in RULES]
    assert ids == ["R%d" % i for i in range(1, len(RULES) + 1)]


def test_each_rule_fires_under_its_condition():
    cases = {
        "R1": ("DECLARE_FAILOVER", _obs(n_suspect=1), P.NOMINAL),
        "R2": ("RETURN_HOME", _obs(), P.CRITICAL),
        "R3": ("RETURN_HOME", _obs(sigma=25.0), P.ISOLATED),
        "R4": ("HOLD_POSITION", _obs(), P.CRITICAL),
        "R5": ("HOLD_POSITION", _obs(sigma=25.0), P.DEGRADED_NAV),
        "R6": ("EVADE_JAMMING", _obs(), P.DEGRADED_COMMS),
        "R7": ("HOLD_SECTOR", _obs(sigma=12.0), P.DEGRADED_NAV),
        "R8": ("HOLD_SECTOR", _obs(), P.ISOLATED),
        "R9": ("SCAN_TARGET", _obs(idle=True, n_open_targets=1), P.NOMINAL),
    }
    assert set(cases) == {r.id for r in RULES}
    for rid, (tool, o, ph) in cases.items():
        score, got = fired(tool, o, ph)
        assert got == rid and score > 0.0, rid


def test_no_rule_in_calm_state():
    o = _obs()
    for tool in {r.tool for r in RULES}:
        assert fired(tool, o, P.NOMINAL) == (0.0, "")


def test_higher_score_rule_wins_for_one_tool():
    # CRITICAL и ISOLATED с sigma > 20 не бывают вместе, но для HOLD_SECTOR выбирается максимум
    score, rid = fired("HOLD_SECTOR", _obs(sigma=12.0), P.DEGRADED_NAV)
    assert (score, rid) == (0.65, "R7")


def test_gps_drift_reflex_waits_for_sigma():
    """Дрейф GPS при живом VIO: sigma растёт на 0.02*v + 0.02 м/с (flight_ctrl.py), v ~ 2 м/с.
    Пока sigma < 10 м, S1 в DEGRADED_NAV молчит (VIO держит позицию). Тест фиксирует это поведение и время до R7 / R5."""
    rate = 0.02 * 2.0 + 0.02
    t = 0.0
    sigma = 5.0
    first = {}
    while t < 600.0 and len(first) < 2:
        o = _obs(sigma=sigma, gps_suspect=True)
        for tool, rid in (("HOLD_SECTOR", "R7"), ("HOLD_POSITION", "R5")):
            if rid not in first and fired(tool, o, P.DEGRADED_NAV)[1] == rid:
                first[rid] = t
        sigma += rate
        t += 1.0
    assert first["R7"] > 60.0      # реакция на дрейф при исправном VIO не быстрее минуты
    assert first["R5"] > first["R7"]
