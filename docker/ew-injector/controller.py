#!/usr/bin/env python3
"""ew-controller: оркестратор РЭБ. HTTP/WS API + таймерные сценарии (YAML)."""
import asyncio
import hmac
import logging
import os
import time
from pathlib import Path
from typing import Any, Literal

import httpx
import yaml
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s ew-controller %(message)s")
log = logging.getLogger("ew-controller")

AGENTS = [u.strip() for u in os.environ["AGENT_URLS"].split(",")]
BRIDGE = os.environ["SIM_BRIDGE_URL"]
TOKEN = os.environ.get("EW_TOKEN", "")
SCEN_DIR = Path(os.environ.get("SCENARIO_DIR", "/app/scenarios"))
N = len(AGENTS)
client = httpx.AsyncClient(timeout=6.0, headers={"Authorization": f"Bearer {TOKEN}"} if TOKEN else {})

LEVELS: dict[str, dict[str, Any]] = {
    "L1": dict(loss_pct=30, loss_corr_pct=25, delay_ms=50, jitter_ms=50, rate_kbit=64, limit_pkts=100),
    "L2": dict(loss_pct=50, loss_corr_pct=30, delay_ms=200, jitter_ms=200, rate_kbit=20, limit_pkts=60),
    "L3": dict(loss_pct=80, loss_corr_pct=40, delay_ms=500, jitter_ms=500, rate_kbit=5, limit_pkts=40),
    "L4": dict(loss_pct=100, loss_corr_pct=0, delay_ms=0, jitter_ms=0, rate_kbit=None, limit_pkts=10),
}


# ───────────── транспорт ─────────────

def targets(drones: list[int] | None) -> list[int]:
    ids = drones or list(range(1, N + 1))
    bad = [d for d in ids if not 1 <= d <= N]
    if bad:
        raise HTTPException(422, f"unknown drones: {bad}")
    return ids


async def call(base: str, method: str, path: str, body: dict | None = None) -> dict:
    try:
        r = await client.request(method, base + path, json=body)
        data = r.json() if r.content else None
        return {"ok": r.status_code < 300, "status": r.status_code, "body": data}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "error": str(e)}


async def to_agents(ids: list[int], method: str, path: str, body: dict | None = None) -> dict:
    res = await asyncio.gather(*[call(AGENTS[i - 1], method, path, body) for i in ids])
    return {str(i): r for i, r in zip(ids, res)}


# ───────────── модели запросов ─────────────

class Common(BaseModel):
    drones: list[int] | None = None
    duration_s: float | None = Field(None, gt=0)


class C2Req(Common):
    include_onboard: bool = False


class MeshReq(Common):
    level: Literal["L1", "L2", "L3", "L4"] | None = None
    loss_pct: float | None = Field(None, ge=0, le=100)
    loss_corr_pct: float | None = Field(None, ge=0, le=100)
    delay_ms: int | None = Field(None, ge=0)
    jitter_ms: int | None = Field(None, ge=0)
    rate_kbit: int | None = Field(None, ge=1)
    limit_pkts: int | None = Field(None, ge=1)
    peers: list[int] | None = None


class GpsReq(Common):
    mode: Literal["denied", "degraded", "noise", "drift", "spoof"]
    eph_start_m: float = 5.0
    eph_end_m: float = 60.0
    ramp_s: float = 30.0
    rate_mps: float = 1.0
    heading_deg: float = 45.0
    sigma_m: float = 2.0
    target_north_m: float = 0.0
    target_east_m: float = 0.0


class RestoreReq(BaseModel):
    channel: Literal["c2", "gps", "mesh", "all"] = "all"
    drones: list[int] | None = None


# ───────────── таймеры авто-восстановления ─────────────

TIMERS: dict[tuple[str, int], asyncio.Task] = {}


def cancel_timers(channel: str, ids: list[int]) -> None:
    for ch in ("c2", "gps", "mesh") if channel == "all" else (channel,):
        for i in ids:
            t = TIMERS.pop((ch, i), None)
            if t and not t.done() and t is not asyncio.current_task():
                t.cancel()


def arm_timer(channel: str, ids: list[int], seconds: float | None) -> None:
    cancel_timers(channel, ids)
    if not seconds:
        return

    async def _later(i: int):
        await asyncio.sleep(seconds)
        TIMERS.pop((channel, i), None)
        await do_restore(RestoreReq(channel=channel, drones=[i]), _from_timer=True)

    for i in ids:
        TIMERS[(channel, i)] = asyncio.create_task(_later(i))


