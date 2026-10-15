"""swarm/actions.py — строго типизированные действия, фазы, фильтр phase×source×tool (Pydantic V2, Python 3.10)."""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class Phase(str, Enum):
    NOMINAL = "NOMINAL"
    DEGRADED_COMMS = "DEGRADED_COMMS"
    DEGRADED_NAV = "DEGRADED_NAV"
    ISOLATED = "ISOLATED"
    SWARM_LOSS = "SWARM_LOSS"
    CRITICAL = "CRITICAL"


SEVERITY = {Phase.NOMINAL: 0, Phase.DEGRADED_COMMS: 1, Phase.DEGRADED_NAV: 2,
            Phase.SWARM_LOSS: 3, Phase.ISOLATED: 4, Phase.CRITICAL: 5}
S2_TRIGGER_PHASES = frozenset({Phase.ISOLATED, Phase.SWARM_LOSS, Phase.CRITICAL})
MAX_AGE_S = {"S1": 2.0, "S2": 40.0}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NoArgs(_Strict):
    pass


class ScanTargetArgs(_Strict):
    target: int = Field(ge=0, le=3)


class DeclareFailoverArgs(_Strict):
    failed: int = Field(ge=1, le=8)
    reason: Literal["NO_HEARTBEAT", "GPS_LOST", "LINK_DEGRADED", "NAV_DRIFT"] = "NO_HEARTBEAT"


class HoldSectorArgs(_Strict):
    hold_s: int = Field(default=20, ge=5, le=120)


class EvadeJammingArgs(_Strict):
    hold_s: int = Field(default=30, ge=5, le=180)
    min_dist_m: float = Field(default=15.0, ge=5.0, le=60.0)


class SplitSectorArgs(_Strict):
    sector: int = Field(ge=0, le=5)
    parts: Literal[2, 3]


class NavigateToArgs(_Strict):
    x: float = Field(ge=-200.0, le=200.0)
    y: float = Field(ge=-200.0, le=200.0)


class ScanTarget(_Strict):
    tool: Literal["SCAN_TARGET"] = "SCAN_TARGET"
    args: ScanTargetArgs


class DeclareFailover(_Strict):
    tool: Literal["DECLARE_FAILOVER"] = "DECLARE_FAILOVER"
    args: DeclareFailoverArgs


class HoldSector(_Strict):
    tool: Literal["HOLD_SECTOR"] = "HOLD_SECTOR"
    args: HoldSectorArgs = HoldSectorArgs()


class EvadeJamming(_Strict):
    tool: Literal["EVADE_JAMMING"] = "EVADE_JAMMING"
    args: EvadeJammingArgs = EvadeJammingArgs()


class HoldPosition(_Strict):
    tool: Literal["HOLD_POSITION"] = "HOLD_POSITION"
    args: NoArgs = NoArgs()


class ReturnHome(_Strict):
    tool: Literal["RETURN_HOME"] = "RETURN_HOME"
    args: NoArgs = NoArgs()


class SplitSector(_Strict):
    tool: Literal["SPLIT_SECTOR"] = "SPLIT_SECTOR"
    args: SplitSectorArgs


class NavigateTo(_Strict):
    tool: Literal["NAVIGATE_TO"] = "NAVIGATE_TO"
    args: NavigateToArgs


Action = Annotated[
    Union[ScanTarget, DeclareFailover, HoldSector, EvadeJamming, HoldPosition, ReturnHome, SplitSector, NavigateTo],
    Field(discriminator="tool")]
ACTION: TypeAdapter = TypeAdapter(Action)

S1_TOOLS = frozenset({"SCAN_TARGET", "DECLARE_FAILOVER", "HOLD_SECTOR", "EVADE_JAMMING", "HOLD_POSITION", "RETURN_HOME"})
S2_TOOLS = frozenset({"SPLIT_SECTOR", "NAVIGATE_TO", "SCAN_TARGET", "DECLARE_FAILOVER", "HOLD_SECTOR", "RETURN_HOME"})
PHASE_TOOLS = {
    Phase.NOMINAL: frozenset({"SCAN_TARGET", "DECLARE_FAILOVER", "HOLD_SECTOR", "SPLIT_SECTOR", "NAVIGATE_TO"}),
    Phase.DEGRADED_COMMS: frozenset({"SCAN_TARGET", "DECLARE_FAILOVER", "HOLD_SECTOR", "EVADE_JAMMING"}),
    Phase.DEGRADED_NAV: frozenset({"DECLARE_FAILOVER", "HOLD_SECTOR", "HOLD_POSITION", "RETURN_HOME"}),
    Phase.ISOLATED: frozenset({"HOLD_SECTOR", "EVADE_JAMMING", "HOLD_POSITION", "RETURN_HOME"}),
    Phase.SWARM_LOSS: frozenset({"DECLARE_FAILOVER", "SCAN_TARGET", "HOLD_SECTOR", "SPLIT_SECTOR", "NAVIGATE_TO", "RETURN_HOME"}),
    Phase.CRITICAL: frozenset({"RETURN_HOME", "HOLD_POSITION"}),
}


def permitted(phase: Phase, source: str, tool: str) -> bool:
    return tool in PHASE_TOOLS[phase] and tool in (S1_TOOLS if source == "S1" else S2_TOOLS)


class Meta(_Strict):
    id: int = Field(ge=0)
    source: Literal["S1", "S2"]
    phase: Phase
    t_mono: float


class _RawCmd(_Strict):
    id: int = Field(ge=0)
    source: Literal["S1", "S2"]
    phase: Phase
    t_mono: float
    tool: str
    args: dict = Field(default_factory=dict)


def parse_cmd(d: dict) -> tuple[Meta, Action]:
    """dict из /swarm/local/planner_cmd -> (Meta, Action); ValidationError при любом нарушении схемы."""
    raw = _RawCmd.model_validate(d)
    meta = Meta(id=raw.id, source=raw.source, phase=raw.phase, t_mono=raw.t_mono)
    return meta, ACTION.validate_python({"tool": raw.tool, "args": raw.args})


def dump_cmd(meta: Meta, act) -> dict:
    return {**meta.model_dump(mode="json"), "tool": act.tool, "args": act.args.model_dump()}
