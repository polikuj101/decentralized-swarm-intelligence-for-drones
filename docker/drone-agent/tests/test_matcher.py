"""tests/test_matcher.py — тесты для Matcher, rule_score, cooldown и property-тест решений."""
import random

from swarm import actions as A
from swarm.candidates import enumerate_candidates
from swarm.features import build_obs
from swarm.matcher import Decision, Matcher, rule_score
from swarm.synth import synth_state


def test_rule_score_values():
    rng = random.Random(1)
    st = synth_state(rng)
    obs = build_obs(st)

    # Fake n_suspect > 0
    obs_suspect = type(obs)(
        uptime_s=obs.uptime_s, n_total=obs.n_total, n_alive=obs.n_alive, link_q=obs.link_q,
        gps_fix=obs.gps_fix, gps_suspect=obs.gps_suspect, sigma=obs.sigma,
        min_silent_s=obs.min_silent_s, max_silent_s=obs.max_silent_s,
        n_suspect=1, idle=obs.idle, task_kind=obs.task_kind,
        n_open_targets=obs.n_open_targets, withdrawn=obs.withdrawn, override_active=obs.override_active,
    )
    fo = A.DeclareFailover(args=A.DeclareFailoverArgs(failed=2))
    assert rule_score(fo, obs_suspect, A.Phase.NOMINAL) == 0.95

    rh = A.ReturnHome()
    assert rule_score(rh, obs, A.Phase.CRITICAL) == 0.95
    assert rule_score(rh, obs, A.Phase.NOMINAL) == 0.0

    hp = A.HoldPosition()
    assert rule_score(hp, obs, A.Phase.CRITICAL) == 0.8

    ej = A.EvadeJamming()
    assert rule_score(ej, obs, A.Phase.DEGRADED_COMMS) == 0.75
    assert rule_score(ej, obs, A.Phase.NOMINAL) == 0.0


def test_matcher_rules_decide_and_cooldown():
    matcher = Matcher(mode="rules", tau=0.5)
    rng = random.Random(2)
    st = synth_state(rng)
    # Ensure drone has suspect peer
    st["peers"]["2"]["alive"] = False
    st["peers"]["2"]["declared_failed"] = False
    st["peers"]["2"]["silent_s"] = 15.0
    st["t"] = 50.0
    obs = build_obs(st)
    phase = A.Phase.NOMINAL
    cands = [a for a in enumerate_candidates(st, obs, phase, "S1") if a.tool == "DECLARE_FAILOVER"]

    now = 100.0
    dec = matcher.decide(cands, obs, phase, now)
    assert isinstance(dec, Decision)
    assert dec.action is not None
    assert dec.action.tool == "DECLARE_FAILOVER"
    assert dec.path == "rules"
    assert dec.score >= 0.5

    # Calling immediately again with same now -> should be blocked by cooldown
    dec2 = matcher.decide(cands, obs, phase, now + 1.0)
    assert dec2.action is None
    assert dec2.path == "idle"

    # After cooldown expires (20s for failover)
    dec3 = matcher.decide(cands, obs, phase, now + 25.0)
    assert dec3.action is not None
    assert dec3.action.tool == "DECLARE_FAILOVER"


def test_matcher_property_test_g2():
    # 5000 случайных состояний × 6 фаз: ни одно решение не нарушает permitted()
    matcher = Matcher(mode="rules", tau=0.5)
    rng = random.Random(999)
    phases = list(A.Phase)

    for _ in range(5000):
        st = synth_state(rng)
        obs = build_obs(st)
        for ph in phases:
            cands = enumerate_candidates(st, obs, ph, "S1")
            matcher.until.clear()  # reset cooldowns for pure property check
            dec = matcher.decide(cands, obs, ph, now=st["t"])
            if dec.action is not None:
                assert A.permitted(ph, "S1", dec.action.tool), (
                    f"VIOLATION: Matcher chose {dec.action.tool} not permitted in phase {ph.value}"
                )
