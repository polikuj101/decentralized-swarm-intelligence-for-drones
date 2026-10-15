"""tests/test_features_phase.py — тесты для features, phase tracker и property-тест кандидатов (G2)."""
import random

from swarm import actions as A
from swarm.candidates import enumerate_candidates
from swarm.features import build_obs
from swarm.phase import PhaseTracker
from swarm.synth import synth_state


def test_obs_buckets_and_text():
    rng = random.Random(42)
    st = synth_state(rng)
    obs = build_obs(st)
    buckets = obs.buckets(A.Phase.NOMINAL)
    assert buckets["phase"] == "nominal"
    assert buckets["link"] in ("good", "fair", "poor")
    assert buckets["gps"] in ("lost", "suspect", "ok")
    assert buckets["sigma"] in ("low", "mid", "high", "critical")
    assert buckets["silent"] in ("long", "short", "none")
    assert buckets["lost"] in ("none", "some", "half", "most")
    assert buckets["task"] in ("idle", "target", "sector")
    text = obs.text(A.Phase.NOMINAL)
    assert text.startswith("phase nominal.")
    assert "link " in text
    assert "gps " in text
    assert "sigma " in text


def test_phase_tracker_hysteresis():
    tracker = PhaseTracker()
    assert tracker.phase == A.Phase.NOMINAL

    rng = random.Random(10)
    st = synth_state(rng)
    # Warm uptime
    st["t"] = 60.0
    st["nav"]["sigma"] = 45.0  # Critical
    o = build_obs(st)
    ph = tracker.update(o, now=100.0)
    assert ph == A.Phase.CRITICAL  # Instant escalation

    # Lower severity to NOMINAL
    st["nav"]["sigma"] = 2.0
    st["link_q"] = 0.9
    st["nav"]["gps_fix"] = 3
    st["nav"]["gps_suspect"] = False
    for p in st["peers"].values():
        p["alive"] = True
        p["silent_s"] = 0.5
    st["alive"] = [1, 2, 3, 4, 5]
    o2 = build_obs(st)

    # Immediately, deescalation should NOT happen (hold down 5s)
    ph2 = tracker.update(o2, now=101.0)
    assert ph2 == A.Phase.CRITICAL

    # After 3s (total 4s), still CRITICAL
    ph3 = tracker.update(o2, now=104.0)
    assert ph3 == A.Phase.CRITICAL

    # After 6s, deescalates to NOMINAL
    ph4 = tracker.update(o2, now=106.5)
    assert ph4 == A.Phase.NOMINAL


def test_candidates_property_g2():
    # G2: Property-тест: 10 000 случайных состояний × 6 фаз × 2 источника,
    # ни один кандидат не нарушает permitted() (0 нарушений).
    rng = random.Random(12345)
    phases = list(A.Phase)
    sources = ["S1", "S2"]

    for _ in range(10000):
        st = synth_state(rng)
        obs = build_obs(st)
        for ph in phases:
            for src in sources:
                cands = enumerate_candidates(st, obs, ph, src)
                for a in cands:
                    assert A.permitted(ph, src, a.tool), (
                        f"VIOLATION: tool {a.tool} not permitted in phase {ph.value} for source {src}"
                    )
