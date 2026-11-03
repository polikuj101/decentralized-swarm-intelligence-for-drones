"""tests/test_build_synthetic_terrain.py — вымышленная подложка HUD (scripts/build_synthetic_terrain.py): формат terrain.json, границы, детерминизм."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1].parents[1] / "scripts"))

from build_synthetic_terrain import build  # noqa: E402

AREA = {"x_min": 10.0, "x_max": 60.0, "y_min": -30.0, "y_max": 35.0}
LINE_KINDS = {"road_major", "road", "track", "rail", "river"}          # те, что рисует app.js (TERR)
AREA_KINDS = {"forest", "water"}                                       # TERR_AREA


def test_same_seed_same_map_and_kinds_the_hud_can_draw():
    t = build(AREA, seed=7)
    assert t == build(AREA, seed=7) and t != build(AREA, seed=8)
    assert {x["kind"] for x in t["lines"]} <= LINE_KINDS and {x["kind"] for x in t["polys"]} <= AREA_KINDS
    assert all(len(x["pts"]) >= 2 for x in t["lines"]) and all(len(x["pts"]) >= 3 for x in t["polys"])


def test_everything_stays_inside_the_scene_area():
    t = build(AREA)
    pts = [p for x in t["lines"] + t["polys"] for p in x["pts"]] + [[p["x"], p["y"]] for p in t["places"]]
    assert all(AREA["x_min"] <= x <= AREA["x_max"] and AREA["y_min"] <= y <= AREA["y_max"] for x, y in pts)


def test_marked_fictional_with_no_real_geography():
    t = build(AREA)
    assert t["meta"]["fictional"] is True and "bbox_sw_ne" not in t["meta"]          # HUD по этому флагу пишет «вымышленная местность»
    assert [p["name"] for p in t["places"]] == ["Village A", "Village B"]
