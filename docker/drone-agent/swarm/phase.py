"""swarm/phase.py — классификатор фаз с гистерезисом."""
from swarm.actions import SEVERITY, Phase
from swarm.features import Obs

GRACE_S, HOLD_DOWN_S, ISOLATED_S = 30.0, 5.0, 12.0


class PhaseTracker:
    def __init__(self):
        self.phase, self._cand, self._since = Phase.NOMINAL, None, 0.0

    @staticmethod
    def raw(o: Obs) -> Phase:
        if o.sigma > 40.0:
            return Phase.CRITICAL
        warm = o.n_total > 1 and o.uptime_s >= GRACE_S
        if warm and o.min_silent_s >= ISOLATED_S:
            return Phase.ISOLATED
        if warm and o.n_alive * 2 <= o.n_total:
            return Phase.SWARM_LOSS
        if o.gps_fix < 3 or o.gps_suspect:
            return Phase.DEGRADED_NAV
        if warm and o.link_q < 0.4:
            return Phase.DEGRADED_COMMS
        return Phase.NOMINAL

    def update(self, o: Obs, now: float) -> Phase:
        r = self.raw(o)
        if SEVERITY[r] >= SEVERITY[self.phase]:
            self.phase, self._cand = r, None
        elif self._cand != r:
            self._cand, self._since = r, now
        elif now - self._since >= HOLD_DOWN_S:
            self.phase, self._cand = r, None
        return self.phase
