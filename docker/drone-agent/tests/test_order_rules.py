"""tests/test_order_rules.py — разбор приказа правилами + ответ модели (HANDOFF п. 5).

Набор приказов и ожидаемых полей — тот же, что в живой проверке scripts/verify_tasking.py (20 приказов, точность Qwen-0.5B 4/20)."""
import io
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parents[1] / "scripts"))
# swarm.common читает конфиг при импорте; вне контейнера — mission.json из репозитория и минимальная сцена
os.environ.setdefault("MISSION_FILE", str(ROOT / "config" / "mission.json"))
if "SCENE_FILE" not in os.environ:
    _scene = Path(tempfile.mkdtemp()) / "scene.json"
    _scene.write_text(json.dumps({"drones": [{"id": i, "x_m": 0.0, "y_m": 5.0 * i} for i in range(1, 6)], "targets": [], "obstacles": []}))
    os.environ["SCENE_FILE"] = str(_scene)

from swarm import order_rules, tasking  # noqa: E402
from verify_tasking import CASES  # noqa: E402

# ответ по умолчанию, который модель давала в 10 из 20 приказов независимо от текста
WORST_LLM = {"zones": ["alpha", "bravo", "charlie"], "on_target": "DWELL_RECON", "dwell_s": 8, "rtb_on_complete": True}


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_llm(monkeypatch, out=WORST_LLM, fail=False):
    calls = []

    def urlopen(req, timeout=None):
        calls.append(json.loads(req.data))
        if fail:
            raise OSError("connection refused")
        return _Resp(json.dumps({"content": json.dumps(out)}).encode())

    monkeypatch.setattr(tasking.urllib.request, "urlopen", urlopen)
    return calls


@pytest.mark.parametrize("lang,prompt,expected", CASES, ids=[f"{i + 1}-{c[0]}" for i, c in enumerate(CASES)])
def test_verify_tasking_set_is_correct_even_with_the_default_llm_answer(monkeypatch, lang, prompt, expected):
    _fake_llm(monkeypatch)
    m = tasking.compile_order(prompt)
    got = {k: m[k] for k in expected}
    assert got == expected, m["parsed_by"]


def test_zones_never_come_from_the_model(monkeypatch):
    _fake_llm(monkeypatch)
    m = tasking.compile_order("Разведать квадрат Браво")
    assert m["active_sectors"] == [4] and m["parsed_by"]["zones"] == "rules"
    assert m["llm_zones"] == ["alpha", "bravo", "charlie"]       # что предложила модель, видно в манифесте


def test_order_without_zones_is_rejected_before_calling_the_model(monkeypatch):
    calls = _fake_llm(monkeypatch)
    with pytest.raises(tasking.TaskingError, match="не названа ни одна зона"):
        tasking.compile_order("Осмотреться и доложить")
    assert calls == []


def test_unspecified_fields_come_from_the_model(monkeypatch):
    _fake_llm(monkeypatch, out={"zones": ["all"], "on_target": "DWELL_ONLY", "dwell_s": 11, "rtb_on_complete": False})
    m = tasking.compile_order("Look at Charlie please")
    assert (m["active_sectors"], m["on_target"], m["dwell_s"], m["rtb_on_complete"]) == ([1], "DWELL_ONLY", 11, False)
    assert m["parsed_by"] == {"zones": "rules", "on_target": "llm", "dwell_s": "llm", "rtb_on_complete": "llm"}


def test_model_down_still_compiles_with_defaults(monkeypatch):
    _fake_llm(monkeypatch, fail=True)
    m = tasking.compile_order("Проверить Фокстрот, цели игнорировать")
    assert (m["active_sectors"], m["on_target"], m["dwell_s"], m["rtb_on_complete"]) == ([5], "SKIP", 8, True)
    assert m["parsed_by"]["dwell_s"] == "default" and "connection refused" in m["llm_error"]


def test_model_is_asked_only_for_fields_missing_from_the_text(monkeypatch):
    calls = _fake_llm(monkeypatch)
    m = tasking.compile_order("Search sector Alpha")
    assert len(calls) == 1 and calls[0]["temperature"] == 0.0 and calls[0]["seed"] == 42
    calls.clear()
    m = tasking.compile_order("Sweep zone Charlie, investigate any contacts for 5 seconds, then return to base")
    assert calls == [] and m["latency_ms"] < 50 and set(m["parsed_by"].values()) == {"rules"}


@pytest.mark.parametrize("text,zones", [
    ("Облететь Альфу и Дельту", ["alpha", "delta"]),
    ("Пройтись по Фокстроту", ["foxtrot"]),
    ("Search everything", ["all"]),
    ("Ignore all detections in Echo", ["echo"]),
    ("Обследовать весь район", ["all"]),
    ("Обследовать всю территорию", ["all"]),
    ("all sectors", ["all"]),
    ("Alpha Bravo Charlie Delta Echo Foxtrot", ["all"]),
])
def test_zones(text, zones):
    assert order_rules.zones(text) == zones


@pytest.mark.parametrize("text,want", [
    ("после поиска не возвращаться", False),
    ("do not return to base", False),
    ("потом вернуться", True),
    ("RTB when done", True),
    ("осмотреть Альфу", None),
    ("вернуться, но остаться на месте", None),     # противоречие — решает модель
])
def test_rtb(text, want):
    assert order_rules.rtb(text) is want


@pytest.mark.parametrize("text,want", [
    ("зависнуть на пятнадцать секунд", 15),
    ("hover for thirteen seconds", 13),
    ("dwell 40 seconds", 15),                     # за пределами грамматики 3..15 — к ближайшей границе
    ("Search sector 3", None),                    # число без единиц времени — не время зависания
])
def test_dwell(text, want):
    assert order_rules.dwell_s(text) == want


def test_without_recon_beats_recon():
    assert order_rules.on_target("при обнаружении только подтверди присутствие, без доразведки") == "DWELL_ONLY"


# ───────── примеры в подсказке модели (tasking.SHOTS) ─────────

def test_shots_are_valid_manifest_answers():
    """Пример, нарушающий грамматику, учил бы модель отвечать вне grammar/manifest.gbnf."""
    for _q, a in tasking.SHOTS:
        assert set(a) == {"zones", "on_target", "dwell_s", "rtb_on_complete"}
        assert 1 <= len(a["zones"]) <= 3 and set(a["zones"]) <= set(order_rules.ZONES) | {"all"}
        assert a["on_target"] in ("DWELL_RECON", "DWELL_ONLY", "SKIP")
        assert 3 <= a["dwell_s"] <= 15 and isinstance(a["rtb_on_complete"], bool)


def test_shots_do_not_leak_the_eval_set():
    """Замер точности на CASES честен, только пока примеры не совпадают с проверочными приказами."""
    shots = {q.strip().lower() for q, _ in tasking.SHOTS}
    assert not shots & {prompt.strip().lower() for _lang, prompt, _exp in CASES}


def test_chat_prompt_layout():
    p = tasking.chat_prompt("  Проверить Альфу  ")
    assert p.startswith("<|im_start|>system\n" + tasking.SYSTEM)
    assert p.count("<|im_start|>user\n") == len(tasking.SHOTS) + 1
    assert p.endswith("<|im_start|>user\nПроверить Альфу<|im_end|>\n<|im_start|>assistant\n")
    for q, a in tasking.SHOTS:
        assert f"<|im_start|>user\n{q}<|im_end|>\n<|im_start|>assistant\n{json.dumps(a, ensure_ascii=False)}<|im_end|>" in p
