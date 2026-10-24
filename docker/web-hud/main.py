#!/usr/bin/env python3
"""web-hud: Mission Control. Приём телеметрии бортов, MAVLink-индикатор C2, опрос ew-controller,
лента «мыслей» LLM, WebSocket для браузера, прокси команд РЭБ (белый список)."""
import asyncio
import hmac
import json
import logging
import os
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

os.environ.setdefault("MAVLINK20", "1")
import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pymavlink import mavutil

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s web-hud %(message)s")
log = logging.getLogger("web-hud")

N = int(os.environ.get("SWARM_SIZE", "5"))
EW_URL = os.environ.get("EW_URL", "http://172.33.0.2:8080")
TOKEN = os.environ.get("EW_TOKEN", "")
SCENE_FILE = os.environ.get("SCENE_FILE", "/opt/swarm/scene/scene.json")
MISSION_FILE = os.environ.get("MISSION_FILE", "/opt/swarm/config/mission.json")
MAV_BIND = os.environ.get("MAV_BIND", "0.0.0.0:14550")
ALLOWED = {o.strip() for o in os.environ.get(
    "HUD_ALLOWED_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000").split(",") if o.strip()}
C2_LOST_S = 3.0
MAX_BODY = 65536
STATIC = Path(__file__).parent / "static"

DR = {i: {"pkt": None, "rx": None, "keys": set()} for i in range(1, N + 1)}
MAV = {i: {"hb": None, "armed": None, "fix": None, "sats": None} for i in range(1, N + 1)}
EW = {"state": None, "ok": False, "err": "not polled yet", "rx": None}
FEED: deque = deque(maxlen=1000)
EVIDENCE: deque = deque(maxlen=200)
AGENT_MGMT_IPS = {i: f"172.33.0.{10+i}" for i in range(1, N + 1)}
_feed_n = 0
CLIENT: httpx.AsyncClient | None = None
_cache: dict = {}


# ───────────── лента ─────────────

def feed_add(kind: str, drone: int, **kw) -> None:
    global _feed_n
    _feed_n += 1
    FEED.append({"n": _feed_n, "kind": kind, "drone": drone, "at": time.time(), **kw})


def _s(v, n):
    return None if v is None else str(v)[:n]


# ───────────── MAVLink (c2-net) ─────────────

def mav_loop() -> None:
    while True:
        try:
            m = mavutil.mavlink_connection(f"udpin:{MAV_BIND}")
            log.info("MAVLink listening on %s", MAV_BIND)
            while True:
                msg = m.recv_match(blocking=True, timeout=1.0)
                if msg is None:
                    continue
                s = msg.get_srcSystem()
                if not 1 <= s <= N or msg.get_srcComponent() != 1:
                    continue          # отсекает, например, sysid=250 (ew-agent)
                d, t = MAV[s], msg.get_type()
                if t == "HEARTBEAT":
                    d["hb"] = time.monotonic()
                    d["armed"] = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
                elif t == "GPS_RAW_INT":
                    d["fix"], d["sats"] = int(msg.fix_type), int(msg.satellites_visible)
        except Exception as e:  # noqa: BLE001
            log.warning("mavlink loop: %s", e)
            time.sleep(2.0)


# ───────────── опрос ew-controller ─────────────

async def ew_poll() -> None:
    while True:
        try:
            r = await CLIENT.get(EW_URL + "/v1/state")
            r.raise_for_status()
            EW.update(state=r.json(), ok=True, err=None, rx=time.monotonic())
        except Exception as e:  # noqa: BLE001
            EW.update(ok=False, err=f"{type(e).__name__}"[:60])
        await asyncio.sleep(1.0)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global CLIENT
    CLIENT = httpx.AsyncClient(timeout=8.0)
    threading.Thread(target=mav_loop, daemon=True).start()
    task = asyncio.create_task(ew_poll())
    yield
    task.cancel()
    await CLIENT.aclose()


