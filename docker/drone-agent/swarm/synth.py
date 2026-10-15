"""swarm/synth.py — генератор правдоподобных состояний роя для тестов и бенчмарков."""
from __future__ import annotations

import random


def synth_state(rng: random.Random | None = None) -> dict:
    if rng is None:
        rng = random.Random()
    me = rng.randint(1, 5)
    uptime = rng.uniform(5.0, 300.0)
    epoch = rng.randint(0, 10)
    pos = [rng.uniform(-50.0, 50.0), rng.uniform(-50.0, 50.0), rng.uniform(0.0, 20.0)]
    gps_fix = rng.choice([0, 1, 2, 3, 3, 3, 3])
    gps_suspect = rng.random() < 0.1
    sigma = rng.uniform(1.0, 45.0)
    link_q = rng.uniform(0.1, 1.0)
    total_tasks = 10
    done = sorted(rng.sample(range(total_tasks), rng.randint(0, 5)))
    open_tasks = [j for j in range(total_tasks) if j not in done]

    opn = []
    for j in open_tasks:
        kind = "TARGET" if j >= 6 else "SECTOR"
        name = f"T{j-6}" if kind == "TARGET" else f"S{j}"
        opn.append({
            "id": j,
            "name": name,
            "kind": kind,
            "cx": rng.uniform(-60.0, 60.0),
            "cy": rng.uniform(-60.0, 60.0),
            "area": rng.uniform(200.0, 800.0),
            "splittable": kind == "SECTOR" and rng.random() < 0.5,
        })

    alive_peers = [i for i in range(1, 6) if i != me and rng.random() > 0.2]
    alive = sorted(alive_peers + [me])
    peers = {}
    for i in range(1, 6):
        if i == me:
            continue
        is_alive = i in alive_peers
        declared = not is_alive and rng.random() < 0.3
        silent_s = rng.uniform(0.1, 2.0) if is_alive else rng.uniform(3.0, 30.0)
        peers[str(i)] = {
            "alive": is_alive,
            "declared_failed": declared,
            "silent_s": round(silent_s, 1),
            "q": round(rng.uniform(0.2, 1.0) if is_alive else 0.0, 2),
            "x": rng.uniform(-50.0, 50.0),
            "y": rng.uniform(-50.0, 50.0),
            "z": rng.uniform(0.0, 20.0),
            "task": rng.choice(open_tasks + [255]) if open_tasks else 255,
            "state": 2,
            "flags": 1,
            "epoch": epoch,
            "heard": sorted(rng.sample(range(1, 6), rng.randint(1, 5))),
        }

    my_task = rng.choice(open_tasks) if open_tasks and rng.random() < 0.8 else None
    task_name = f"S{my_task}" if my_task is not None and my_task < 6 else f"T{my_task-6}" if my_task is not None else None

    return {
        "t": round(uptime, 1),
        "me": me,
        "epoch": epoch,
        "pos": pos,
        "nav": {
            "sigma": sigma,
            "gps_fix": gps_fix,
            "gps_suspect": gps_suspect,
            "state": "MISSION",
            "seq": 1,
            "done": False,
        },
        "task": my_task,
        "task_name": task_name,
        "cbaa_x": my_task,
        "winners": {str(j): rng.choice(alive) for j in open_tasks if rng.random() < 0.7},
        "done": done,
        "total_tasks": total_tasks,
        "open": opn,
        "alive": alive,
        "link_q": round(link_q, 2),
        "periods": {"hb": 1.0, "claim": 1.0, "alloc": 2.0, "rate_Bps": 200.0},
        "heard": sorted(rng.sample(range(1, 6), rng.randint(1, 5))),
        "peers": peers,
        "events": [[uptime - 5.0, "event A"], [uptime - 1.0, "event B"]],
        "last_cmd": None,
        "override": "HOLD_SECTOR" if rng.random() < 0.1 else None,
        "withdrawn": rng.random() < 0.05,
        "mesh": {
            "ready": True,
            "tuned": True,
            "zenoh_peers": len(alive) - 1,
            "dropped": {},
            "bad_tag": 0,
            "tx_bytes": 1000,
        },
    }
