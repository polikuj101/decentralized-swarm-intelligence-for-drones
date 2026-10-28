"""swarm/candidates.py — типизированные кандидаты действий (после фильтра фазы и источника)."""
import math

from swarm import actions as A
from swarm.features import Obs, suspect_after

MOVERS = {"HOLD_SECTOR", "EVADE_JAMMING", "NAVIGATE_TO"}


def enumerate_candidates(st: dict, o: Obs, phase: A.Phase, source: str) -> list:
    x, y = st["pos"][0], st["pos"][1]
    out = []
    t_suspect = suspect_after(o.link_q)
    for k, p in sorted(st["peers"].items(), key=lambda kv: -kv[1]["silent_s"]):
        if not p["alive"] and not p["declared_failed"] and p["silent_s"] >= t_suspect:
            out.append(A.DeclareFailover(args=A.DeclareFailoverArgs(failed=int(k))))
    near = sorted((t for t in st["open"] if t["kind"] == "TARGET"), key=lambda t: math.hypot(t["cx"] - x, t["cy"] - y))
    for t in near[:2]:
        out.append(A.ScanTarget(args=A.ScanTargetArgs(target=t["id"] - 6)))
    if st.get("task") is not None or st["open"]:
        out.append(A.HoldSector())
    if phase in (A.Phase.DEGRADED_COMMS, A.Phase.ISOLATED):
        out.append(A.EvadeJamming())
    if phase in (A.Phase.DEGRADED_NAV, A.Phase.CRITICAL, A.Phase.ISOLATED):
        out.append(A.HoldPosition())
    if phase in (A.Phase.CRITICAL, A.Phase.ISOLATED, A.Phase.DEGRADED_NAV, A.Phase.SWARM_LOSS):
        out.append(A.ReturnHome())
    if source == "S2":
        big = [t for t in st["open"] if t["splittable"] and t["area"] > 400]
        if big and len(st["open"]) < len(st["alive"]):
            s = max(big, key=lambda t: t["area"])
            out.append(A.SplitSector(args=A.SplitSectorArgs(sector=s["id"], parts=2)))
        if st.get("task") is None and st["open"]:
            t = min(st["open"], key=lambda t: math.hypot(t["cx"] - x, t["cy"] - y))
            out.append(A.NavigateTo(args=A.NavigateToArgs(x=t["cx"], y=t["cy"])))
    if o.override_active:
        out = [a for a in out if a.tool not in MOVERS]
    if o.withdrawn:
        out = [a for a in out if a.tool == "DECLARE_FAILOVER"]
    return [a for a in out if A.permitted(phase, source, a.tool)]
