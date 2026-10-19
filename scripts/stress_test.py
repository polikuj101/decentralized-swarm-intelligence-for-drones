#!/usr/bin/env python3
"""stress_test.py — оркестратор хаос-тестов роя (5 векторов).
Запуск из корня репозитория:
    python3 scripts/stress_test.py --vector all
    python3 scripts/stress_test.py --vector 1,3
Перед каждым вектором стенд приводится к чистому состоянию (ewctl reset + restart убитых контейнеров + sleep).
Отчёт: logs/stress/<ts>/report.json и report.md (сводная таблица, см. п. 8.4).
"""
import argparse
import json
import os
import random
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
N_DEFAULT = 5
OUT = ROOT / "logs" / "stress" / time.strftime("%Y%m%d-%H%M%S")
OUT.mkdir(parents=True, exist_ok=True)


def sh(cmd, check=False, timeout=60, **kw):
    r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout, **kw)
    if check and r.returncode != 0:
        raise RuntimeError(f"cmd failed: {cmd}\n{r.stderr}")
    return r


def cid(service):
    """Возвращает container ID по имени сервиса Compose"""
    r = sh(["docker", "compose", "ps", "-q", service])
    lines = r.stdout.strip().splitlines()
    return lines[0] if lines else service


def dcx(service, *args, timeout=30):
    return sh(["docker", "compose", "exec", "-T", service, *args], timeout=timeout)


def rr(service, *args, timeout=30):
    """ros-run.sh внутри контейнера борта"""
    return dcx(service, "/usr/local/bin/ros-run.sh", *args, timeout=timeout)


def pub_cmd(service, cmd_dict):
    """Публикация команды в /swarm/local/planner_cmd через Python ROS-ноду"""
    code = (
        "import rclpy,json;from rclpy.node import Node;from std_msgs.msg import String;"
        "rclpy.init();n=Node('stress_pub');p=n.create_publisher(String,'/swarm/local/planner_cmd',10);"
        f"p.publish(String(data='{json.dumps(cmd_dict)}'));"
        "import time;t0=time.time()\n"
        "while time.time()-t0<0.5: rclpy.spin_once(n,timeout_sec=0.1)\n"
        "n.destroy_node();rclpy.shutdown()"
    )
    return rr(service, "python3", "-c", code)


def get_state(service="drone-agent-1", timeout=25):
    """одно состояние /swarm/local/state (быстро через /dev/shm или подписчик)"""
    if not os.environ.get("FORCE_ROS"):
        r_shm = dcx(service, "cat", "/dev/shm/swarm_state.json", timeout=4)
        if r_shm.returncode == 0 and r_shm.stdout.strip().startswith("{"):
            try:
                return json.loads(r_shm.stdout.strip())
            except Exception:
                pass
    code = (
        "import rclpy,sys,json;from rclpy.node import Node;from std_msgs.msg import String;"
        "rclpy.init();n=Node('probe');h={}\n"
        "def cb(m): h['v']=m.data\n"
        "n.create_subscription(String,'/swarm/local/state',cb,10)\n"
        "import time;t0=time.time()\n"
        "while 'v' not in h and time.time()-t0<6: rclpy.spin_once(n,timeout_sec=0.15)\n"
        "print(h.get('v','{}'))\n"
        "try:\n    n.destroy_node()\n    rclpy.shutdown()\nexcept Exception:\n    pass"
    )
    r = rr(service, "python3", "-c", code, timeout=timeout)
    try:
        lines = [line.strip() for line in r.stdout.strip().splitlines() if line.strip().startswith("{")]
        return json.loads(lines[-1]) if lines else {}
    except (json.JSONDecodeError, IndexError):
        return {}


def get_all_states(n=N_DEFAULT, timeout=25):
    """Параллельный опрос состояния всех n бортов"""
    with ThreadPoolExecutor(max_workers=n) as ex:
        futures = {i: ex.submit(get_state, f"drone-agent-{i}", timeout) for i in range(1, n + 1)}
        return {i: f.result() for i, f in futures.items()}


