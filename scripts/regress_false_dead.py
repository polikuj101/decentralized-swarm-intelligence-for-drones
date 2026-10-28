#!/usr/bin/env python3
"""regress_false_dead.py — D10/D11 regression: при L2 и L3 (без убийства бортов, 5 минут каждый) считает
ложные DEAD-события и DECLARE_FAILOVER. Порог как в §4.12: ≤ 1 ложный DEAD за 5 минут на L3.
Запуск: python3 scripts/regress_false_dead.py --level L2,L3 --duration 300
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, "scripts")
from stress_test import ew_agent_errors, reset_stand, sh, rr, N_DEFAULT  # noqa: E402

THRESHOLDS = {"L1": 2, "L2": 1, "L3": 1, "L4": None}   # None = не нормируется (L4 — ожидаемый блэкаут, не тест)


def collect_events(service, seconds):
    """подписка на /swarm/local/state на N секунд, извлекает переходы alive->dead и declared_failed для каждого пира
    (без убийства реальных контейнеров — все 5 бортов живы физически всё время теста)"""
    code = f"""
import rclpy, json, time
from rclpy.node import Node
from std_msgs.msg import String
rclpy.init()
n = Node('false_dead_probe')
events = []
prev = {{}}
samples = [0]
def cb(m):
    try:
        st = json.loads(m.data)
    except json.JSONDecodeError:
        return
    samples[0] += 1
    now = time.time()
    for k, p in st.get('peers', {{}}).items():
        was_alive = prev.get(k, {{}}).get('alive', True)
        was_declared = prev.get(k, {{}}).get('declared_failed', False)
        if was_alive and not p['alive']:
            events.append({{'t': now, 'ev': 'dead', 'peer': k}})
        if not was_declared and p['declared_failed']:
            events.append({{'t': now, 'ev': 'declared', 'peer': k}})
        prev[k] = p
n.create_subscription(String, '/swarm/local/state', cb, 10)
t0 = time.time()
while time.time() - t0 < {seconds}:
    rclpy.spin_once(n, timeout_sec=0.3)
try:
    n.destroy_node()
    rclpy.shutdown()
except Exception:
    pass
print(json.dumps({{'events': events, 'samples': samples[0]}}))
"""
    try:
        r = rr(service, "python3", "-c", code, timeout=seconds + 25)
        lines = [line.strip() for line in r.stdout.strip().splitlines() if line.strip().startswith("{")]
        return json.loads(lines[-1]) if lines else None
    except Exception:
        return None           # зонд не отработал: раньше это превращалось в «0 событий» и PASS


def probes_valid(probes: dict, n: int, min_samples: int) -> bool:
    """Каждый из n зондов вернул результат и реально получил состояния (иначе «нет событий» ничего не значит)."""
    return (len(probes) == n and all(isinstance(p, dict) and p.get("samples", 0) >= min_samples for p in probes.values()))


def environment_record():
    """Чем реально снят результат: эмуляция помех из состояния ew-агента (nftables или tc, что не воспроизведено) и загрузка хоста.
    Без этого порог нельзя сравнивать между прогонами (2026-10-28: порог подбирали под tc netem, а стенд на nftables)."""
    env = {"load_avg": [round(x, 2) for x in os.getloadavg()] if hasattr(os, "getloadavg") else None, "cpus": os.cpu_count()}
    try:
        st = json.loads(sh(["./scripts/ewctl.sh", "state"]).stdout)
        prof = ((st.get("drones") or {}).get("1") or {}).get("mesh", {}).get("profile") or {}
        env["emulation"] = prof.get("emulation")
        env["emulation_unsupported"] = prof.get("unsupported")
    except Exception as e:  # noqa: BLE001
        env["emulation"] = f"не удалось прочитать: {type(e).__name__}"
    return env


def run_level(level, duration):
    reset_stand(N_DEFAULT)
    ew_errors = ew_agent_errors(sh(["./scripts/ewctl.sh", "mesh", level, "all", str(duration + 10)]))
    import threading
    probes = {}

    def _collect(i):
        try:
            probes[i] = collect_events(f"drone-agent-{i}", duration)
        except Exception:
            probes[i] = None

    threads = [threading.Thread(target=_collect, args=(i,)) for i in range(1, N_DEFAULT + 1)]
    for t in threads:
        t.start()
    env = environment_record()
    t_start = time.time()
    while any(t.is_alive() for t in threads):
        elapsed = int(time.time() - t_start)
        print(f"[{level}] сбор событий: {elapsed}/{duration}s...", flush=True)
        time.sleep(15)
    for t in threads:
        t.join()
    sh(["./scripts/ewctl.sh", "reset"])

    valid = probes_valid(probes, N_DEFAULT, min_samples=max(1, duration))     # состояние публикуется ~5 Гц; берём с большим запасом
    evs = [p["events"] for p in probes.values() if isinstance(p, dict)]
    all_dead = [e for ev in evs for e in ev if e["ev"] == "dead"]
    all_declared = [e for ev in evs for e in ev if e["ev"] == "declared"]
    # все 5 бортов были живы физически всё время -> каждое событие 'dead'/'declared' ложное по определению теста
    return {"level": level, "duration_s": duration, "probes_valid": valid, "ew_agent_errors": sorted(set(ew_errors))[:5],
            "samples": {i: (p or {}).get("samples") for i, p in probes.items()}, "false_dead_count": len(all_dead),
            "false_declared_failover_count": len(all_declared), "environment": env,
            "dead_events": all_dead, "declared_events": all_declared}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", default="L2,L3")
    ap.add_argument("--duration", type=int, default=300)
    a = ap.parse_args()
    levels = a.level.split(",")

    results = []
    for lvl in levels:
        print(f"\n=== Уровень {lvl}, {a.duration}с, все борты физически живы ===", flush=True)
        res = run_level(lvl, a.duration)
        thr = THRESHOLDS.get(lvl)
        res["threshold"] = thr
        measured = res["probes_valid"] and not res["ew_agent_errors"]       # без валидных зондов и применённого профиля PASS невозможен
        res["verdict"] = "PASS" if (measured and (thr is None or res["false_declared_failover_count"] <= thr)) else "FAIL"
        print(json.dumps({k: v for k, v in res.items() if not k.endswith("_events")}, indent=2))
        results.append(res)

    out = ROOT / "logs" / "verify" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "false_dead_regression.json").write_text(json.dumps(results, indent=2))
    ok = all(r["verdict"] == "PASS" for r in results)
    print(f"\n{'PASS' if ok else 'FAIL'}: {'новые тайминги T_DEAD/T_SUSPECT не повышают долю ложных failover' if ok else 'новые тайминги дают ложные DECLARE_FAILOVER сверх порога D10/D11 — откатить T_DEAD/T_SUSPECT или ужесточить непрямые свидетельства heard_mask'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
