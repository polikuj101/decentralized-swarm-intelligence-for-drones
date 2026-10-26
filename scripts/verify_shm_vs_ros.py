#!/usr/bin/env python3
"""verify_shm_vs_ros.py — параллельно меряет сходимость/failover через оба пути (shm и ROS2 subscription)
и сравнивает. Если |Δt| > 0.5s систематически — shm-путь не заменяет ROS2 для метрик вектора 1/2.
Запуск: python3 scripts/verify_shm_vs_ros.py --scenario merge|failover --runs 5
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, "scripts")
from stress_test import reset_stand, sh, dcx, rr, N_DEFAULT  # noqa: E402

def ew_token():
    """Bearer для изменяющих методов ew-controller: EW_TOKEN из окружения или из .env в корне репозитория."""
    tok = os.environ.get("EW_TOKEN")
    if tok:
        return tok
    env = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
    try:
        for line in open(env, encoding="utf-8"):
            if line.startswith("EW_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return ""



def read_shm(service):
    r = dcx(service, "cat", "/dev/shm/swarm_state.json")
    try:
        return json.loads(r.stdout), time.time()
    except json.JSONDecodeError:
        return None, time.time()


def read_ros(service, timeout=8):
    """оригинальный путь: короткоживущая подписка на /swarm/local/state (как было ДО оптимизации)"""
    code = ("import rclpy,time,json;from rclpy.node import Node;from std_msgs.msg import String;"
            "rclpy.init();n=Node('verify_probe');h={}\n"
            "def cb(m): h['v']=m.data; h['t']=time.time()\n"
            "n.create_subscription(String,'/swarm/local/state',cb,10)\n"
            "t0=time.time()\n"
            "while 'v' not in h and time.time()-t0<%f: rclpy.spin_once(n,timeout_sec=0.2)\n"
            "print(json.dumps({'v': h.get('v','{}'), 't': h.get('t', time.time())}))\n"
            "try:\n    n.destroy_node()\n    rclpy.shutdown()\nexcept Exception:\n    pass") % timeout
    r = rr(service, "python3", "-c", code, timeout=timeout + 3)
    try:
        out = json.loads(r.stdout.strip().splitlines()[-1])
        return json.loads(out["v"]), out["t"]
    except (json.JSONDecodeError, IndexError, KeyError):
        return None, time.time()


def first_convergence(service, probe_fn, deadline_s=40, poll=0.3):
    """возвращает (t_relative, epoch, dup_count) при первом наблюдении единой эпохи без дублей среди всех бортов"""
    t0 = time.time()
    while time.time() - t0 < deadline_s:
        states = {}
        for i in range(1, N_DEFAULT + 1):
            st, _ts = probe_fn(f"drone-agent-{i}")
            if st:
                states[i] = st
        if len(states) == N_DEFAULT:
            epochs = {s.get("epoch") for s in states.values()}
            seen, dup = {}, 0
            for s in states.values():
                for task, who in (s.get("winners") or {}).items():
                    if task in seen and seen[task] != who:
                        dup += 1
                    seen[task] = who
            if len(epochs) == 1 and dup == 0:
                return round(time.time() - t0, 2), list(epochs)[0], dup
        time.sleep(poll)
    return None, None, None


def run_merge_scenario():
    sh(["./scripts/ewctl.sh", "reset"])
    groupA, groupB = [1, 2, 3], [4, 5]
    for a in groupA:
        sh(["curl", "-fsS", "-X", "POST", "-H", f"Authorization: Bearer {ew_token()}", "http://127.0.0.1:8080/v1/ew/mesh",
            "-H", "Content-Type: application/json",
            "-d", json.dumps({"level": "L4", "drones": [a], "peers": groupB})])
    time.sleep(20)
    sh(["./scripts/ewctl.sh", "reset"])
    t_shm, e_shm, d_shm = first_convergence("drone-agent-1", lambda s: read_shm(s))
    return t_shm, e_shm, d_shm


def run_merge_scenario_ros_only():
    """повтор того же сценария, но измерение идёт только через ROS2-путь (отдельный прогон, т.к. нельзя мерить
    оба пути синхронно на одном and том же переходном процессе без взаимных помех по времени)"""
    sh(["./scripts/ewctl.sh", "reset"])
    groupA, groupB = [1, 2, 3], [4, 5]
    for a in groupA:
        sh(["curl", "-fsS", "-X", "POST", "-H", f"Authorization: Bearer {ew_token()}", "http://127.0.0.1:8080/v1/ew/mesh",
            "-H", "Content-Type: application/json",
            "-d", json.dumps({"level": "L4", "drones": [a], "peers": groupB})])
    time.sleep(20)
    sh(["./scripts/ewctl.sh", "reset"])
    t_ros, e_ros, d_ros = first_convergence("drone-agent-1", lambda s: read_ros(s))
    return t_ros, e_ros, d_ros


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", choices=["merge"], default="merge")     # failover раньше принимался, но не запускался
    # NB: shm и ROS меряются в разных прогонах, поэтому порог 0.5 с сравним с разбросом самой сходимости; см. verify_ground_truth.py
    ap.add_argument("--runs", type=int, default=5)
    a = ap.parse_args()

    results = []
    for i in range(a.runs):
        print(f"\n--- прогон {i+1}/{a.runs} (shm) ---", flush=True)
        reset_stand(N_DEFAULT)
        t_shm, e_shm, d_shm = run_merge_scenario()
        print(f"shm: t={t_shm} epoch={e_shm} dup={d_shm}")

        print(f"--- прогон {i+1}/{a.runs} (ROS2 subscription, оригинальный путь) ---", flush=True)
        reset_stand(N_DEFAULT)
        t_ros, e_ros, d_ros = run_merge_scenario_ros_only()
        print(f"ros: t={t_ros} epoch={e_ros} dup={d_ros}")

        results.append({"run": i, "t_shm": t_shm, "t_ros": t_ros,
                        "delta": None if (t_shm is None or t_ros is None) else round(t_ros - t_shm, 2)})

    out = ROOT / "logs" / "verify" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "shm_vs_ros.json").write_text(json.dumps(results, indent=2))

    deltas = [r["delta"] for r in results if r["delta"] is not None]
    print("\n=== СВОДКА ===")
    print(json.dumps(results, indent=2))
    if not deltas:
        print("ОШИБКА: ни один прогон не дал валидных измерений с обеих сторон — расследовать отдельно")
        sys.exit(2)
    avg = sum(deltas) / len(deltas)
    mx = max(abs(d) for d in deltas)
    print(f"среднее расхождение (ROS - shm): {avg:+.2f}s, максимум по модулю: {mx:.2f}s")
    # Порог: если shm систематически занижает время сходимости больше чем на 0.5с — числа вектора 1/2 недостоверны
    if mx > 0.5:
        print("ВЕРДИКТ: FAIL — shm-путь расходится с ROS2-путём больше чем на 0.5с. "
              "Вектор 1/2 должен быть пересчитан на ROS2-пути до принятия результата.")
        sys.exit(1)
    print("ВЕРДИКТ: PASS — shm-путь согласован с ROS2-путём, числа вектора 1/2 можно доверять.")


if __name__ == "__main__":
    main()
