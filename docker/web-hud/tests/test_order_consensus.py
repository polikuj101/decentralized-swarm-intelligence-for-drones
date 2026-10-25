"""docker/web-hud/tests/test_order_consensus.py — выбор единого манифеста по большинству бортов."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import order_consensus as oc  # noqa: E402


def man(rtb=True, sectors=(0,), on_target="DWELL_ONLY", dwell=8, mid="m-1"):
    return {"manifest_id": mid, "active_sectors": list(sectors), "on_target": on_target, "dwell_s": dwell, "rtb_on_complete": rtb}


def res(drone, body=None, ok=True):
    return {"drone": drone, "ok": ok, "body": body}


def test_identical_manifests_have_no_dissent():
    m, dissent, missing = oc.choose([res(i, man(mid=f"m-{i}")) for i in range(1, 6)])
    assert m is not None and dissent == [] and missing == []


def test_majority_wins_and_dissenter_is_listed():
    rs = [res(1, man(rtb=False)), res(2, man(rtb=False)), res(3, man(rtb=True)), res(4, man(rtb=False)), res(5, man(rtb=False))]
    m, dissent, _ = oc.choose(rs)
    assert m["rtb_on_complete"] is False and dissent == [3]


def test_tie_goes_to_lowest_drone_number():
    rs = [res(1, man(rtb=True)), res(2, man(rtb=False)), res(3, man(rtb=True)), res(4, man(rtb=False))]
    m, dissent, _ = oc.choose(rs)
    assert m["rtb_on_complete"] is True and dissent == [2, 4]


def test_sector_order_and_manifest_id_do_not_count_as_difference():
    a, b = man(sectors=(1, 0), mid="m-1"), man(sectors=(0, 1), mid="m-2")
    assert oc.signature(a) == oc.signature(b)


def test_failed_boards_are_reported_missing_not_dissenting():
    rs = [res(1, man()), res(2, None, ok=False), res(3, man())]
    m, dissent, missing = oc.choose(rs)
    assert m is not None and dissent == [] and missing == [2]


def test_nobody_compiled():
    m, dissent, missing = oc.choose([res(1, None, ok=False), res(2, None, ok=False)])
    assert m is None and missing == [1, 2]
