#!/usr/bin/env python3
"""scripts/build_terrain.py — подложка карты для HUD из открытых данных OpenStreetMap (ответ Overpass, `out geom`).

Реальный район (км) масштабируется равномерно в область поиска сцены (метры, mission.json), форма сохраняется.
Выход: docker/web-hud/static/terrain.json — линии и полигоны в метрах сцены, подписи населённых пунктов, мета с масштабом.
Это иллюстративный масштаб: симуляция идёт на площадке 50x65 м, карта — только фон для показа.
    python scripts/build_terrain.py --raw docs/map/overpass_raw.json
"""
import argparse
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BBOX = (48.440, 37.665, 48.512, 37.747)      # юг, запад, север, восток (градусы); тот же, что в запросе к Overpass
M_PER_DEG_LAT = 111_200.0
ROAD_MAJOR = {"motorway", "trunk", "primary"}
ROAD = {"secondary", "tertiary", "unclassified"}


class Frame:
    """Градусы -> метры сцены: равнопромежуточная проекция вокруг центра рамки, равномерный масштаб, рамка по центру области."""

    def __init__(self, bbox, area):
        s, w, n, e = bbox
        self.lat0, self.lon0 = (s + n) / 2, (w + e) / 2
        self.m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(self.lat0))
        real_w, real_h = (e - w) * self.m_per_deg_lon, (n - s) * M_PER_DEG_LAT
        self.area = area
        self.k = min((area["x_max"] - area["x_min"]) / real_w, (area["y_max"] - area["y_min"]) / real_h)   # метров сцены на метр местности
        self.cx, self.cy = (area["x_min"] + area["x_max"]) / 2, (area["y_min"] + area["y_max"]) / 2
        self.real_size_m = (real_w, real_h)

    def xy(self, lat, lon):
        return ((self.cx + (lon - self.lon0) * self.m_per_deg_lon * self.k),
                (self.cy + (lat - self.lat0) * M_PER_DEG_LAT * self.k))

    def rect(self):
        hw, hh =self.real_size_m[0] * self.k / 2, self.real_size_m[1] * self.k / 2
        return (self.cx - hw, self.cy - hh, self.cx + hw, self.cy + hh)     # занятая карта: рамка может быть уже области


def clip_segment(p, q, r):
    """Liang–Barsky: часть отрезка pq внутри прямоугольника r=(x0,y0,x1,y1) или None."""
    (x0, y0), (x1, y1) = p, q
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for pp, qq in ((-dx, x0 - r[0]), (dx, r[2] - x0), (-dy, y0 - r[1]), (dy, r[3] - y0)):
        if pp == 0:
            if qq < 0:
                return None
        else:
            t = qq / pp
            if pp < 0:
                if t > t1:
                    return None
                t0 = max(t0, t)
            else:
                if t < t0:
                    return None
                t1 = min(t1, t)
    return (x0 + t0 * dx, y0 + t0 * dy), (x0 + t1 * dx, y0 + t1 * dy)


def clip_polyline(pts, r):
    out, cur = [], []
    for a, b in zip(pts, pts[1:]):
        seg = clip_segment(a, b, r)
        if seg is None:
            if len(cur) > 1:
                out.append(cur)
            cur = []
            continue
        if cur and cur[-1] == seg[0]:
            cur.append(seg[1])
        else:
            if len(cur) > 1:
                out.append(cur)
            cur = [seg[0], seg[1]]
    if len(cur) > 1:
        out.append(cur)
    return out


