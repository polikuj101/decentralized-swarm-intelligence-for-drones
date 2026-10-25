#!/usr/bin/env python3
"""swarm/tasking.py — компиляция NL-приказа оператора в Task Manifest локальным Qwen2.5-0.5B.
HTTP-сервер (порт 9091, mgmt-net) + функция компиляции. Вызывается до взлёта, пока жив C2."""
import hmac
import json
import os
import time
import urllib.request

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from swarm import common
from swarm.manifest_check import manifest_problem

LLM_URL = os.environ.get("LLM_URL", "http://127.0.0.1:8080")
GRAMMAR_FILE = os.environ.get("GRAMMAR_FILE", "/opt/grammar/manifest.gbnf")
MANIFEST_FILE = os.environ.get("MANIFEST_FILE", "/opt/swarm/config/manifest.json")
EW_TOKEN = os.environ.get("EW_TOKEN", "")

# Сопоставление имя зоны -> ID базового сектора (COLS=3, ROWS=2 из mission.json, раздел 3.4.6/4.5).
# ЗАДАЁТСЯ ИНЖЕНЕРАМИ, не выводится из геометрии. Bravo намеренно указывает на S4, где по текущей
# раскладке scene.json находится мишень T0 (см. D4, п. 9.1) — это гарантирует срабатывание демо-нарратива
# «разведать квадрат Браво -> найдена цель». При изменении позиций целей или сетки секторов таблицу
# необходимо пересчитать вручную.
ZONE_TO_SECTOR = {"alpha": 0, "bravo": 4, "charlie": 1, "delta": 2, "echo": 3, "foxtrot": 5}

SYSTEM = ("You convert a human operator's mission order (in English or Russian) into a structured task manifest for an "
          "autonomous drone swarm searching named zones (alpha..foxtrot, or all). "
          "Zone mapping: Альфа=alpha, Браво=bravo, Чарли=charlie, Дельта=delta, Эхо=echo, Фокстрот=foxtrot, все=all. "
          'Reply with JSON only: {"zones": [...], "on_target": "...", "dwell_s": <int>, '
          '"rtb_on_complete": <bool>}. zones: only the zones explicitly or clearly implied by the order. '
          "on_target: DWELL_RECON if the order asks to investigate/hover over findings, DWELL_ONLY if it "
          "only asks to confirm presence, SKIP if detections should be ignored. dwell_s: 3-15, default 8 "
          "unless specified. rtb_on_complete: true unless the order says to hold position after the search.")


class TaskingError(Exception):
    pass


def compile_order(prompt: str, timeout_s: float = 20.0) -> dict:
    """Детерминированно: temperature=0, top_k=1, seed=42 -> одинаковый prompt на разных бортах даёт
    одинаковый manifest без обмена по сети (см. п. 9.3, D2)."""
    grammar = None
    candidates = [
        GRAMMAR_FILE,
        "/opt/grammar/manifest.gbnf",
        os.path.join(os.path.dirname(__file__), "..", "grammar", "manifest.gbnf"),
        os.path.join(os.path.dirname(__file__), "grammar", "manifest.gbnf"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    grammar = f.read()
                    break
            except Exception:
                pass
    if not grammar:
        raise TaskingError(f"manifest.gbnf not found in candidates: {candidates}")

    body = {
        "prompt": f"<|im_start|>system\n{SYSTEM}<|im_end|>\n<|im_start|>user\n{prompt.strip()}<|im_end|>\n<|im_start|>assistant\n",
        "grammar": grammar, "n_predict": 100, "temperature": 0.0, "top_k": 1, "seed": 42,
        "cache_prompt": True, "stop": ["<|im_end|>"],
    }
    req = urllib.request.Request(LLM_URL + "/completion", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        res = json.loads(r.read())
    try:
        out = json.loads(res["content"])
    except (json.JSONDecodeError, KeyError) as e:
        raise TaskingError(f"malformed completion: {e!r}") from e

    if "all" in out["zones"]:
        sectors = sorted(set(ZONE_TO_SECTOR.values()))
    else:
        sectors = sorted({ZONE_TO_SECTOR[z] for z in out["zones"] if z in ZONE_TO_SECTOR})
    if not sectors:
        raise TaskingError("manifest resolved to zero active sectors")

    return {
        "manifest_id": f"m-{int(time.time())}", "source_prompt": prompt.strip()[:300],
        "zones_named": out["zones"], "active_sectors": sectors, "on_target": out["on_target"],
        "dwell_s": int(out["dwell_s"]), "rtb_on_complete": bool(out["rtb_on_complete"]),
        "compiled_at": time.time(), "latency_ms": round((time.monotonic() - t0) * 1000, 1),
    }


def write_manifest(manifest: dict) -> None:
    os.makedirs(os.path.dirname(MANIFEST_FILE), exist_ok=True)
    tmp = MANIFEST_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    os.replace(tmp, MANIFEST_FILE)   # атомарно: coordinator никогда не видит частично записанный файл


app = FastAPI(title=f"tasking-{common.DRONE_ID}")


class TaskingReq(BaseModel):
    prompt: str


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/v1/manifest")
def post_manifest(m: dict, authorization: str = Header(default="")):
    """Принять готовый манифест (HUD выравнивает борты по большинству); проверка та же, что у координатора при загрузке."""
    if not EW_TOKEN:
        raise HTTPException(503, "EW_TOKEN not configured")
    if not hmac.compare_digest(authorization.encode(), f"Bearer {EW_TOKEN}".encode()):
        raise HTTPException(401, "bad token")
    bad = manifest_problem(m, common.COLS * common.ROWS)
    if bad:
        raise HTTPException(422, bad)
    write_manifest(m)
    return m


@app.post("/v1/tasking")
def post_tasking(r: TaskingReq, authorization: str = Header(default="")):
    if not EW_TOKEN:
        raise HTTPException(503, "EW_TOKEN not configured")
    if not hmac.compare_digest(authorization.encode(), f"Bearer {EW_TOKEN}".encode()):
        raise HTTPException(401, "bad token")
    try:
        manifest = compile_order(r.prompt)
    except (TaskingError, Exception) as e:  # noqa: BLE001
        raise HTTPException(502, f"tasking compile failed: {e!r}")
    write_manifest(manifest)
    return manifest


if __name__ == "__main__":
    uvicorn.run(app, host=os.environ.get("SELF_MGMT_IP", "0.0.0.0"), port=9091, log_level="info")
