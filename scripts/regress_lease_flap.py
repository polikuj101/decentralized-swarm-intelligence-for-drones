#!/usr/bin/env python3
"""regress_lease_flap.py — держит L3 (80% потерь) НЕПРЕРЫВНО 3 минуты (без split/merge-перехода) и считает
число переустановок Zenoh-сессии (zenoh_peers падает до 0 и восстанавливается) на каждом борту.
Запуск: python3 scripts/regress_lease_flap.py --duration 180
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


def collect_zenoh_peers(service, seconds):
    """подписка на /swarm/local/state, извлекает mesh.zenoh_peers (из telemetry/s1 состояния координатора) —
    считает число переходов 0 -> >0 (переустановка сессии) за время наблюдения"""
    code = f"""
import rclpy, json, time
from rclpy.node import Node
from std_msgs.msg import String
rclpy.init()
n = Node('zenoh_flap_probe')
samples = []
def cb(m):
    try:
        st = json.loads(m.data)
    except json.JSONDecodeError:
        return
    samples.append({{'t': time.time(), 'zenoh_peers': (st.get('mesh') or {{}}).get('zenoh_peers')}})
n.create_subscription(String, '/swarm/local/state', cb, 10)
t0 = time.time()
while time.time() - t0 < {seconds}:
    rclpy.spin_once(n, timeout_sec=0.3)
print(json.dumps(samples))
"""
    r = rr(service, "python3", "-c", code, timeout=seconds + 15)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return None           # зонд не отработал — это не «0 флапов"


def samples_usable(samples, min_samples) -> bool:
    """Зонд вернул достаточно образцов, и в них есть числовое mesh.zenoh_peers (иначе 0 флапов ничего не доказывает)."""
    return (isinstance(samples, list) and len(samples) >= min_samples
            and any(isinstance(s.get("zenoh_peers"), (int, float)) and s["zenoh_peers"] > 0 for s in samples))


def count_flaps(samples):
    """флап = переход от 0 (или None) к значению > 0, после того как ранее уже было > 0 хотя бы раз
    (т.е. не считаем самый первый подъём при старте, только РАЗРЫВЫ и переустановки)"""
    flaps, was_up, ever_up = 0, False, False
    for s in samples:
        zp = s.get("zenoh_peers")
        up = isinstance(zp, (int, float)) and zp > 0
        if up and not was_up and ever_up:
            flaps += 1
        if up:
            ever_up = True
        was_up = up
    return flaps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=int, default=180)
    a = ap.parse_args()

    print("=== Непрерывный L3 (без split/merge), наблюдение zenoh_peers на всех бортах ===", flush=True)
    reset_stand(N_DEFAULT)
    ew_errors = ew_agent_errors(sh(["./scripts/ewctl.sh", "mesh", "L3", "all", str(a.duration + 10)]))

    import threading
    probes = {}

    def _collect(i):
        probes[i] = collect_zenoh_peers(f"drone-agent-{i}", a.duration)

    threads = [threading.Thread(target=_collect, args=(i,)) for i in range(1, N_DEFAULT + 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    sh(["./scripts/ewctl.sh", "reset"])

    usable = all(samples_usable(probes.get(i), max(1, a.duration)) for i in range(1, N_DEFAULT + 1))
    per_drone = {i: count_flaps(samples or []) for i, samples in probes.items()}
    total_flaps = sum(per_drone.values())

    out = ROOT / "logs" / "verify" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "lease_flap.json").write_text(json.dumps({"per_drone_flaps": per_drone, "raw_samples": probes,
                                                     "probes_usable": usable, "ew_agent_errors": ew_errors}, indent=2))

    print(json.dumps({"per_drone_flaps": per_drone, "total_flaps": total_flaps}, indent=2))
    # Порог: при lease=60000мс и keep_alive=20 ожидалось 0 переустановок за 3 минуты устойчивого L3 (п. 2.3 U9,
    # 4.6.3). При lease=4000мс любое количество > 0 указывает, что сессия не переживает L3 целиком и "быстрая
    # конвергенция" Вектора 1 вероятно объясняется переустановкой сессии, а не быстрым CBAA.
    if not usable or ew_errors:
        print(f"FAIL: измерение недействительно (зонды пригодны: {usable}; ошибки применения L3: {sorted(set(ew_errors))[:2] or 'нет'})")
        sys.exit(1)
    ok = total_flaps == 0
    print(f"\n{'PASS' if ok else 'FAIL'}: "
          f"{'сессии Zenoh переживают устойчивый L3 без переустановок, lease=4000ms безопасен' if ok else f'{total_flaps} переустановок сессии за {a.duration}с устойчивого L3 — lease=4000ms слишком короткий для L3 (80% потерь); вернуть к lease=60000ms/keep_alive=20 (§4.6.3) и пересчитать Вектор 1 заново'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