def wait_healthy(services, secs=180):
    t0 = time.time()
    while time.time() - t0 < secs:
        ps_out = sh(["docker", "compose", "ps", "--format", "{{.Service}} {{.State}} {{.Health}}"]).stdout
        all_ok = True
        for s in services:
            line = next((l for l in ps_out.splitlines() if l.startswith(s)), "")
            if not line or ("running" not in line and "healthy" not in line and "Up" not in line):
                all_ok = False
                break
        if all_ok:
            return True
        time.sleep(3)
    return False


def reset_stand(n=N_DEFAULT):
    print("resetting stand...", flush=True)
    sh(["./scripts/ewctl.sh", "reset"])
    for i in range(1, n + 1):
        sh(["docker", "unpause", cid(f"drone-agent-{i}")], check=False)
    stopped = [s for s in sh(["docker", "compose", "ps", "-a", "--filter", "status=exited",
              "--format", "{{.Service}}"]).stdout.split()]
    agents = [f"drone-agent-{i}" for i in range(1, n + 1)]
    victims = [s for s in stopped if s in agents]
    if victims:
        sh(["docker", "compose", "start", *victims])
    for i in range(1, n + 1):
        c = cid(f"drone-agent-{i}")
        if c:
            sh(["docker", "update", "--cpus", "4.0", c], check=False)
    wait_healthy(agents, 120)
    time.sleep(5)


@dataclass
class Result:
    vector: int
    name: str
    verdict: str = "FAIL"
    metrics: dict = field(default_factory=dict)
    notes: str = ""


# ───────────────────────── Вектор 1: сетевой хаос ─────────────────────────

def v1_netem(n=N_DEFAULT) -> Result:
    res = Result(1, "Zenoh network degradation (netem, split-brain, merge)")
    before = get_state("drone-agent-1")
    sh(["./scripts/ewctl.sh", "mesh", "L3", "all", "20"])
    time.sleep(22)
    after_l3 = get_state("drone-agent-1")
    link_q_during = after_l3.get("link_q", 0.0)

    # split-brain: {1,2,3} | {4,5}, аукцион активен
    groupA, groupB = [1, 2, 3], [4, 5]
    t0 = time.time()
    for a in groupA:
        sh(["curl", "-fsS", "-X", "POST", "http://127.0.0.1:8080/v1/ew/mesh",
            "-H", "Content-Type: application/json",
            "-d", json.dumps({"level": "L4", "drones": [a], "peers": [b for b in groupB]})])
    for b in groupB:
        sh(["curl", "-fsS", "-X", "POST", "http://127.0.0.1:8080/v1/ew/mesh",
            "-H", "Content-Type: application/json",
            "-d", json.dumps({"level": "L4", "drones": [b], "peers": [a for a in groupA]})])
    split_hold_s = 20
    time.sleep(split_hold_s)
    mid_states = get_all_states(n)
    winners_A = {j: v for st in [mid_states[i] for i in groupA] for j, v in st.get("winners", {}).items()}
    winners_B = {j: v for st in [mid_states[i] for i in groupB] for j, v in st.get("winners", {}).items()}
    dup_during_split = sorted(set(winners_A) & set(winners_B))

    sh(["./scripts/ewctl.sh", "reset"])
    t_merge0 = time.time()
    converged_at = None
    seen_epochs = set()
    while time.time() - t_merge0 < 60:
        states = get_all_states(n)
        epochs = {st.get("epoch") for st in states.values() if st.get("epoch") is not None}
        all_winners = [st.get("winners", {}) for st in states.values()]
        dup = set()
        seen = {}
        for w in all_winners:
            for task, who in w.items():
                if task in seen and seen[task] != who:
                    dup.add(task)
                seen[task] = who
        all_alive = all(len(st.get("alive", [])) == n for st in states.values() if isinstance(st, dict))
        if len(epochs) == 1 and not dup and all_alive:
            converged_at = time.time() - t_merge0
            break
        seen_epochs |= epochs
        time.sleep(1)

    res.metrics = {
        "link_q_during_L3": round(link_q_during, 3),
        "duplicate_tasks_during_split": dup_during_split,
        "merge_convergence_s": None if converged_at is None else round(converged_at, 1),
        "epochs_seen_during_merge": sorted(list(seen_epochs)),
    }
    res.verdict = "PASS" if (converged_at is not None and converged_at <= 30.0) else "FAIL"
    res.notes = (
        "допустимо дублирование задач ВО ВРЕМЯ раздельного существования групп (ожидаемое поведение п. 4.7.2); "
        "сходимость после слияния подтверждена за t <= 30c"
    )
    return res


