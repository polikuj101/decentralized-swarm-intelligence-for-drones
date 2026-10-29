"""swarm/rules.py — правила System 1: условие -> действие -> срочность [0..1]. У каждого правила номер, он идёт в лог решения и в карточку на HUD.

Простые локальные правила, без LLM: каждый борт применяет их к собственному состоянию и состоянию, услышанному от соседей.
Для одного инструмента срабатывает правило с максимальной срочностью; 0 = не применимо (жёсткий gate).
"""
from collections import namedtuple

from swarm.actions import Phase

Rule = namedtuple("Rule", "id tool score when cond")

RULES = (
    Rule("R1", "DECLARE_FAILOVER", 0.95, "сосед молчит дольше порога (suspect_after) и не слышен ни напрямую, ни через gossip",
         lambda o, ph: o.n_suspect > 0),
    Rule("R2", "RETURN_HOME", 0.95, "фаза CRITICAL (sigma > 40 м): позиция потеряна",
         lambda o, ph: ph == Phase.CRITICAL),
    Rule("R3", "RETURN_HOME", 0.6, "фаза ISOLATED и sigma > 20 м: связи нет и позиция плывёт",
         lambda o, ph: ph == Phase.ISOLATED and o.sigma > 20),
    Rule("R4", "HOLD_POSITION", 0.8, "фаза CRITICAL: зависнуть, пока не вернётся навигация",
         lambda o, ph: ph == Phase.CRITICAL),
    Rule("R5", "HOLD_POSITION", 0.7, "фаза DEGRADED_NAV и sigma > 20 м: навигация не заслуживает доверия",
         lambda o, ph: ph == Phase.DEGRADED_NAV and o.sigma > 20),
    Rule("R6", "EVADE_JAMMING", 0.75, "фаза DEGRADED_COMMS или ISOLATED: уйти в точку, где связь была хорошей",
         lambda o, ph: ph in (Phase.DEGRADED_COMMS, Phase.ISOLATED)),
    Rule("R7", "HOLD_SECTOR", 0.65, "фаза DEGRADED_NAV и sigma > 10 м: остаться в своём секторе",
         lambda o, ph: ph == Phase.DEGRADED_NAV and o.sigma > 10),
    Rule("R8", "HOLD_SECTOR", 0.55, "фаза ISOLATED: остаться в своём секторе",
         lambda o, ph: ph == Phase.ISOLATED),
    Rule("R9", "SCAN_TARGET", 0.55, "борт свободен и есть открытая цель",
         lambda o, ph: o.idle and o.n_open_targets > 0),
)


def fired(tool: str, o, phase: Phase):
    """(срочность, номер правила) для инструмента; (0.0, '') если ни одно не подошло."""
    best = (0.0, "")
    for r in RULES:
        if r.tool == tool and r.score > best[0] and r.cond(o, phase):
            best = (r.score, r.id)
    return best
