"""tests/test_actions.py — тесты схемы действий, фаз, валидации и фильтра."""
import pytest
from pydantic import ValidationError

from swarm import actions as A


def test_phase_tools_covers_all_phases():
    for phase in A.Phase:
        assert phase in A.PHASE_TOOLS
        assert len(A.PHASE_TOOLS[phase]) > 0


def test_action_classes():
    scan = A.ScanTarget(args=A.ScanTargetArgs(target=2))
    assert scan.tool == "SCAN_TARGET"
    assert scan.args.target == 2

    fo = A.DeclareFailover(args=A.DeclareFailoverArgs(failed=3, reason="GPS_LOST"))
    assert fo.tool == "DECLARE_FAILOVER"
    assert fo.args.failed == 3
    assert fo.args.reason == "GPS_LOST"

    hs = A.HoldSector(args=A.HoldSectorArgs(hold_s=45))
    assert hs.tool == "HOLD_SECTOR"
    assert hs.args.hold_s == 45

    ej = A.EvadeJamming(args=A.EvadeJammingArgs(hold_s=30, min_dist_m=20.0))
    assert ej.tool == "EVADE_JAMMING"
    assert ej.args.min_dist_m == 20.0

    hp = A.HoldPosition()
    assert hp.tool == "HOLD_POSITION"
    assert isinstance(hp.args, A.NoArgs)

    rh = A.ReturnHome()
    assert rh.tool == "RETURN_HOME"
    assert isinstance(rh.args, A.NoArgs)

    ss = A.SplitSector(args=A.SplitSectorArgs(sector=2, parts=2))
    assert ss.tool == "SPLIT_SECTOR"
    assert ss.args.parts == 2

    nv = A.NavigateTo(args=A.NavigateToArgs(x=10.0, y=-25.0))
    assert nv.tool == "NAVIGATE_TO"
    assert nv.args.x == 10.0


def test_parse_cmd_valid():
    d = {
        "id": 1,
        "source": "S1",
        "phase": "NOMINAL",
        "t_mono": 123.456,
        "tool": "SCAN_TARGET",
        "args": {"target": 1},
    }
    meta, act = A.parse_cmd(d)
    assert meta.id == 1
    assert meta.source == "S1"
    assert meta.phase == A.Phase.NOMINAL
    assert meta.t_mono == 123.456
    assert act.tool == "SCAN_TARGET"
    assert act.args.target == 1


def test_parse_cmd_without_args_for_no_arg_tools():
    # tool без поля args у инструментов без обязательных аргументов допустим
    d = {
        "id": 2,
        "source": "S1",
        "phase": "CRITICAL",
        "t_mono": 200.0,
        "tool": "RETURN_HOME",
    }
    meta, act = A.parse_cmd(d)
    assert act.tool == "RETURN_HOME"
    assert isinstance(act.args, A.NoArgs)

    d2 = {
        "id": 3,
        "source": "S1",
        "phase": "CRITICAL",
        "t_mono": 201.0,
        "tool": "HOLD_POSITION",
    }
    meta2, act2 = A.parse_cmd(d2)
    assert act2.tool == "HOLD_POSITION"

    d3 = {
        "id": 4,
        "source": "S1",
        "phase": "NOMINAL",
        "t_mono": 202.0,
        "tool": "HOLD_SECTOR",
    }
    meta3, act3 = A.parse_cmd(d3)
    assert act3.tool == "HOLD_SECTOR"
    assert act3.args.hold_s == 20


def test_parse_cmd_unknown_tool_raises_validation_error():
    d = {
        "id": 5,
        "source": "S1",
        "phase": "NOMINAL",
        "t_mono": 200.0,
        "tool": "FIRE_LASER",
        "args": {},
    }
    with pytest.raises(ValidationError):
        A.parse_cmd(d)


def test_parse_cmd_extra_top_level_fields_raises_validation_error():
    d = {
        "id": 6,
        "source": "S1",
        "phase": "NOMINAL",
        "t_mono": 200.0,
        "tool": "HOLD_POSITION",
        "args": {},
        "extra_field": "forbidden",
    }
    with pytest.raises(ValidationError):
        A.parse_cmd(d)


def test_parse_cmd_extra_args_fields_raises_validation_error():
    d = {
        "id": 7,
        "source": "S1",
        "phase": "NOMINAL",
        "t_mono": 200.0,
        "tool": "SCAN_TARGET",
        "args": {"target": 1, "extra_arg": 999},
    }
    with pytest.raises(ValidationError):
        A.parse_cmd(d)


def test_permitted_filter():
    assert A.permitted(A.Phase.NOMINAL, "S1", "SCAN_TARGET") is True
    assert A.permitted(A.Phase.NOMINAL, "S1", "SPLIT_SECTOR") is False  # S1 never does SPLIT_SECTOR
    assert A.permitted(A.Phase.NOMINAL, "S2", "SPLIT_SECTOR") is True
    assert A.permitted(A.Phase.CRITICAL, "S1", "SCAN_TARGET") is False
    assert A.permitted(A.Phase.CRITICAL, "S1", "RETURN_HOME") is True
    assert A.permitted(A.Phase.CRITICAL, "S2", "RETURN_HOME") is True
    assert A.permitted(A.Phase.CRITICAL, "S2", "SPLIT_SECTOR") is False  # Not in CRITICAL phase


def test_dump_cmd():
    meta = A.Meta(id=10, source="S1", phase=A.Phase.NOMINAL, t_mono=50.0)
    act = A.ScanTarget(args=A.ScanTargetArgs(target=3))
    d = A.dump_cmd(meta, act)
    assert d == {
        "id": 10,
        "source": "S1",
        "phase": "NOMINAL",
        "t_mono": 50.0,
        "tool": "SCAN_TARGET",
        "args": {"target": 3},
    }
