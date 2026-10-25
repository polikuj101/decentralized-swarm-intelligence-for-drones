"""tests/test_orders.py — поля приказа on_target / dwell_s / rtb_on_complete действительно меняют поведение."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from swarm.orders import manifest_signature, needs_rebuild, order_params, target_dwell_s, task_enabled  # noqa: E402


def m(**kw):
    base = {"on_target": "DWELL_RECON", "dwell_s": 8, "rtb_on_complete": True}
    base.update(kw)
    return base


def test_no_manifest_keeps_previous_behaviour():
    p = order_params(None)
    assert p == {"skip_targets": False, "capture": True, "dwell_s": 5.0, "rtb": True, "on_target": "DWELL_RECON"}


def test_skip_removes_targets_but_not_sectors():
    p = order_params(m(on_target="SKIP"))
    assert not task_enabled("TARGET", p)
    assert task_enabled("SECTOR", p)
    assert not p["capture"]


def test_dwell_only_hovers_without_capture_and_recon_captures():
    only, recon = order_params(m(on_target="DWELL_ONLY")), order_params(m(on_target="DWELL_RECON"))
    assert task_enabled("TARGET", only) and not only["capture"]
    assert task_enabled("TARGET", recon) and recon["capture"]


def test_dwell_comes_from_order_for_targets_only():
    p = order_params(m(dwell_s=12))
    assert target_dwell_s("TARGET", p) == 12.0
    assert target_dwell_s("SECTOR", p) == 0.0


def test_dwell_falls_back_when_missing_or_not_a_number():
    assert order_params(m(dwell_s=None))["dwell_s"] == 5.0
    assert order_params(m(dwell_s="8"))["dwell_s"] == 5.0
    assert order_params(m(dwell_s=True))["dwell_s"] == 5.0


def test_rtb_flag():
    assert order_params(m(rtb_on_complete=False))["rtb"] is False
    assert order_params(m(rtb_on_complete=True))["rtb"] is True


def test_only_skip_change_rebuilds_the_auction():
    base = order_params(m())
    assert not needs_rebuild(base, order_params(m(dwell_s=3, rtb_on_complete=False)))
    assert not needs_rebuild(base, order_params(m(on_target="DWELL_ONLY")))
    assert needs_rebuild(base, order_params(m(on_target="SKIP")))


def test_same_id_with_different_content_is_a_different_manifest():
    """Регрессия (ревью): выровненный манифест несёт id большинства; борт с тем же id, но другим rtb, обязан его принять."""
    a, b = m(rtb_on_complete=True), m(rtb_on_complete=False)
    a["manifest_id"] = b["manifest_id"] = "m-1"
    assert manifest_signature(a) != manifest_signature(b)


def test_sector_order_is_not_a_difference():
    assert manifest_signature(m(active_sectors=[1, 0])) == manifest_signature(m(active_sectors=[0, 1]))


def test_signature_of_missing_manifest_is_stable():
    assert manifest_signature(None) == manifest_signature({})