# ───────────── действия ─────────────

async def do_c2(r: C2Req) -> dict:
    ids = targets(r.drones)
    out = await to_agents(ids, "POST", "/v1/c2", {"ports": [14550], "include_onboard": r.include_onboard})
    arm_timer("c2", ids, r.duration_s)
    log.info("c2 cut drones=%s onboard=%s", ids, r.include_onboard)
    return out


async def do_mesh(r: MeshReq) -> dict:
    p: dict[str, Any] = dict(LEVELS[r.level]) if r.level else {}
    for k in ("loss_pct", "loss_corr_pct", "delay_ms", "jitter_ms", "rate_kbit", "limit_pkts"):
        v = getattr(r, k)
        if v is not None:
            p[k] = v
    if not p:
        raise HTTPException(422, "need level or explicit parameters")
    p["peers"] = r.peers
    ids = targets(r.drones)
    out = await to_agents(ids, "POST", "/v1/mesh", p)
    arm_timer("mesh", ids, r.duration_s)
    log.info("mesh drones=%s profile=%s", ids, p)
    return out


async def do_gps(r: GpsReq) -> dict:
    ids = targets(r.drones)
    if r.mode in ("denied", "degraded"):
        body = {"mode": r.mode, "eph_start_m": r.eph_start_m, "eph_end_m": r.eph_end_m, "ramp_s": r.ramp_s}
        out = {"agents": await to_agents(ids, "POST", "/v1/gps", body)}
    else:
        out = {"bridge": await call(BRIDGE, "POST", "/v1/gps", {
            "drones": ids, "mode": r.mode, "rate_mps": r.rate_mps, "heading_deg": r.heading_deg,
            "sigma_m": r.sigma_m, "target_north_m": r.target_north_m, "target_east_m": r.target_east_m})}
    arm_timer("gps", ids, r.duration_s)
    log.info("gps drones=%s mode=%s", ids, r.mode)
    return out


async def do_restore(r: RestoreReq, _from_timer: bool = False) -> dict:
    ids = targets(r.drones)
    ch = r.channel
    out: dict[str, Any] = {}
    if ch in ("c2", "all"):
        out["c2"] = await to_agents(ids, "DELETE", "/v1/c2")
    if ch in ("mesh", "all"):
        out["mesh"] = await to_agents(ids, "DELETE", "/v1/mesh")
    if ch in ("gps", "all"):
        out["gps_agents"] = await to_agents(ids, "DELETE", "/v1/gps")
        out["gps_bridge"] = await call(BRIDGE, "POST", "/v1/gps", {"drones": ids, "mode": "pass"})
    if not _from_timer:
        cancel_timers(ch, ids)
    log.info("restore channel=%s drones=%s", ch, ids)
    return out


ACTIONS = {
    "c2": (C2Req, do_c2),
    "gps": (GpsReq, do_gps),
    "mesh": (MeshReq, do_mesh),
    "restore": (RestoreReq, do_restore),
}


# ───────────── состояние и статусы ─────────────

ORDER = {"GREEN": 0, "DEGRADED": 1, "JAMMED": 2}


def classify(a: dict, bridge_mode: str) -> str:
    c2, gps, mesh = a["c2"], a["gps"], a["mesh"]
    if c2["active"] or (gps["active"] and gps["mode"] == "denied"):
        return "JAMMED"
    if mesh["active"]:
        p = mesh["profile"]
        if p["loss_pct"] >= 50 or (p.get("rate_kbit") and p["rate_kbit"] <= 10):
            return "JAMMED"
        return "DEGRADED"
    if gps["active"] or bridge_mode != "pass":
        return "DEGRADED"
    return "GREEN"