def clip_polygon(pts, r):
    """Sutherland–Hodgman по четырём сторонам прямоугольника."""
    def inside(p, edge):
        i, side = edge
        return p[i] >= side if edge in ((0, r[0]), (1, r[1])) else p[i] <= side

    def cross(a, b, edge):
        i, side = edge
        t = (side - a[i]) / (b[i] - a[i])
        return (side, a[1] + t * (b[1] - a[1])) if i == 0 else (a[0] + t * (b[0] - a[0]), side)

    poly = pts[:-1] if len(pts) > 1 and pts[0] == pts[-1] else list(pts)
    for edge in ((0, r[0]), (0, r[2]), (1, r[1]), (1, r[3])):
        src, poly = poly, []
        for a, b in zip(src, src[1:] + src[:1]):
            if inside(b, edge):
                if not inside(a, edge):
                    poly.append(cross(a, b, edge))
                poly.append(b)
            elif inside(a, edge):
                poly.append(cross(a, b, edge))
        if not poly:
            return []
    return poly if len(poly) >= 3 else []


def _round(pts):
    out = []
    for x, y in pts:
        q = (round(x, 2), round(y, 2))
        if not out or out[-1] != q:
            out.append(q)
    return [list(p) for p in out]


def classify(tags):
    hw = tags.get("highway")
    if hw in ROAD_MAJOR:
        return "road_major", "line"
    if hw in ROAD:
        return "road", "line"
    if hw == "track":
        return "track", "line"
    if tags.get("railway") == "rail":
        return "rail", "line"
    if tags.get("waterway") in ("river", "stream", "canal"):
        return "river", "line"
    if tags.get("natural") == "water":
        return "water", "area"
    if tags.get("landuse") == "forest" or tags.get("natural") == "wood":
        return "forest", "area"
    return None, None


def build(osm, bbox, area):
    f = Frame(bbox, area)
    rect = f.rect()
    lines, polys, places = [], [], []
    for el in osm.get("elements", []):
        tags = el.get("tags") or {}
        if el.get("type") == "node" and tags.get("place") in ("city", "town", "village", "hamlet"):
            x, y = f.xy(el["lat"], el["lon"])
            if rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3] and (tags.get("name") or tags.get("name:uk")):
                places.append({"kind": tags["place"], "name": tags.get("name:uk") or tags["name"], "name_en": tags.get("name:en"),
                               "x": round(x, 2), "y": round(y, 2)})
            continue
        geom = el.get("geometry")
        kind, shape = classify(tags)
        if el.get("type") != "way" or not geom or kind is None:
            continue
        pts = [f.xy(g["lat"], g["lon"]) for g in geom]
        closed = len(pts) > 3 and pts[0] == pts[-1]
        if shape == "area" and closed:
            p = clip_polygon(pts, rect)
            if p:
                polys.append({"kind": kind, "pts": _round(p)})
        else:                                      # незамкнутые реки и «кривые» озёра рисуем как линии
            for seg in clip_polyline(pts, rect):
                if len(_round(seg)) > 1:
                    lines.append({"kind": kind if shape == "line" else "river", "pts": _round(seg)})
    return {
        "meta": {"source": "© OpenStreetMap contributors (ODbL)", "bbox_sw_ne": list(bbox), "area_m": area,
                 "real_m_per_scene_m": round(1 / f.k, 1), "real_size_km": [round(v / 1000, 2) for v in f.real_size_m],
                 "note": "simulation, scaled, illustrative"},
        "lines": lines, "polys": polys, "places": places,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=str(ROOT / "docs/map/overpass_raw.json"))
    ap.add_argument("--out", default=str(ROOT / "docker/web-hud/static/terrain.json"))
    ap.add_argument("--mission", default=str(ROOT / "docker/drone-agent/config/mission.json"))
    ap.add_argument("--bbox", type=float, nargs=4, metavar=("S", "W", "N", "E"), default=DEFAULT_BBOX)
    a = ap.parse_args()
    with open(a.raw, encoding="utf-8") as fh:
        osm = json.load(fh)
    with open(a.mission, encoding="utf-8") as fh:
        area = json.load(fh)["search_area"]
    t = build(osm, tuple(a.bbox), area)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(t, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"{a.out}: линий {len(t['lines'])}, полигонов {len(t['polys'])}, мест {len(t['places'])}, масштаб 1 м сцены = {t['meta']['real_m_per_scene_m']} м")


if __name__ == "__main__":
    main()
