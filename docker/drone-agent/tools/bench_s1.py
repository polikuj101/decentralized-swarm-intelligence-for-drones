#!/usr/bin/env python3
"""bench_s1.py --n 500 [--mode hybrid]: p50/p95/p99 мс для rules, hybrid(кэш), холодного эмбеддинга; RSS процесса."""
import argparse
import json
import os
import random
import time

import numpy as np

from swarm.candidates import enumerate_candidates
from swarm.features import build_obs
from swarm.matcher import Matcher
from swarm.phase import PhaseTracker
from swarm.synth import synth_state

ap = argparse.ArgumentParser()
ap.add_argument("--n", type=int, default=500)
a = ap.parse_args()
rng = random.Random(7)
states = [synth_state(rng) for _ in range(a.n)]


def pct(x):
    x = np.asarray(x)
    return {f"p{q}": round(float(np.percentile(x, q)), 2) for q in (50, 95, 99)}


def run(m, st_list):
    out = []
    tr = PhaseTracker()
    for st in st_list:
        t0 = time.perf_counter()
        o = build_obs(st)
        ph = tr.update(o, st["t"])
        m.decide(enumerate_candidates(st, o, ph, "S1"), o, ph, 1e9 + st["t"])
        out.append((time.perf_counter() - t0) * 1000)
        m.until.clear()
    return out


res = {"n": a.n, "rules_ms": pct(run(Matcher("rules"), states))}
try:
    from swarm.embedder import Embedder, load_or_build_protos
    d = os.environ.get("S1_EMBED_DIR", "/opt/models/embed")
    e = Embedder(d, dim=int(os.environ.get("S1_EMBED_DIM", "128")))
    protos = load_or_build_protos(e, os.path.join(d, "protos.npz"))
    texts = sorted({build_obs(s).text(__import__("swarm.actions", fromlist=["Phase"]).Phase.NOMINAL) for s in states})[:200]
    cold = []
    for t in texts:
        t0 = time.perf_counter()
        e.encode(t)
        cold.append((time.perf_counter() - t0) * 1000)
    m = Matcher("hybrid", e, protos)
    run(m, states)
    time.sleep(5)
    res.update(embed_cold_ms=pct(cold), hybrid_cached_ms=pct(run(m, states)), cache=len(m.cache))
except Exception as ex:  # noqa: BLE001
    res["embed_error"] = repr(ex)

try:
    res["rss_mb"] = round(int([l for l in open("/proc/self/status") if l.startswith("VmRSS")][0].split()[1]) / 1024, 1)
except Exception:
    res["rss_mb"] = None

print(json.dumps(res, indent=2))
