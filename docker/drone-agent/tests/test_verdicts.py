"""tests/test_verdicts.py — вердикт решения берётся из ответа координатора, а не из факта публикации."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from swarm.verdicts import VerdictResolver  # noqa: E402


def th(i, verdict="ACCEPTED", source="S1"):
    return {"id": i, "source": source, "verdict": verdict, "reason": ""}


def test_non_accepted_passes_through_immediately():
    r = VerdictResolver()
    assert r.add(th(1, "NOOP"), 0.0)["verdict"] == "NOOP"
    assert r.add(th(2, "FALLBACK"), 0.0)["verdict"] == "FALLBACK"
    assert r.drain(0.0) == []


def test_accepted_waits_then_applied():
    r = VerdictResolver()
    assert r.add(th(1), 0.0) is None
    assert r.drain(1.0) == []
    r.observe({"id": 1, "source": "S1", "ok": True, "msg": "hold S0"})
    out = r.drain(1.1)
    assert [(x["id"], x["verdict"]) for x in out] == [(1, "APPLIED")]
    assert r.drain(2.0) == []


def test_rejected_by_coordinator_carries_reason():
    r = VerdictResolver()
    r.add(th(7), 0.0)
    r.observe({"id": 7, "source": "S1", "ok": False, "msg": "busy: override active"})
    out = r.drain(0.5)
    assert out[0]["verdict"] == "REJECTED" and "override" in out[0]["reason"]


def test_same_id_other_source_does_not_confirm():
    r = VerdictResolver()
    r.add(th(3, source="S2"), 0.0)
    r.observe({"id": 3, "source": "S1", "ok": True, "msg": "x"})
    assert r.drain(1.0) == []


def test_unconfirmed_after_timeout():
    r = VerdictResolver(timeout_s=5.0)
    r.add(th(4), 0.0)
    assert r.drain(4.9) == []
    assert r.drain(5.0)[0]["verdict"] == "UNCONFIRMED"


def test_bad_last_cmd_ignored():
    r = VerdictResolver()
    r.observe(None)
    r.observe({"id": None, "source": None, "ok": False, "msg": "schema"})
    assert not r.results
