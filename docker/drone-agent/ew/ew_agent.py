#!/usr/bin/env python3
"""ew-agent: исполнитель РЭБ внутри drone-agent-N.
C2   -> nftables (drop по портам)
Mesh -> tc netem (root netem либо prio + u32 по dst-IP пира); на ядрах без netem/prio — nftables (см. nft_mesh_script)
GNSS -> MAVLink PARAM_SET параметров SIM_GPS_JAM / SIM_GPS_JAM_EPH (патч PX4, см. п. 2.5)
"""
import ipaddress
import json
import logging
import os
import struct
import subprocess
import threading
import time
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from pymavlink import mavutil

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s ew-agent %(message)s")
log = logging.getLogger("ew-agent")

DRONE_ID = int(os.environ["DRONE_ID"])
SWARM_SIZE = int(os.environ.get("SWARM_SIZE", "5"))
EW_TOKEN = os.environ.get("EW_TOKEN", "")
MESH_NET = ipaddress.ip_network(os.environ.get("MESH_CIDR", "172.31.0.0/24"))
C2_NET = ipaddress.ip_network(os.environ.get("C2_CIDR", "172.32.0.0/24"))
SIM_NET = ipaddress.ip_network(os.environ.get("SIM_CIDR", "172.30.0.0/24"))
MAV_ENDPOINT = os.environ.get("EW_MAVLINK", "udpout:127.0.0.1:14600")
ONBOARD_PORT = 14540 + (DRONE_ID - 1)
GCS_PORT = 14550


class EwError(Exception):
    pass


