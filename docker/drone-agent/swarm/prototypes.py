"""swarm/prototypes.py — эталонные ситуации (канонические бакеты), в которых данное действие уместно."""


def _p(phase, link, gps, sigma, silent, lost, task):
    return dict(phase=phase, link=link, gps=gps, sigma=sigma, silent=silent, lost=lost, task=task)


PROTOS = {
    "DECLARE_FAILOVER": [
        _p("nominal", "good", "ok", "low", "long", "some", "sector"),
        _p("degraded_comms", "fair", "ok", "low", "long", "some", "sector"),
        _p("swarm_loss", "fair", "ok", "mid", "long", "half", "target"),
        _p("swarm_loss", "poor", "lost", "mid", "long", "most", "sector"),
    ],
    "EVADE_JAMMING": [
        _p("degraded_comms", "poor", "ok", "low", "short", "none", "sector"),
        _p("degraded_comms", "poor", "ok", "low", "long", "some", "target"),
        _p("isolated", "poor", "ok", "mid", "long", "most", "sector"),
    ],
    "HOLD_SECTOR": [
        _p("degraded_nav", "good", "lost", "mid", "none", "none", "sector"),
        _p("degraded_nav", "fair", "suspect", "mid", "none", "none", "sector"),
        _p("isolated", "poor", "ok", "low", "long", "most", "sector"),
    ],
    "HOLD_POSITION": [
        _p("degraded_nav", "fair", "lost", "high", "none", "none", "sector"),
        _p("critical", "poor", "lost", "critical", "long", "most", "idle"),
    ],
    "RETURN_HOME": [
        _p("critical", "poor", "lost", "critical", "long", "most", "sector"),
        _p("isolated", "poor", "lost", "high", "long", "most", "sector"),
    ],
    "SCAN_TARGET": [
        _p("nominal", "good", "ok", "low", "none", "none", "idle"),
        _p("degraded_comms", "fair", "ok", "low", "none", "none", "idle"),
        _p("swarm_loss", "fair", "ok", "low", "none", "half", "idle"),
    ],
}
