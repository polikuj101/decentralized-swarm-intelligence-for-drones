#!/usr/bin/env python3
"""scripts/build_synthetic_terrain.py — вымышленная подложка карты для HUD, без привязки к реальному району.

Реальная подложка (scripts/build_terrain.py, юг Константиновки) на записи для внешних собеседников читается как
планирование над реальным районом боёв. Эта генерирует дороги, реку, лес и два посёлка с условными именами
в том же формате terrain.json; meta.fictional = true, HUD подписывает карту «вымышленная местность».
Детерминирована по --seed.
    python scripts/build_synthetic_terrain.py            # перезаписывает docker/web-hud/static/terrain.json
"""
import argparse
import json
import math
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _clamp(pts, a):
    return [[round(min(max(x, a["x_min"]), a["x_max"]), 2), round(min(max(y, a["y_min"]), a["y_max"]), 2)] for x, y in pts]


def _wavy(p, q, amp, waves, n, phase):
    """Линия p -> q с синусоидальным отклонением поперёк (дорога, река)."""
    (x0, y0), (x1, y1) = p, q
    dx, dy = x1 - x0, y1 - y0
    ln = math.hypot(dx, dy)
    nx, ny = -dy / ln, dx / ln
    out = []
    for i in range(n + 1):
        t = i / n
        o = amp * math.sin(2 * math.pi * waves * t + phase)
        out.append((x0 + dx * t + nx * o, y0 + dy * t + ny * o))
    return out


def _blob(rng, cx, cy, r, n=18):
    """Неровный замкнутый контур вокруг центра (лес, пруд)."""
    return [(cx + r * (0.7 + 0.3 * rng.random()) * math.cos(2 * math.pi * k / n),
             cy + r * (0.7 + 0.3 * rng.random()) * math.sin(2 * math.pi * k / n)) for k in range(n)]


def build(area, seed=7):
    rng = random.Random(seed)
    a = area
    w, h = a["x_max"] - a["x_min"], a["y_max"] - a["y_min"]
    at = lambda fx, fy: (a["x_min"] + fx * w, a["y_min"] + fy * h)          # noqa: E731  доли области -> метры сцены
    lines = [
        {"kind": "road_major", "pts": _wavy(at(-0.05, -0.02), at(1.05, 0.95), 1.2, 1.5, 40, 0.3)},
        {"kind": "road", "pts": _wavy(at(-0.05, 0.55), at(1.05, 0.48), 0.8, 2, 30, 1.1)},
        {"kind": "road", "pts": _wavy(at(0.22, 1.05), at(0.30, 0.55), 0.6, 1, 15, 0.0)},
        {"kind": "rail", "pts": [at(-0.05, 0.20), at(1.05, 0.33)]},
        {"kind": "river", "pts": _wavy(at(-0.05, 0.88), at(1.05, 0.12), 3.0, 2.5, 80, 0.8)},
    ]
    for k in range(6):                                                       # полевые дороги
        fx, fy = 0.1 + 0.8 * rng.random(), 0.1 + 0.8 * rng.random()
        ang, ln = rng.uniform(0, math.pi), rng.uniform(0.15, 0.3) * w
        p = (at(fx, fy)[0] - ln / 2 * math.cos(ang), at(fx, fy)[1] - ln / 2 * math.sin(ang))
        q = (p[0] + ln * math.cos(ang), p[1] + ln * math.sin(ang))
        lines.append({"kind": "track", "pts": _wavy(p, q, 0.5, 1, 10, k)})
    polys = [{"kind": "forest", "pts": _blob(rng, *at(fx, fy), r * w)}
             for fx, fy, r in ((0.15, 0.15, 0.10), (0.80, 0.70, 0.12), (0.62, 0.22, 0.08), (0.12, 0.72, 0.07), (0.45, 0.85, 0.06))]
    polys.append({"kind": "water", "pts": _blob(rng, *at(0.70, 0.40), 0.05 * w)})
    for x in lines + polys:
        x["pts"] = _clamp(x["pts"], a)
    places = [{"kind": "village", "name": n, "name_en": n, "x": round(at(fx, fy)[0], 2), "y": round(at(fx, fy)[1], 2)}
              for n, fx, fy in (("Village A", 0.06, 0.78), ("Village B", 0.55, 0.60))]
    return {"meta": {"source": "synthetic", "fictional": True, "seed": seed, "area_m": a,
                     "note": "simulation, fictional terrain, not a real place"},
            "lines": lines, "polys": polys, "places": places}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "docker/web-hud/static/terrain.json"))
    ap.add_argument("--mission", default=str(ROOT / "docker/drone-agent/config/mission.json"))
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    with open(a.mission, encoding="utf-8") as fh:
        area = json.load(fh)["search_area"]
    t = build(area, a.seed)
    with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(t, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"{a.out}: линий {len(t['lines'])}, полигонов {len(t['polys'])}, мест {len(t['places'])}, вымышленная местность (seed {a.seed})")


if __name__ == "__main__":
    main()
