"""tests/test_manifest_check.py — манифест из тома принимается только в допустимых границах."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from swarm.manifest_check import manifest_problem  # noqa: E402

GOOD = {"active_sectors": [0, 4], "on_target": "DWELL_RECON", "dwell_s": 8, "rtb_on_complete": True}


def bad(**kw):
    return manifest_problem({**GOOD, **kw}, 6)


def test_good_manifest_passes():
    assert manifest_problem(GOOD, 6) is None


def test_sector_out_of_range_rejected():
    assert bad(active_sectors=[0, 6]) and bad(active_sectors=[-1]) and bad(active_sectors=[])


def test_sector_wrong_types_rejected():
    assert bad(active_sectors="0") and bad(active_sectors=["0"]) and bad(active_sectors=[True])


def test_unknown_on_target_rejected():
    assert bad(on_target="ENGAGE") and bad(on_target=None)


def test_dwell_bounds():
    assert bad(dwell_s=2) and bad(dwell_s=16) and bad(dwell_s="8") and bad(dwell_s=None)
    assert bad(dwell_s=3) is None and bad(dwell_s=15) is None


def test_non_object_rejected():
    assert manifest_problem([], 6) and manifest_problem(None, 6)
