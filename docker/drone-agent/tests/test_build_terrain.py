"""tests/test_build_terrain.py — конвертер OSM -> подложка HUD (scripts/build_terrain.py), на синтетических данных."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1].parents[1] / "scripts"))

from build_terrain import Frame, build, clip_polygon, clip_polyline  # noqa: E402

BBOX = (48.440, 37.665, 48.512, 37.747)
AREA = {"x_min": 10.0, "x_max": 60.0, "y_min": -30.0, "y_max": 35.0}


def _way(tags, pts):
    return {"type": "way", "tags": tags, "geometry": [{"lat": la, "lon": lo} for la, lo in pts]}


def test_frame_keeps_shape_and_fits_the_scene_area():
    f = Frame(BBOX, AREA)
    x0, y0, x1, y1 = f.rect()
    assert AREA["x_min"] - 1e-6 <= x0 < x1 <= AREA["x_max"] + 1e-6 and AREA["y_min"] - 1e-6 <= y0 < y1 <= AREA["y_max"] + 1e-6
    assert abs((x1 - x0) / (y1 - y0) - f.real_size_m[0] / f.real_size_m[1]) < 1e-9          # соотношение сторон сохранено
    sx, sy = f.xy(BBOX[0], BBOX[1])                                                          # юго-запад рамки -> левый нижний угол карты
    assert abs(sx - x0) < 1e-6 and abs(sy - y0) < 1e-6
    cx, cy = f.xy((BBOX[0] + BBOX[2]) / 2, (BBOX[1] + BBOX[3]) / 2)
    assert abs(cx - 35.0) < 1e-6 and abs(cy - 2.5) < 1e-6                                    # центр рамки = центр области
    assert 100 < 1 / f.k < 150                                                               # район ~6x8 км на 50x65 м


def test_north_is_up_and_east_is_right():
    f = Frame(BBOX, AREA)
    x_a, y_a = f.xy(48.47, 37.70)
    x_b, y_b = f.xy(48.48, 37.71)
    assert y_b > y_a and x_b > x_a


def test_clip_polyline_cuts_at_the_border_and_splits_on_exit():
    r = (0, 0, 10, 10)
    assert clip_polyline([(-5, 5), (15, 5)], r) == [[(0.0, 5.0), (10.0, 5.0)]]
    assert clip_polyline([(-5, -5), (-1, 20)], r) == []
    parts = clip_polyline([(2, 2), (2, 12), (5, 12), (5, 3)], r)       # ушла за верх и вернулась
    assert len(parts) == 2


def test_clip_polygon_cuts_corner_and_drops_outside():
    r = (0, 0, 10, 10)
    sq = [(5, 5), (15, 5), (15, 15), (5, 15), (5, 5)]
    p = clip_polygon(sq, r)
    assert len(p) == 4 and max(x for x, _ in p) == 10 and max(y for _, y in p) == 10
    assert clip_polygon([(20, 20), (30, 20), (30, 30), (20, 20)], r) == []


def test_build_classifies_features_and_names_places():
    osm = {"elements": [
        _way({"highway": "primary"}, [(48.45, 37.67), (48.50, 37.74)]),
        _way({"highway": "track"}, [(48.46, 37.68), (48.47, 37.69)]),
        _way({"highway": "footway"}, [(48.46, 37.68), (48.47, 37.69)]),                  # не берём
        _way({"waterway": "river"}, [(48.44, 37.70), (48.51, 37.70)]),
        _way({"railway": "rail"}, [(48.45, 37.70), (48.46, 37.72)]),
        _way({"landuse": "forest"}, [(48.47, 37.68), (48.47, 37.69), (48.48, 37.69), (48.48, 37.68), (48.47, 37.68)]),
        _way({"natural": "water"}, [(48.46, 37.71), (48.46, 37.72), (48.47, 37.72)]),    # незамкнутое «озеро» -> линия
        {"type": "node", "lat": 48.47, "lon": 37.70, "tags": {"place": "village", "name": "Село", "name:en": "Selo"}},
        {"type": "node", "lat": 48.47, "lon": 37.71, "tags": {"place": "village"}},      # без имени — пропуск
        {"type": "node", "lat": 49.50, "lon": 37.70, "tags": {"place": "town", "name": "Далеко"}},   # вне рамки
    ]}
    t = build(osm, BBOX, AREA)
    kinds = sorted(l["kind"] for l in t["lines"])
    assert kinds == ["rail", "river", "river", "road_major", "track"]
    assert [p["kind"] for p in t["polys"]] == ["forest"]
    assert [p["name"] for p in t["places"]] == ["Село"] and t["places"][0]["name_en"] == "Selo"
    x0, y0, x1, y1 = Frame(BBOX, AREA).rect()
    for seq in [l["pts"] for l in t["lines"]] + [p["pts"] for p in t["polys"]]:
        assert all(x0 - 0.01 <= x <= x1 + 0.01 and y0 - 0.01 <= y <= y1 + 0.01 for x, y in seq)
    m = t["meta"]
    assert "OpenStreetMap" in m["source"] and "simulation" in m["note"] and m["real_m_per_scene_m"] > 100


def test_build_survives_empty_and_broken_elements():
    assert build({}, BBOX, AREA)["lines"] == []
    t = build({"elements": [{"type": "way", "tags": {"highway": "primary"}}, {"type": "way", "tags": {}, "geometry": []}]}, BBOX, AREA)
    assert t["lines"] == [] and t["polys"] == []