app = FastAPI(title="web-hud", lifespan=lifespan)


@app.middleware("http")
async def sec_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' ws: wss:")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "no-store"
    return resp


def guard(request: Request) -> None:
    """защита мутирующих запросов от чужих страниц: Origin + кастомный заголовок (вынуждает preflight)"""
    o = request.headers.get("origin")
    if o and o not in ALLOWED:
        raise HTTPException(403, "bad origin")
    if request.headers.get("x-hud") != "1":
        raise HTTPException(403, "missing X-HUD header")


# ───────────── конфигурация сцены ─────────────

def _load(path: str):
    p = Path(path)
    mt = p.stat().st_mtime
    c = _cache.get(path)
    if not c or c[0] != mt:
        c = _cache[path] = (mt, json.loads(p.read_text()))
    return c[1]


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/config")
def config():
    try:
        return {"swarm_size": N, "scene": _load(SCENE_FILE), "mission": _load(MISSION_FILE)}
    except (OSError, ValueError):
        raise HTTPException(503, "scene/mission not ready")


# ───────────── приём телеметрии ─────────────

@app.post("/api/ingest/{drone}")
async def ingest(drone: int, request: Request):
    if TOKEN and not hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {TOKEN}"):
        raise HTTPException(401, "bad token")
    if drone not in DR:
        raise HTTPException(404, "unknown drone")
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise HTTPException(413, "too large")
    try:
        pkt = json.loads(raw, parse_constant=lambda c: None)       # U16: NaN/Infinity -> null
    except ValueError:
        raise HTTPException(400, "bad json")
    if (not isinstance(pkt, dict) or pkt.get("v") != 1 or pkt.get("drone") != drone
            or not all(isinstance(pkt.get(k), dict) or k == "pos" for k in ("pos", "nav", "swarm", "mesh"))):
        raise HTTPException(422, "bad schema")
    d, keys = DR[drone], set()
    for th in pkt.get("thoughts") or []:
        if not isinstance(th, dict):
            continue
        k = ("L", th.get("source"), th.get("id"), th.get("t"), th.get("verdict"))       # U14 + source: id у S1 и S2 независимы
        keys.add(k)
        if k not in d["keys"]:
            feed_add("llm", drone, source=_s(th.get("source"), 4), id=th.get("id"), t=th.get("t"), trigger=_s(th.get("trigger"), 40),
                     reasoning=_s(th.get("reasoning"), 200), tool=_s(th.get("tool"), 40), args=th.get("args"),
                     verdict=_s(th.get("verdict"), 20), reason=_s(th.get("reason"), 160),
                     latency_ms=th.get("latency_ms"))
    for ev in pkt["swarm"].get("events") or []:
        if isinstance(ev, list) and len(ev) == 2:
            k = ("E", ev[0], ev[1])
            keys.add(k)
            if k not in d["keys"]:
                feed_add("event", drone, t=ev[0], text=_s(ev[1], 160))
    d["keys"] = keys
    stored = {k: v for k, v in pkt.items() if k != "thoughts"}
    stored["swarm"] = {k: v for k, v in pkt["swarm"].items() if k != "events"}
    d["pkt"], d["rx"] = stored, time.monotonic()
    return {"ok": True}


# ───────────── снимок и WebSocket ─────────────

def snapshot() -> dict:
    now = time.monotonic()
    drones = {}
    for i in range(1, N + 1):
        d, m = DR[i], MAV[i]
        hb_age = None if m["hb"] is None else round(now - m["hb"], 1)
        drones[str(i)] = {
            "tele_age": None if d["rx"] is None else round(now - d["rx"], 1),
            "pkt": d["pkt"],
            "c2": {"state": "NO_LINK" if hb_age is None else ("OK" if hb_age < C2_LOST_S else "LOST"),
                   "hb_age": hb_age, "armed": m["armed"], "fix": m["fix"], "sats": m["sats"]}}
    return {"ts": time.time(), "swarm_size": N, "drones": drones, "ew": EW["state"],
            "ew_ok": bool(EW["ok"] and now - (EW["rx"] or 0.0) < 4.0), "ew_err": EW["err"]}