class Runner:
    def __init__(self):
        self.task: asyncio.Task | None = None
        self.name: str | None = None
        self.t0: float | None = None
        self.events: list[dict] = []
        self.finished = False

    def info(self) -> dict:
        return {"running": bool(self.task and not self.task.done()), "name": self.name,
                "elapsed_s": round(time.monotonic() - self.t0, 1) if self.t0 else None,
                "events": self.events[-20:]}

    async def stop(self, restore: bool = True):
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        if restore:
            await do_restore(RestoreReq(channel="all"))

    async def start(self, name: str, sc: dict):
        await self.stop(restore=False)
        self.name, self.events, self.finished = name, [], False
        self.task = asyncio.create_task(self._run(sc))

    async def _run(self, sc: dict):
        self.t0 = time.monotonic()
        for step in sorted(sc["steps"], key=lambda s: s["at"]):
            await asyncio.sleep(max(0.0, self.t0 + float(step["at"]) - time.monotonic()))
            model, handler = ACTIONS[step["action"]]
            t = round(time.monotonic() - self.t0, 2)
            try:
                await handler(model(**step.get("params", {})))
                self.events.append({"t": t, "action": step["action"], "ok": True})
            except Exception as e:  # noqa: BLE001
                log.error("scenario step failed: %s", e)
                self.events.append({"t": t, "action": step["action"], "ok": False, "error": str(e)})
        self.finished = True


runner = Runner()
app = FastAPI(title="ew-controller")


def require_token(request: Request):
    """Изменяющие методы РЭБ — только с Bearer EW_TOKEN (HUD, ewctl.sh, скрипты стенда). Пустой токен = отказ, а не «без проверки»."""
    if not TOKEN:
        raise HTTPException(503, "EW_TOKEN not configured")
    if not hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {TOKEN}"):
        raise HTTPException(401, "bad token")


async def build_state() -> dict:
    ids = list(range(1, N + 1))
    agents, bridge = await asyncio.gather(to_agents(ids, "GET", "/v1/status"), call(BRIDGE, "GET", "/v1/gps"))
    bmodes = (bridge.get("body") or {}) if bridge["ok"] else {}
    drones, worst = {}, "GREEN"
    for i in ids:
        r = agents[str(i)]
        if not r["ok"]:
            drones[str(i)] = {"status": "UNKNOWN", "error": r.get("error") or r["status"]}
            continue
        b = r["body"]
        bm = (bmodes.get(str(i)) or {}).get("mode", "pass")
        st = classify(b, bm)
        worst = max(worst, st, key=ORDER.get)
        drones[str(i)] = {"status": st, "c2": b["c2"], "gps": b["gps"], "mesh": b["mesh"], "gps_bridge": bmodes.get(str(i))}
    return {"ts": time.time(), "overall": worst, "drones": drones, "scenario": runner.info()}


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/v1/state")
async def state():
    return await build_state()


@app.websocket("/v1/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    try:
        while True:
            await sock.send_json(await build_state())
            await asyncio.sleep(1.0)
    except (WebSocketDisconnect, RuntimeError):
        return


@app.post("/v1/ew/c2", dependencies=[Depends(require_token)])
async def ep_c2(r: C2Req):
    return await do_c2(r)


@app.post("/v1/ew/gps", dependencies=[Depends(require_token)])
async def ep_gps(r: GpsReq):
    return await do_gps(r)


@app.post("/v1/ew/mesh", dependencies=[Depends(require_token)])
async def ep_mesh(r: MeshReq):
    return await do_mesh(r)


@app.post("/v1/ew/restore", dependencies=[Depends(require_token)])
async def ep_restore(r: RestoreReq):
    return await do_restore(r)


class RunReq(BaseModel):
    name: str | None = None
    inline: dict | None = None


class StopReq(BaseModel):
    restore: bool = True


@app.get("/v1/scenarios")
async def scenarios():
    out = []
    for f in sorted(SCEN_DIR.glob("*.yaml")):
        d = yaml.safe_load(f.read_text())
        out.append({"name": f.stem, "description": d.get("description", ""), "steps": len(d["steps"])})
    return out


@app.post("/v1/scenarios/run", dependencies=[Depends(require_token)])
async def run_scenario(r: RunReq):
    if r.inline:
        sc, name = r.inline, "inline"
    elif r.name:
        f = SCEN_DIR / f"{r.name}.yaml"
        if not f.is_file():
            raise HTTPException(404, f"scenario {r.name} not found")
        sc, name = yaml.safe_load(f.read_text()), r.name
    else:
        raise HTTPException(422, "name or inline required")
    for s in sc["steps"]:
        if s["action"] not in ACTIONS:
            raise HTTPException(422, f"unknown action {s['action']}")
        ACTIONS[s["action"]][0](**s.get("params", {}))        # валидация параметров заранее
    await runner.start(name, sc)
    return runner.info()


@app.post("/v1/scenarios/stop", dependencies=[Depends(require_token)])
async def stop_scenario(r: StopReq):
    await runner.stop(restore=r.restore)
    return runner.info()