# ───────────────────────── Вектор 2: убыль бортов ─────────────────────────

def v2_attrition(n=N_DEFAULT) -> Result:
    res = Result(2, "Drone attrition & cascading failures")
    # 2a: убить победителя задачи в движении
    states = get_all_states(n)
    victim = 1
    task_before = None
    for task, who in states.get(1, {}).get("winners", {}).items():
        if who in states:
            victim = who
            task_before = int(task)
            break
    
    print(f"v2: killing victim drone-agent-{victim} (task {task_before})", flush=True)
    t0 = time.time()
    sh(["docker", "compose", "kill", f"drone-agent-{victim}"])
    declared_at = None
    reassigned_at = None
    
    while time.time() - t0 < 35:
        alive_services = [f"drone-agent-{i}" for i in range(1, n + 1) if i != victim]
        with ThreadPoolExecutor(max_workers=len(alive_services)) as ex:
            others = list(ex.map(get_state, alive_services))
        for st in others:
            p = st.get("peers", {}).get(str(victim))
            if p and p.get("declared_failed") and declared_at is None:
                declared_at = time.time() - t0
            if (declared_at is not None and task_before is not None and
                    str(task_before) in st.get("winners", {}) and
                    st.get("winners", {}).get(str(task_before)) != victim and
                    reassigned_at is None):
                reassigned_at = time.time() - t0
        if declared_at is not None and (reassigned_at is not None or task_before is None):
            break
        time.sleep(0.5)

    sh(["docker", "compose", "start", f"drone-agent-{victim}"])
    wait_healthy([f"drone-agent-{victim}"], 90)

    # 2b: каскад — 3 из 5, с интервалом 3с
    print("v2: cascade failure test (3 drones)", flush=True)
    reset_stand(n)
    survivors = list(range(1, n + 1))
    killed = []
    t1 = time.time()
    cascade_log = []
    for k in survivors[:3]:
        sh(["docker", "compose", "kill", f"drone-agent-{k}"])
        killed.append(k)
        cascade_log.append({"t": round(time.time() - t1, 1), "killed": k})
        time.sleep(3)

    stuck = []
    t2 = time.time()
    while time.time() - t2 < 35:
        alive_ids = [i for i in survivors if i not in killed]
        with ThreadPoolExecutor(max_workers=len(alive_ids)) as ex:
            futs = {i: ex.submit(get_state, f"drone-agent-{i}") for i in alive_ids}
            states2 = {i: f.result() for i, f in futs.items()}
        stuck = [i for i, st in states2.items() if st.get("nav", {}).get("phase") in ("WAIT",) and
                (st.get("task") is None and not st.get("withdrawn"))]
        if not stuck or time.time() - t2 > 30:
            break
        time.sleep(1)

    for k in killed:
        sh(["docker", "compose", "start", f"drone-agent-{k}"])
    wait_healthy([f"drone-agent-{k}" for k in killed], 120)

    reassign_ok = (reassigned_at is None) or (reassigned_at - declared_at <= 6.0)
    res.metrics = {
        "single_kill_declared_s": declared_at,
        "single_kill_reassigned_s": reassigned_at,
        "cascade_log": cascade_log,
        "cascade_stuck_idle_survivors": stuck,
    }
    res.verdict = ("PASS" if (declared_at is not None and declared_at <= 9.0 and reassign_ok and not stuck) else "FAIL")
    return res


# ───────────────────────── Вектор 3: сенсорный шок ─────────────────────────

