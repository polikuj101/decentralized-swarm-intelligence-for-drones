#!/usr/bin/env python3
"""gps-jam-bridge: ретранслятор gz.msgs.NavSat между сырым топиком сенсора и топиком PX4.
Режимы на борт: pass | noise | drift | spoof. HTTP API на :9100 (mgmt-net)."""
import hmac
import logging
import math
import os
import random
import threading
import time
from typing import Literal

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from gz.msgs10.navsat_pb2 import NavSat
from gz.transport13 import Node
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s gps-bridge %(message)s")
log = logging.getLogger("gps-bridge")

WORLD = os.environ.get("GZ_WORLD", "swarm_world")
N = int(os.environ.get("SWARM_SIZE", "5"))
RAW_T = "/swarm/{n}/navsat_raw"
OUT_T = "/world/{w}/model/x500_swarm_{n}/link/base_link/sensor/navsat_sensor/navsat"
M_PER_DEG_LAT = 111_320.0
ADMIN_TOKEN = os.environ.get("EW_ADMIN_TOKEN", "")   # операторский токен (ew-controller), не тот, что у бортов


class Chan:
    def __init__(self, n: int, node: Node):
        self.n = n
        self.lock = threading.Lock()
        self.mode = "pass"
        self.rate = 1.0          # м/с (drift, spoof)
        self.heading = 45.0      # град (drift): 0 = север, 90 = восток
        self.sigma = 2.0         # м (noise; для drift — шаг случайного блуждания)
        self.tgt_n = 0.0         # м (spoof)
        self.tgt_e = 0.0
        self.b_n = 0.0           # накопленное смещение, м
        self.b_e = 0.0
        self.last = None
        self.pub = node.advertise(OUT_T.format(w=WORLD, n=n), NavSat)
        if not node.subscribe(NavSat, RAW_T.format(n=n), self.on_msg):
            raise RuntimeError(f"subscribe failed for drone {n}")

    def on_msg(self, msg: NavSat) -> None:
        now = time.monotonic()
        with self.lock:
            dt = 0.0 if self.last is None else min(now - self.last, 1.0)
            self.last = now
            if self.mode == "pass":
                self.b_n = self.b_e = 0.0
                off_n = off_e = 0.0
            elif self.mode == "noise":
                self.b_n = self.b_e = 0.0
                off_n, off_e = random.gauss(0, self.sigma), random.gauss(0, self.sigma)
            elif self.mode == "drift":
                h = math.radians(self.heading)
                self.b_n += self.rate * dt * math.cos(h) + random.gauss(0, self.sigma) * math.sqrt(dt)
                self.b_e += self.rate * dt * math.sin(h) + random.gauss(0, self.sigma) * math.sqrt(dt)
                off_n, off_e = self.b_n, self.b_e
            else:  # spoof: линейный увод к цели
                dn, de = self.tgt_n - self.b_n, self.tgt_e - self.b_e
                dist = math.hypot(dn, de)
                step = min(dist, self.rate * dt)
                if dist > 1e-6:
                    self.b_n += dn / dist * step
                    self.b_e += de / dist * step
                off_n, off_e = self.b_n, self.b_e
        out = NavSat()
        out.CopyFrom(msg)
        out.latitude_deg = msg.latitude_deg + off_n / M_PER_DEG_LAT
        out.longitude_deg = msg.longitude_deg + off_e / (
            M_PER_DEG_LAT * max(0.01, math.cos(math.radians(msg.latitude_deg))))
        self.pub.publish(out)

    def info(self) -> dict:
        with self.lock:
            return {"mode": self.mode, "rate_mps": self.rate, "heading_deg": self.heading,
                    "sigma_m": self.sigma, "bias_north_m": round(self.b_n, 2),
                    "bias_east_m": round(self.b_e, 2)}


node = Node()
chans = {n: Chan(n, node) for n in range(1, N + 1)}
app = FastAPI(title="gps-jam-bridge")


class Req(BaseModel):
    drones: list[int] | None = None
    mode: Literal["pass", "noise", "drift", "spoof"] = "pass"
    rate_mps: float = Field(1.0, ge=0, le=100)
    heading_deg: float = 45.0
    sigma_m: float = Field(2.0, ge=0, le=1000)
    target_north_m: float = 0.0
    target_east_m: float = 0.0


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/v1/gps")
def get_gps():
    return {str(n): c.info() for n, c in chans.items()}


@app.post("/v1/gps")
def post_gps(r: Req, authorization: str = Header(default="")):
    if not ADMIN_TOKEN:
        raise HTTPException(503, "EW_ADMIN_TOKEN not configured")
    if not hmac.compare_digest(authorization.encode(), f"Bearer {ADMIN_TOKEN}".encode()):
        raise HTTPException(401, "bad token")
    ids = r.drones or list(chans)
    for n in ids:
        c = chans[n]
        with c.lock:
            c.mode, c.rate, c.heading, c.sigma = r.mode, r.rate_mps, r.heading_deg, r.sigma_m
            c.tgt_n, c.tgt_e = r.target_north_m, r.target_east_m
    log.info("drones=%s mode=%s", ids, r.mode)
    return get_gps()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9100, log_level="info")
