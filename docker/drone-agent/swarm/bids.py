"""swarm/bids.py — ставка борта на задачу (чистая функция; coordinator.bid_for подставляет в неё состояние борта)."""
import math

BID_SCALE = 2700
BID_CAP = 1.5          # ставка <= 4050 < 4095 (предел 12 бит в wire.enc_claim)


def compute_bid(kind, dist, dx, dy, vx=0.0, vy=0.0, degraded=False, executing=False, preferred=False) -> int:
    """kind: "TARGET"|"SECTOR"; (dx, dy) — вектор от борта к задаче, dist — его длина; (vx, vy) — скорость борта."""
    s = (1.0 if kind == "TARGET" else 0.8) / (1.0 + dist / 25.0)
    vmag = math.hypot(vx, vy)
    if vmag > 0.5 and dist > 2.0:                       # выравнивание по скорости: убирает слепые зоны на границах секторов
        s *= 1.0 + 0.25 * (vx * dx + vy * dy) / (vmag * dist)
    if degraded:
        s *= 0.6
    if executing:
        s *= 1.4
    if preferred:
        s *= 1.5
    return max(1, int(round(min(BID_CAP, s) * BID_SCALE)))