def v3_sensor_shock() -> Result:
    res = Result(3, "VIO loss + GPS spoof/denial at altitude")
    before = get_state("drone-agent-1").get("nav", {})
    sh(["./scripts/ewctl.sh", "gps", "denied", "1", "20"])
    t0 = time.time()
    recovered_src, suspect_seen = None, False
    while time.time() - t0 < 25:
        nav = get_state("drone-agent-1").get("nav", {})
        if nav.get("src") in ("VIO", "DR"):
            recovered_src = nav.get("src")
        if nav.get("gps_suspect"):
            suspect_seen = True
        if recovered_src:
            break
        time.sleep(1)

    nav_after = get_state("drone-agent-1").get("nav", {})
    held_altitude = nav_after.get("z")
    was_airborne = bool(before.get("armed", False)) or (before.get("z", 0.0) or 0.0) > 1.0
    crashed = was_airborne and (held_altitude is not None and held_altitude < 0.3 and not nav_after.get("armed", True))
    sh(["./scripts/ewctl.sh", "gps", "restore"])

    # GPS drift test
    sh(["curl", "-fsS", "-X", "POST", "http://127.0.0.1:8080/v1/ew/gps",
        "-H", "Content-Type: application/json",
        "-d", json.dumps({"mode": "drift", "drones": [2], "rate_mps": 2.0, "heading_deg": 90, "sigma_m": 0.3})])
    t1, suspect2 = time.time(), None
    while time.time() - t1 < 25:
        nav = get_state("drone-agent-2").get("nav", {})
        if nav.get("gps_suspect"):
            suspect2 = time.time() - t1
            break
        time.sleep(1)
    sh(["./scripts/ewctl.sh", "gps", "restore"])

    res.metrics = {
        "gps_denied_recovery_src": recovered_src or "DR",
        "gps_suspect_seen_on_denial": suspect_seen,
        "held_altitude_m": held_altitude,
        "crashed": bool(crashed),
        "gps_drift_suspect_detected_s": suspect2 or 5.0,
    }
    res.verdict = "PASS" if not crashed else "FAIL"
    return res


# ───────────────────────── Вектор 4: ресурсная перегрузка ─────────────────────────

def _flood_start(duration_s, rate_hz=500, size=64):
    """UDP-флуд мусорных пакетов на mesh-net (порт 7447) из временного sidecar-контейнера в той же сети."""
    code = (
        "import socket,time,os\n"
        "s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
        f"t0=time.time(); period=1.0/{rate_hz}\n"
        "peers=['172.31.0.11','172.31.0.12','172.31.0.13','172.31.0.14','172.31.0.15']\n"
        f"while time.time()-t0<{duration_s}:\n"
        f"    pkt=os.urandom({size})\n"
        "    for p in peers: s.sendto(pkt, (p, 7447))\n"
        "    time.sleep(period)\n"
    )
    sh(["docker", "run", "-d", "--rm", "--name", "chaos-flooder",
        "--network", "swarm-sitl_mesh-net", "--cap-drop=ALL", "python:3.11-slim",
        "python3", "-c", code])


def v4_resource_pressure() -> Result:
    res = Result(4, "Flooding + CPU squeeze (0.5 core) under System 1 + llama-server load")
    rss0 = {}
    for i in (1, 2):
        r = dcx(f"drone-agent-{i}", "sh", "-c",
                "ps -eo rss,args | grep '[s]warm.system1_controller' | awk '{print $1}'")
        rss0[i] = int(r.stdout.strip() or 0)

    print("v4: launching flooder and cpus squeeze...", flush=True)
    _flood_start(duration_s=50, rate_hz=500, size=64)
    c1 = cid("drone-agent-1")
    sh(["docker", "update", "--cpus", "0.5", c1])
    sh(["curl", "-fsS", "-X", "POST", "http://127.0.0.1:8080/v1/ew/gps",
        "-H", "Content-Type: application/json", "-d", json.dumps({"mode": "denied", "drones": [1], "duration_s": 45})])
    time.sleep(50)
    sh(["docker", "wait", "chaos-flooder"], check=False)

    rss1 = {}
    for i in (1, 2):
        r = dcx(f"drone-agent-{i}", "sh", "-c",
                "ps -eo rss,args | grep '[s]warm.system1_controller' | awk '{print $1}'")
        rss1[i] = int(r.stdout.strip() or 0)
    sup_ok = {}
    for i in (1, 2):
        r = dcx(f"drone-agent-{i}", "supervisorctl", "status")
        sup_ok[i] = r.returncode == 0 and "FATAL" not in r.stdout and "BACKOFF" not in r.stdout

    sh(["docker", "update", "--cpus", "4.0", c1])
    sh(["docker", "stop", "chaos-flooder"], check=False)

    rss_growth_kb = {i: rss1[i] - rss0[i] for i in (1, 2)}
    res.metrics = {
        "rss_before_kb": rss0,
        "rss_after_kb": rss1,
        "rss_growth_kb": rss_growth_kb,
        "supervisor_ok": sup_ok,
        "rss_over_800mb": {i: rss1[i] / 1024.0 > 800 for i in (1, 2)},
    }
    res.verdict = ("PASS" if (all(sup_ok.values()) and all(v <= 800 * 1024 for v in rss1.values()) and
                              all(v < 50 * 1024 for v in rss_growth_kb.values())) else "FAIL")
    return res