def sh(cmd: list[str], check: bool = True, stdin: str | None = None) -> subprocess.CompletedProcess:
    log.info("exec: %s", " ".join(cmd))
    r = subprocess.run(cmd, input=stdin, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise EwError(f"{' '.join(cmd)} -> rc={r.returncode}: {r.stderr.strip()}")
    return r


def iface_for(net: ipaddress.IPv4Network) -> str:
    out = sh(["ip", "-j", "-4", "addr", "show"]).stdout
    for link in json.loads(out):
        for a in link.get("addr_info", []):
            if ipaddress.ip_address(a["local"]) in net:
                return link["ifname"]
    raise EwError(f"interface for {net} not found")


IF = {"mesh": iface_for(MESH_NET), "c2": iface_for(C2_NET), "sim": iface_for(SIM_NET)}
log.info("interfaces: %s", IF)


def peer_ip(pid: int) -> str:
    return str(MESH_NET.network_address + 10 + pid)   # drone K -> .(10+K)


lock = threading.RLock()
state = {
    "c2": {"active": False, "ports": [], "include_onboard": False, "since": None},
    "gps": {"active": False, "mode": "off", "eph_m": None, "since": None},
    "mesh": {"active": False, "profile": None, "since": None},
}

# ───────────────────────────── C2 (nftables) ─────────────────────────────

def _nft_rules(iface: str, ports: list[int]) -> tuple[str, str]:
    pset = "{ " + ", ".join(str(p) for p in ports) + " }"
    return (
        f'    iifname "{iface}" udp dport {pset} counter drop\n'
        f'    iifname "{iface}" udp sport {pset} counter drop\n'
    ), (
        f'    oifname "{iface}" udp dport {pset} counter drop\n'
        f'    oifname "{iface}" udp sport {pset} counter drop\n'
    )


def c2_cut(ports: list[int], include_onboard: bool) -> None:
    in_r, out_r = _nft_rules(IF["c2"], ports)
    if include_onboard:
        i2, o2 = _nft_rules(IF["sim"], [ONBOARD_PORT])
        in_r += i2
        out_r += o2
    script = (
        "table inet ew\n"
        "delete table inet ew\n"
        "table inet ew {\n"
        "  chain ew_in {\n    type filter hook input priority 0; policy accept;\n" + in_r + "  }\n"
        "  chain ew_out {\n    type filter hook output priority 0; policy accept;\n" + out_r + "  }\n"
        "}\n"
    )
    sh(["nft", "-f", "-"], stdin=script)
    state["c2"] = {"active": True, "ports": ports, "include_onboard": include_onboard, "since": time.time()}


def c2_restore() -> None:
    sh(["nft", "delete", "table", "inet", "ew"], check=False)
    state["c2"] = {"active": False, "ports": [], "include_onboard": False, "since": None}

# ───────────────────────────── Mesh (tc netem) ─────────────────────────────

class MeshProfile(BaseModel):
    loss_pct: float = Field(0, ge=0, le=100)
    loss_corr_pct: float = Field(25, ge=0, le=100)
    delay_ms: int = Field(0, ge=0, le=10000)
    jitter_ms: int = Field(0, ge=0, le=10000)
    rate_kbit: int | None = Field(None, ge=1)
    limit_pkts: int = Field(50, ge=1, le=10000)
    peers: list[int] | None = None


def netem_args(p: MeshProfile) -> list[str]:
    a = ["netem", "limit", str(p.limit_pkts)]
    if p.delay_ms or p.jitter_ms:
        a += ["delay", f"{p.delay_ms}ms"]
        if p.jitter_ms:
            a += [f"{p.jitter_ms}ms", "distribution", "normal"]
    if p.loss_pct > 0:
        a += ["loss", f"{p.loss_pct:g}%"]
        if p.loss_corr_pct > 0:
            a += [f"{p.loss_corr_pct:g}%"]
    if p.rate_kbit:
        a += ["rate", f"{p.rate_kbit}kbit"]
    return a


def nft_mesh_script(dev: str, p: MeshProfile, peer_ips: list[str] | None) -> tuple[str | None, list[str]]:
    """Эмуляция деградации mesh через nftables — для ядер без sch_netem/sch_prio (стандартное ядро WSL2).
    Умеет: равномерную потерю (numgen random), жёсткий обрыв, ограничение скорости (limit rate).
    Не умеет: задержку/джиттер, коррелированные (пакетные) потери — они возвращаются списком `unsupported`
    и попадают в state, чтобы профиль не выглядел точнее, чем он применён.
    peer_ips=None — вся mesh-интерфейс, иначе только трафик к/от перечисленных пиров.
    Возвращает (скрипт nft или None если нечего применять, unsupported)."""
    unsupported = []
    if p.delay_ms or p.jitter_ms:
        unsupported.append("delay/jitter")
    if 0 < p.loss_pct < 100 and p.loss_corr_pct > 0:
        unsupported.append("loss_corr (пакетные потери заменены равномерными)")
    to = "" if peer_ips is None else f"ip daddr {{ {', '.join(peer_ips)} }} "
    out_rules, in_rules = [], []
    if p.loss_pct >= 100:
        out_rules.append(f'oifname "{dev}" {to}counter drop')
        if peer_ips is not None:          # симметрично на приёме, как раньше: жёсткий разрыв с обеих сторон
            in_rules.append(f'iifname "{dev}" ip saddr {{ {", ".join(peer_ips)} }} counter drop')
    else:
        if p.loss_pct > 0:
            out_rules.append(f'oifname "{dev}" {to}numgen random mod 1000 < {round(p.loss_pct * 10)} counter drop')
        if p.rate_kbit:
            out_rules.append(f'oifname "{dev}" {to}limit rate over {p.rate_kbit * 125} bytes/second counter drop')
    if not out_rules and not in_rules:
        return None, unsupported

    def chain(name: str, hook: str, rules: list[str]) -> str:
        body = "".join(f"    {r}\n" for r in rules)
        return f"  chain {name} {{\n    type filter hook {hook} priority 0; policy accept;\n{body}  }}\n"

    chains = chain("ew_mesh_out", "output", out_rules)
    if in_rules:
        chains += chain("ew_mesh_in", "input", in_rules)
    return "table inet ew_mesh\ndelete table inet ew_mesh\ntable inet ew_mesh {\n" + chains + "}\n", unsupported


def mesh_apply(p: MeshProfile) -> None:
    dev = IF["mesh"]
    sh(["tc", "qdisc", "del", "dev", dev, "root"], check=False)
    sh(["nft", "delete", "table", "inet", "ew_mesh"], check=False)
    peer_ips = None
    if p.peers is not None:
        for pid in p.peers:
            if not (1 <= pid <= SWARM_SIZE) or pid == DRONE_ID:
                raise EwError(f"invalid peer id {pid}")
        peer_ips = [peer_ip(pid) for pid in p.peers]
    emulation, unsupported = "tc", []
    try:
        if p.peers is None:
            sh(["tc", "qdisc", "add", "dev", dev, "root"] + netem_args(p))
        else:
            # band 1 (1:1) — трафик без деградации; band k+1 — пир с ID=k
            sh(["tc", "qdisc", "add", "dev", dev, "root", "handle", "1:", "prio",
                "bands", str(SWARM_SIZE + 1), "priomap"] + ["0"] * 16)
            for pid in p.peers:
                band = pid + 1
                sh(["tc", "qdisc", "add", "dev", dev, "parent", f"1:{band}", "handle", f"{10 + pid}:"]
                   + netem_args(p))
                sh(["tc", "filter", "add", "dev", dev, "parent", "1:", "protocol", "ip", "prio", "1",
                    "u32", "match", "ip", "dst", f"{peer_ip(pid)}/32", "flowid", f"1:{band}"])
    except EwError as e:
        log.warning("tc netem/prio failed (%s), fallback to nftables (ядро без netem, например WSL2)", e)
        sh(["tc", "qdisc", "del", "dev", dev, "root"], check=False)      # убрать частично созданное
        script, unsupported = nft_mesh_script(dev, p, peer_ips)
        if script is None:
            # раньше здесь молча ничего не применялось, а state говорил active=True — это ложный успех
            raise EwError(f"ядро без netem, профиль нельзя эмулировать через nftables "
                          f"(нужны: {', '.join(unsupported) or 'loss/rate'}): {e}")
        sh(["nft", "-f", "-"], stdin=script)
        emulation = "nftables"
        if unsupported:
            log.warning("nftables-эмуляция не воспроизводит: %s", ", ".join(unsupported))
    state["mesh"] = {"active": True, "profile": {**p.model_dump(), "emulation": emulation, "unsupported": unsupported},
                     "since": time.time()}


def mesh_restore() -> None:
    sh(["tc", "qdisc", "del", "dev", IF["mesh"], "root"], check=False)
    sh(["nft", "delete", "table", "inet", "ew_mesh"], check=False)
    state["mesh"] = {"active": False, "profile": None, "since": None}

# ───────────────────────────── GNSS (PX4 params via MAVLink) ─────────────────────────────

_master = None
_ramp_stop = threading.Event()


def _mav():
    global _master
    if _master is None:
        m = mavutil.mavlink_connection(MAV_ENDPOINT, source_system=250, source_component=191)
        m.mav.heartbeat_send(mavutil.mavlink.MAV_TYPE_GCS, mavutil.mavlink.MAV_AUTOPILOT_INVALID, 0, 0, 0)
        if m.wait_heartbeat(timeout=5) is None:
            raise EwError("no heartbeat from FC via mavlink-router")
        _master = m
    return _master


def set_param(name: str, value: float, ptype: str) -> None:
    """PX4 принимает INT32 побайтово (bytewise), REAL32 как float."""
    m = _mav()
    if ptype == "int32":
        raw = struct.unpack("<f", struct.pack("<i", int(value)))[0]
        t = mavutil.mavlink.MAV_PARAM_TYPE_INT32
    else:
        raw = float(value)
        t = mavutil.mavlink.MAV_PARAM_TYPE_REAL32
    for _ in range(3):
        m.mav.param_set_send(DRONE_ID, 1, name.encode(), raw, t)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            msg = m.recv_match(type="PARAM_VALUE", blocking=True, timeout=0.5)
            if msg is None:
                continue
            pid = msg.param_id if isinstance(msg.param_id, str) else msg.param_id.decode()
            if pid.rstrip("\x00") != name:
                continue
            got = (struct.unpack("<i", struct.pack("<f", msg.param_value))[0]
                   if ptype == "int32" else msg.param_value)
            if abs(got - value) < 1e-3:
                return
    raise EwError(f"PARAM_SET {name}={value} not confirmed (is the PX4 GPS-jam patch applied?)")


def _stop_ramp() -> None:
    _ramp_stop.set()


def _ramp(stop: threading.Event, a: float, b: float, secs: float) -> None:
    t0 = time.monotonic()
    while not stop.wait(1.0):
        f = min(1.0, (time.monotonic() - t0) / secs)
        try:
            set_param("SIM_GPS_JAM_EPH", a + (b - a) * f, "float")
            state["gps"]["eph_m"] = round(a + (b - a) * f, 2)
        except EwError as e:
            log.warning("ramp: %s", e)
        if f >= 1.0:
            return


class GpsReq(BaseModel):
    mode: Literal["denied", "degraded"] = "denied"
    eph_start_m: float = Field(5.0, ge=0.5, le=10000)
    eph_end_m: float = Field(60.0, ge=0.5, le=10000)
    ramp_s: float = Field(30.0, ge=0)


def gps_apply(r: GpsReq) -> None:
    global _ramp_stop
    _stop_ramp()
    if r.mode == "denied":
        set_param("SIM_GPS_JAM", 1, "int32")
        state["gps"] = {"active": True, "mode": "denied", "eph_m": None, "since": time.time()}
        return
    set_param("SIM_GPS_JAM_EPH", r.eph_start_m, "float")
    set_param("SIM_GPS_JAM", 2, "int32")
    state["gps"] = {"active": True, "mode": "degraded", "eph_m": r.eph_start_m, "since": time.time()}
    if r.ramp_s > 0 and r.eph_end_m != r.eph_start_m:
        _ramp_stop = threading.Event()
        threading.Thread(target=_ramp, args=(_ramp_stop, r.eph_start_m, r.eph_end_m, r.ramp_s),
                         daemon=True).start()


def gps_restore() -> None:
    _stop_ramp()
    set_param("SIM_GPS_JAM", 0, "int32")
    state["gps"] = {"active": False, "mode": "off", "eph_m": None, "since": None}


def reset_all(tolerate_gps: bool = False) -> None:
    c2_restore()
    mesh_restore()
    try:
        gps_restore()
    except EwError as e:
        if not tolerate_gps:
            raise
        log.warning("gps reset skipped at startup: %s", e)

# ───────────────────────────── HTTP API ─────────────────────────────

def auth(authorization: str = Header(default="")):
    if EW_TOKEN and authorization != f"Bearer {EW_TOKEN}":
        raise HTTPException(401, "bad token")


app = FastAPI(title=f"ew-agent-{DRONE_ID}")
router = APIRouter(dependencies=[Depends(auth)])


@app.exception_handler(EwError)
async def ew_error(_: Request, exc: EwError):
    return JSONResponse(status_code=500, content={"error": str(exc)})


class C2Req(BaseModel):
    ports: list[int] = [GCS_PORT]
    include_onboard: bool = False


@app.get("/healthz")
def healthz():
    return {"ok": True, "drone": DRONE_ID}


@router.get("/v1/status")
def status():
    with lock:
        return {
            "drone": DRONE_ID, "ifaces": IF, **state,
            "tc": sh(["tc", "qdisc", "show", "dev", IF["mesh"]], check=False).stdout,
            "nft": sh(["nft", "list", "ruleset"], check=False).stdout,
        }


@router.post("/v1/c2")
def post_c2(r: C2Req):
    with lock:
        c2_cut(r.ports, r.include_onboard)
        return state["c2"]


@router.delete("/v1/c2")
def del_c2():
    with lock:
        c2_restore()
        return state["c2"]


@router.post("/v1/mesh")
def post_mesh(p: MeshProfile):
    with lock:
        mesh_apply(p)
        return state["mesh"]


@router.delete("/v1/mesh")
def del_mesh():
    with lock:
        mesh_restore()
        return state["mesh"]


@router.post("/v1/gps")
def post_gps(r: GpsReq):
    with lock:
        gps_apply(r)
        return state["gps"]


@router.delete("/v1/gps")
def del_gps():
    with lock:
        gps_restore()
        return state["gps"]


@router.post("/v1/reset")
def post_reset():
    with lock:
        reset_all()
        return state


app.include_router(router)


@app.on_event("startup")
def on_startup():
    with lock:
        reset_all(tolerate_gps=True)
