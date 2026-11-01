"""tests/test_order_assignments.py — приказ с адресацией бортов: «D1 и D2 — Дельта и Фокстрот, остальные — Альфа»."""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parents[1] / "docker" / "web-hud"))
os.environ.setdefault("MISSION_FILE", str(ROOT / "config" / "mission.json"))
if "SCENE_FILE" not in os.environ:
    _scene = Path(tempfile.mkdtemp()) / "scene.json"
    _scene.write_text(json.dumps({"drones": [{"id": i, "x_m": 0.0, "y_m": 5.0 * i} for i in range(1, 6)], "targets": [], "obstacles": []}))
    os.environ["SCENE_FILE"] = str(_scene)

import order_consensus  # noqa: E402
from swarm import order_rules, tasking  # noqa: E402
from swarm.manifest_check import manifest_problem  # noqa: E402
from swarm.orders import manifest_signature, needs_rebuild, order_params, sector_allowed  # noqa: E402

RU = "D1 и D2 отрабатывают Дельту и Фокстрот, пока остальные отсиживаются в Альфе"
EN = "Drones 1 and 2 search delta and foxtrot, the rest stay in alpha"
EXPECTED = {1: ["delta", "foxtrot"], 2: ["delta", "foxtrot"], 3: ["alpha"], 4: ["alpha"], 5: ["alpha"]}


@pytest.mark.parametrize("prompt", [RU, EN])
def test_named_drones_and_the_rest(prompt):
    assert order_rules.assignments(prompt, 5) == EXPECTED


@pytest.mark.parametrize("prompt,expected", [
    ("Борт 3 в Альфу", {3: ["alpha"]}),
    ("Дроны 1, 2 и 4 осмотрят Чарли", {1: ["charlie"], 2: ["charlie"], 4: ["charlie"]}),
    ("Борт 1 — Браво; борт 2 — Эхо", {1: ["bravo"], 2: ["echo"]}),
    ("drone 5 takes foxtrot", {5: ["foxtrot"]}),
])
def test_only_named_drones_are_pinned(prompt, expected):
    assert order_rules.assignments(prompt, 5) == expected


@pytest.mark.parametrize("prompt", [
    "Разведать квадрат Браво",
    "Облететь Альфу и Дельту, цели не трогать",
    "Задержись на 8 секунд над Фокстротом",         # «8 секунд» — не номер борта
    "Осмотреть все квадраты, остальные не трогать",  # «остальные» без зон ни к кому не привязывает
])
def test_orders_without_addressing_are_unchanged(prompt):
    assert order_rules.assignments(prompt, 5) == {}


def test_drone_outside_the_swarm_is_refused():
    with pytest.raises(ValueError, match="борт 7"):
        order_rules.assignments("Борт 7 в Альфу", 5)


def test_compile_order_writes_sectors_per_drone(monkeypatch):
    monkeypatch.setattr(tasking, "_ask_llm", lambda p, t: ({}, None))
    m = tasking.compile_order(RU)
    assert m["active_sectors"] == [0, 2, 5]
    assert m["assignments"] == {"1": [2, 5], "2": [2, 5], "3": [0], "4": [0], "5": [0]}
    assert m["parsed_by"]["assignments"] == "rules"
    assert manifest_problem(m, 6) is None


def test_compile_order_without_addressing_has_no_assignments(monkeypatch):
    monkeypatch.setattr(tasking, "_ask_llm", lambda p, t: ({}, None))
    assert "assignments" not in tasking.compile_order("Разведать квадрат Браво")


def test_compile_order_refuses_unknown_drone(monkeypatch):
    monkeypatch.setattr(tasking, "_ask_llm", lambda p, t: ({}, None))
    with pytest.raises(tasking.TaskingError, match="борт 9"):
        tasking.compile_order("Борт 9 в Альфу")


def _manifest(**kw):
    base = {"active_sectors": [0, 2, 5], "on_target": "DWELL_RECON", "dwell_s": 8, "rtb_on_complete": True,
            "assignments": {"1": [2, 5], "3": [0]}}
    base.update(kw)
    return base


def test_manifest_check_accepts_and_rejects_assignments():
    assert manifest_problem(_manifest(), 6) is None
    assert manifest_problem(_manifest(assignments={}), 6)
    assert manifest_problem(_manifest(assignments={"x": [0]}), 6)
    assert manifest_problem(_manifest(assignments={"1": []}), 6)
    assert manifest_problem(_manifest(assignments={"1": [4]}), 6)         # сектор вне active_sectors
    assert manifest_problem(_manifest(assignments={"1": [True]}), 6)
    assert manifest_problem(_manifest(assignments=[1]), 6)


def test_each_drone_sees_only_its_own_sectors():
    m = _manifest()
    p1, p2, p3 = (order_params(m, drone_id=i) for i in (1, 2, 3))
    assert p1["allowed"] == [2, 5] and p3["allowed"] == [0]
    assert p2["allowed"] is None                       # борт без адресации торгуется за любой активный сектор
    assert sector_allowed(p1, 2) and not sector_allowed(p1, 0)
    assert sector_allowed(p3, 0) and not sector_allowed(p3, 5)
    assert sector_allowed(p2, 0) and sector_allowed(p2, 5)
    assert order_params(m)["allowed"] is None          # номер борта не задан (старые вызовы)
    json.dumps(p1)                                     # params уходят в state как JSON


def test_changed_assignments_rebuild_auction_and_change_signature():
    a, b = _manifest(), _manifest(assignments={"1": [0], "3": [2, 5]})
    assert manifest_signature(a) != manifest_signature(b)
    assert needs_rebuild(order_params(a, drone_id=1), order_params(b, drone_id=1))
    assert not needs_rebuild(order_params(a, drone_id=1), order_params(a, drone_id=1))
    assert manifest_signature(a) == manifest_signature(_manifest())


def test_hud_consensus_distinguishes_assignments():
    a, b = _manifest(), _manifest(assignments={"1": [0], "3": [2, 5]})
    assert order_consensus.signature(a) != order_consensus.signature(b)
    chosen, dissent, _ = order_consensus.choose([{"drone": i, "ok": True, "body": a if i != 4 else b} for i in range(1, 6)])
    assert chosen is a and dissent == [4]


@pytest.mark.parametrize("bad", [{"assignments": [1, 2]}, {"assignments": {"1": 5}}, {"active_sectors": 7}])
def test_hud_consensus_treats_malformed_manifest_as_missing(bad):
    good = _manifest()
    chosen, dissent, missing = order_consensus.choose(
        [{"drone": 1, "ok": True, "body": good}, {"drone": 2, "ok": True, "body": {**good, **bad}}, {"drone": 3, "ok": True, "body": good}])
    assert chosen is good and dissent == [] and missing == [2]
