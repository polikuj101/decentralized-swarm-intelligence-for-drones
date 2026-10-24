"""docker/web-hud/tests/test_hud_endpoints.py — эндпоинты web-hud без живого стенда (TestClient, EW-контроллер подменён).
Регрессии ревью 2026-10-24: guard на /api/tasking, монотонный id карточек BDA, split/heal и расчёт сходимости при ingest."""
import os
import sys
import time
import types
from pathlib import Path

import pytest

os.environ["EW_TOKEN"] = ""                      # без токена: проверяем guard (Origin + X-HUD), а не Bearer
os.environ.setdefault("SWARM_SIZE", "5")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import pymavlink  # noqa: F401
except ImportError:                              # на хосте pymavlink может не быть; main использует его только в mav_loop
    pm = types.ModuleType("pymavlink")
    pm.mavutil = types.SimpleNamespace()
    sys.modules["pymavlink"] = pm
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import convergence  # noqa: E402
import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

HUD = {"X-HUD": "1"}
TICK = 0.05          # на Windows шаг time.monotonic() ~15 мс: разводим события, иначе rx == t_heal


class FakeEW:
    """Подмена httpx.AsyncClient: запоминает вызовы к ew-controller."""

    def __init__(self, status=200, agent_error=None):
        self.status, self.agent_error, self.calls = status, agent_error, []

    async def request(self, method, url, json=None):
        self.calls.append((method, url.split("8080")[-1] if "8080" in url else url, json))
        outer = self

        class R:
            status_code = outer.status
            text = "{}"

            def json(self):
                if outer.agent_error:                      # форма ответа ew-controller: HTTP 200, ошибки по агентам в теле
                    return {"1": {"ok": False, "status": 500, "body": {"error": outer.agent_error}},
                            "2": {"ok": True, "status": 200, "body": {}}}
                return {"1": {"ok": outer.status == 200, "status": outer.status}}
        return R()


@pytest.fixture()
def hud():
    main.EVIDENCE.clear()
    main.TRACK = convergence.Tracker()
    for d in main.DR.values():
        d.update(pkt=None, rx=None, keys=set())
    main.CLIENT = FakeEW()
    return TestClient(main.app)


def pkt(drone, epoch=7, alive=5, winners=None):
    return {"v": 1, "drone": drone, "pos": [0, 0, 0], "nav": {}, "mesh": {},
            "swarm": {"epoch": epoch, "winners": winners or {}, "alive": list(range(1, alive + 1))}}


def ingest_all(c, **kw):
    for i in range(1, 6):
        assert c.post(f"/api/ingest/{i}", json=pkt(i, **kw)).status_code == 200


# ───────────── /api/tasking guard (регрессия: раньше был без Depends(guard)) ─────────────

def test_tasking_without_x_hud_is_403(hud):
    assert hud.post("/api/tasking", json={"prompt": "x"}).status_code == 403


def test_tasking_foreign_origin_is_403(hud):
    r = hud.post("/api/tasking", json={"prompt": "x"}, headers={**HUD, "Origin": "http://evil.com"})
    assert r.status_code == 403


def test_tasking_with_x_hud_reaches_handler(hud):
    assert hud.post("/api/tasking", json={"prompt": ""}, headers=HUD).status_code == 422        # длина промпта проверяется уже в хендлере


# ───────────── id карточек BDA (регрессия: len(deque)+1 давал id=201 после 200-й) ─────────────

def test_evidence_ids_stay_unique_after_deque_is_full(hud):
    for i in range(203):                                        # дубликаты id у старого кода начинаются с 202-й карточки
        card = {"image_jpeg_b64": f"{i:04x}", "enu": [0, 0]}
        assert hud.post(f"/api/evidence/{(i % 5) + 1}", json=card).status_code == 200
    cards = hud.get("/api/evidence").json()
    ids = [c["id"] for c in cards]
    assert len(cards) == 200 and len(set(ids)) == 200 and ids == sorted(ids)
    assert hud.get(f"/api/evidence/{ids[-1]}/image").content == bytes.fromhex(f"{202:04x}")      # последняя карточка отдаёт свой кадр
    assert hud.get(f"/api/evidence/{ids[0] - 1}/image").status_code == 404                        # вытесненная — 404