@app.get("/api/state")
def state():
    return snapshot()


@app.websocket("/ws")
async def ws(sock: WebSocket):
    o = sock.headers.get("origin")
    if o and o not in ALLOWED:
        await sock.close(code=1008)
        return
    await sock.accept()
    last = max(0, _feed_n - 150)
    try:
        while True:
            new = [f for f in FEED if f["n"] > last]
            if new:
                last = new[-1]["n"]
            await sock.send_json({"state": snapshot(), "feed": new})
            await asyncio.sleep(0.5)
    except (WebSocketDisconnect, RuntimeError):
        return


# ───────────── прокси РЭБ (белый список) ─────────────

EW_POST = {"c2": "/v1/ew/c2", "gps": "/v1/ew/gps", "mesh": "/v1/ew/mesh", "restore": "/v1/ew/restore",
           "scenario_run": "/v1/scenarios/run", "scenario_stop": "/v1/scenarios/stop"}


async def ew_forward(method: str, path: str, body: dict | None = None):
    try:
        r = await CLIENT.request(method, EW_URL + path, json=body)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"ew-controller unreachable: {type(e).__name__}")
    try:
        data = r.json()
    except ValueError:
        data = {"raw": r.text[:200]}
    return JSONResponse(data, status_code=r.status_code)


@app.get("/api/ew/scenarios")
async def ew_scenarios():
    return await ew_forward("GET", "/v1/scenarios")


@app.post("/api/ew/{name}", dependencies=[Depends(guard)])
async def ew_post(name: str, request: Request):
    path = EW_POST.get(name)
    if path is None:
        raise HTTPException(404, "unknown action")
    raw = await request.body()
    if len(raw) > 4096:
        raise HTTPException(413, "too large")
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(400, "bad json")
    if not isinstance(body, dict):
        raise HTTPException(400, "object expected")
    return await ew_forward("POST", path, body)


# ───────────── evidence & tasking ─────────────

@app.post("/api/evidence/{drone}")
async def post_evidence(drone: int, request: Request):
    if TOKEN and not hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {TOKEN}"):
        raise HTTPException(401, "bad token")
    raw = await request.body()
    if len(raw) > 65536:
        raise HTTPException(413, "too large")
    try:
        card = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "bad json")
    card["id"], card["drone"] = len(EVIDENCE) + 1, drone
    EVIDENCE.append(card)
    return {"ok": True, "id": card["id"]}


@app.get("/api/evidence")
def get_evidence():
    return [{k: v for k, v in c.items() if k != "image_jpeg_b64"} for c in EVIDENCE]


@app.get("/api/evidence/{card_id}/image")
def get_evidence_image(card_id: int):
    c = next((c for c in EVIDENCE if c["id"] == card_id), None)
    if c is None:
        raise HTTPException(404, "not found")
    return Response(bytes.fromhex(c["image_jpeg_b64"]), media_type="image/jpeg")


@app.post("/api/tasking")
async def post_tasking(request: Request):
    body = await request.json()
    prompt = str(body.get("prompt", "")).strip()
    if not (1 <= len(prompt) <= 500):
        raise HTTPException(422, "prompt length 1..500")

    async def one(i):
        try:
            r = await CLIENT.post(
                f"http://{AGENT_MGMT_IPS[i]}:9091/v1/tasking",
                json={"prompt": prompt},
                headers={"Authorization": f"Bearer {TOKEN}"},
                timeout=25.0
            )
            return {"drone": i, "ok": r.status_code == 200, "body": r.json() if r.status_code == 200 else r.text}
        except Exception as e:
            return {"drone": i, "ok": False, "error": str(e)}

    results = await asyncio.gather(*(one(i) for i in range(1, N + 1)))
    return {"results": results}


# ───────────── статика ─────────────

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