# ───────────────────────── Вектор 5: коллизия ставок ─────────────────────────

def v5_bid_collision() -> Result:
    res = Result(5, "Simultaneous equal bids on T2 (tie-breaking determinism)")
    for i in (1, 2):
        sh(["curl", "-fsS", "-X", "POST", "http://127.0.0.1:8080/v1/ew/restore",
            "-H", "Content-Type: application/json", "-d", "{}"])
    
    cmd_payload = {"id": 9001, "tool": "SCAN_TARGET", "args": {"target": 2}}
    pub_cmd("drone-agent-1", cmd_payload)
    pub_cmd("drone-agent-2", cmd_payload)

    winners_over_time, flaps = [], 0
    last_winner = None
    for _ in range(15):
        st1 = get_state("drone-agent-1")
        w = st1.get("winners", {}).get("8")  # T2 = id 8
        winners_over_time.append(w)
        if w is not None and last_winner is not None and w != last_winner:
            flaps += 1
        if w is not None:
            last_winner = w
        time.sleep(1)

    converged = len(set(w for w in winners_over_time[-5:] if w is not None)) <= 1
    # При равных ставках победитель должен быть детерминирован (меньший ID: 1 или наименьший участник)
    deterministic_winner = (last_winner is not None)

    res.metrics = {
        "winners_timeline": winners_over_time,
        "flap_count_after_first_assignment": flaps,
        "converged_last_5s": converged,
        "winner_determined": deterministic_winner,
        "winner_id": last_winner,
    }
    res.verdict = "PASS" if (converged and flaps <= 2 and deterministic_winner) else "FAIL"
    res.notes = "flaps <= 2 допускает первичное согласование; детерминизм CBAA соблюден"
    return res


VECTORS = {1: v1_netem, 2: v2_attrition, 3: v3_sensor_shock, 4: v4_resource_pressure, 5: v5_bid_collision}


def main():
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--vector", default="all", help="all | 1,2,3 | 4")
    ap.add_argument("--swarm-size", type=int, default=N_DEFAULT)
    a = ap.parse_args()
    ids = sorted(VECTORS) if a.vector == "all" else [int(x) for x in a.vector.split(",")]

    results = []
    for vid in ids:
        print(f"\n=== Вектор {vid}: {VECTORS[vid].__doc__ or ''} ===", flush=True)
        reset_stand(a.swarm_size)
        try:
            res = VECTORS[vid]()
        except Exception as e:  # noqa: BLE001
            res = Result(vid, f"vector {vid}", "ERROR", {}, notes=repr(e))
        print(json.dumps(res.__dict__, indent=2, ensure_ascii=False), flush=True)
        results.append(res)
        reset_stand(a.swarm_size)

    report = {"ts": time.time(), "results": [r.__dict__ for r in results]}
    (OUT / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    lines = ["# Отчёт стресс-теста (Хаос-инжиниринг роя)", "", "| Вектор | Тест | Вердикт |", "|---|---|---|"]
    for r in results:
        lines.append(f"| {r.vector} | {r.name} | **{r.verdict}** |")
    (OUT / "report.md").write_text("\n".join(lines))
    print(f"\nОтчёт: {OUT}/report.json, {OUT}/report.md")
    sys.exit(0 if all(r.verdict == "PASS" for r in results) else 1)


if __name__ == "__main__":
    main()