def test_evidence_unknown_id_is_404(hud):
    assert hud.get("/api/evidence/999999/image").status_code == 404


# ───────────── split / heal ─────────────

def test_split_requires_guard(hud):
    assert hud.post("/api/split").status_code == 403
    assert hud.post("/api/heal").status_code == 403


def test_split_cuts_both_groups_with_deadman_timer(hud):
    r = hud.post("/api/split", headers=HUD)
    assert r.status_code == 200 and r.json()["groups"] == [[1, 2, 3], [4, 5]]
    calls = [c for c in main.CLIENT.calls if c[1] == "/v1/ew/mesh"]
    assert len(calls) == 5
    for _, _, body in calls:
        assert body["level"] == "L4" and body["duration_s"] == main.SPLIT_MAX_S
        other = [4, 5] if body["drones"][0] in (1, 2, 3) else [1, 2, 3]
        assert body["peers"] == other
    assert main.TRACK.phase == "split"


def test_split_failure_does_not_start_measurement(hud):
    main.CLIENT = FakeEW(status=500)
    assert hud.post("/api/split", headers=HUD).status_code == 502
    assert main.TRACK.phase == "idle"


def test_split_with_agent_errors_is_not_reported_as_success(hud):
    """Регрессия: ядро без netem/prio -> агенты отвечают 500 внутри HTTP 200; раньше HUD говорил «ok» и мерил несуществующее слияние."""
    main.CLIENT = FakeEW(agent_error="tc qdisc add ... Error: Specified qdisc kind is unknown.")
    r = hud.post("/api/split", headers=HUD)
    assert r.status_code == 502 and "qdisc kind is unknown" in r.json()["detail"]
    assert main.TRACK.phase == "idle"
    assert any(c[1] == "/v1/ew/restore" for c in main.CLIENT.calls)          # частичное применение откатывается


def test_heal_with_agent_errors_does_not_start_timer(hud):
    hud.post("/api/split", headers=HUD)
    main.CLIENT = FakeEW(agent_error="boom")
    assert hud.post("/api/heal", headers=HUD).status_code == 502
    assert main.TRACK.phase == "split"


def test_heal_without_split_does_not_measure(hud):
    assert hud.post("/api/heal", headers=HUD).status_code == 200
    assert main.TRACK.phase == "idle"


def test_heal_failure_does_not_start_timer(hud):
    hud.post("/api/split", headers=HUD)
    main.CLIENT = FakeEW(status=500)
    assert hud.post("/api/heal", headers=HUD).status_code == 502
    assert main.TRACK.phase == "split"


# ───────────── сходимость при ingest ─────────────

def test_ingest_while_idle_does_not_track(hud):
    ingest_all(hud)
    assert hud.get("/api/state").json()["converge"]["phase"] == "idle"


def test_convergence_measured_from_heal_on_fresh_packets_only(hud):
    hud.post("/api/split", headers=HUD)
    ingest_all(hud, epoch=7, alive=3)                          # во время разделения «видят» только свою группу
    time.sleep(TICK)
    hud.post("/api/heal", headers=HUD)
    time.sleep(TICK)
    snap = hud.get("/api/state").json()["converge"]
    assert snap["phase"] == "merging" and snap["merge_s"] is None      # пакеты до Heal не считаются, хотя эпоха одна
    ingest_all(hud, epoch=7, alive=5)
    snap = hud.get("/api/state").json()["converge"]
    assert snap["phase"] == "converged" and snap["merge_s"] >= 0 and snap["dup_merge"] == []


def test_duplicates_during_split_reported_separately(hud):
    hud.post("/api/split", headers=HUD)
    for i in range(1, 6):
        winners = {"3": 1} if i <= 3 else {"3": 4}
        hud.post(f"/api/ingest/{i}", json=pkt(i, alive=3, winners=winners))
    time.sleep(TICK)
    hud.post("/api/heal", headers=HUD)
    time.sleep(TICK)
    ingest_all(hud)
    snap = hud.get("/api/state").json()["converge"]
    assert snap["dup_split"] == ["3"] and snap["dup_merge"] == [] and snap["phase"] == "converged"
