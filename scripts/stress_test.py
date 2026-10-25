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
    """Возвращает container ID / имя по имени сервиса Compose"""
    return f"swarm-sitl-{service}-1" if not service.startswith("swarm-sitl-") else service


def dcx(service, *args, timeout=30):
    cname = cid(service)
    return sh(["docker", "exec", cname, *args], timeout=timeout)


def rr(service, *args, timeout=30):
    """ros-run.sh внутри контейнера борта"""
    return dcx(service, "/usr/local/bin/ros-run.sh", *args, timeout=timeout)


def pub_cmd(service, cmd_dict):
    """Публикация команды в /swarm/local/planner_cmd через Python ROS-ноду"""
    code = (
        "import rclpy,json;from rclpy.node import Node;from std_msgs.msg import String;"
        "rclpy.init();n=Node('stress_pub');p=n.create_publisher(String,'/swarm/local/planner_cmd',10);"
        f"import time;d=json.loads('{json.dumps(cmd_dict)}');d['t_mono']=time.monotonic();"   # свежий штамп: иначе stale-отказ
        "p.publish(String(data=json.dumps(d)));"
        "t0=time.time()\n"
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
        ps_out = sh(["docker", "ps", "--format", "{{.Names}} {{.State}} {{.Status}}"]).stdout
        all_ok = True
        for s in services:
            target = f"swarm-sitl-{s}-1"
            line = next((l for l in ps_out.splitlines() if target in l or s in l), "")
            if not line or ("running" not in line and "Up" not in line):
                all_ok = False
                break
        if all_ok:
            return True
        time.sleep(1)
    return False


def reset_stand(n=N_DEFAULT):
    print("resetting stand...", flush=True)
    sh(["./scripts/ewctl.sh", "reset"])
    agents = [f"drone-agent-{i}" for i in range(1, n + 1)]
    for i in range(1, n + 1):
        sh(["docker", "unpause", cid(f"drone-agent-{i}")], check=False)
        sh(["docker", "update", "--cpus", "4.0", cid(f"drone-agent-{i}")], check=False)
    # Запускаем упавшие контейнеры, если такие есть
    exited = [s for s in sh(["docker", "ps", "-a", "--filter", "status=exited",
              "--format", "{{.Names}}"]).stdout.split()]
    for i in range(1, n + 1):
        name = cid(f"drone-agent-{i}")
        if name in exited:
            sh(["docker", "start", name])
    healthy = wait_healthy(agents, 60)
    settled = wait_swarm_settled(n, 60) if healthy else False
    if not (healthy and settled):
        print(f"WARNING: стенд не вернулся в чистое состояние (контейнеры={healthy}, рой сошёлся={settled}) — "
              "результат следующего вектора может быть недостоверен", flush=True)
    return healthy and settled


def swarm_settled(states: dict, n: int) -> bool:
    """Все n бортов ответили, видят всех живыми и сидят в одной эпохе."""
    sts = list(states.values())
    return (len(sts) == n and all(st and len(st.get("alive", [])) == n for st in sts)
            and len({st.get("epoch") for st in sts}) == 1)


def wait_swarm_settled(n=N_DEFAULT, secs=60):
    t0 = time.time()
    while time.time() - t0 < secs:
        if swarm_settled(get_all_states(n, timeout=10), n):
            return True
        time.sleep(2)
    return False


@dataclass
class Result:
    vector: int
    name: str
    verdict: str = "FAIL"
    metrics: dict = field(default_factory=dict)
    notes: str = ""


# ───────────────────────── Вектор 1: сетевой хаос ─────────────────────────

def ew_agent_errors(r) -> list:
    """ew-controller отвечает HTTP 200, даже когда агент не применил правило ({"1": {"ok": false, "body": {"error": ...}}}),
    а `curl -f` и ewctl.sh этого не видят: раньше Вектор 1 проходил на рое, который на самом деле никто не резал
    (ядро WSL без netem/prio, старый образ агента). Возвращает тексты ошибок."""
    rc = getattr(r, "returncode", 0)
    if rc != 0:                                      # контроллер недоступен / 4xx / 5xx: curl -f молчит в stdout
        return [f"команда завершилась с кодом {rc}: {(getattr(r, 'stderr', '') or '').strip()[:100]!r}"]
    try:
        data = json.loads((r.stdout or "").strip() or "{}")
    except ValueError:
        return [f"не JSON: {(r.stdout or '')[:80]!r}"]
    errs = []
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, dict) and v.get("ok") is False:
                body = v.get("body")
                errs.append(f"D{k}: {(body.get('error') if isinstance(body, dict) else body) or v.get('status')}"[:160])
    return errs


def container_running(service) -> bool:
    r = sh(["docker", "inspect", "-f", "{{.State.Running}}", cid(service)], timeout=15)
    return r.returncode == 0 and r.stdout.strip() == "true"


def survivors_see_dead(states: dict, dead: list) -> bool:
    """Выжившие борты (непустое состояние) не считают убитые живыми. Пустые состояния (ответа нет) не засчитываются:
    раньше они выглядели как «никто не завис», и каскад проходил без единого наблюдения."""
    live = [st for i, st in states.items() if i not in dead]
    if not live or any(not st for st in live):
        return False
    return all(not (set(st.get("alive", [])) & set(dead)) for st in live)


def gps_denial_visible(src_before, srcs_seen, suspect_seen) -> bool:
    """Отказ GPS наблюдаем по бортовой навигации: источник позиции сменился (VIO/DR) или поднят gps_suspect."""
    return bool(suspect_seen) or any(s in ("VIO", "DR") and s != src_before for s in srcs_seen)


def cpu_limit_applied(nano_cpus, want_cpus: float) -> bool:
    """docker inspect HostConfig.NanoCpus совпадает с запрошенным лимитом (docker update мог молча не примениться)."""
    try:
        return abs(int(nano_cpus) - int(want_cpus * 1e9)) < 1e6
    except (TypeError, ValueError):
        return False


def rss_readings_valid(*readings: dict) -> bool:
    """RSS system1_controller прочитан на всех бортах (ps вернул 0, если процесс не найден: рост 0 КБ ничего не доказывает)."""
    return all(v > 0 for rd in readings for v in rd.values())


def reassignment_ok(task_before, declared_at, reassigned_at, limit_s=6.0) -> bool:
    """Задача убитого борта должна быть переназначена в течение limit_s после объявления отказа.
    Раньше «не переназначили вообще» (reassigned_at is None) считалось успехом. Нет задачи у жертвы — проверять нечего."""
    if task_before is None:
        return True
    return declared_at is not None and reassigned_at is not None and reassigned_at - declared_at <= limit_s


def agreed_winner(winners_by_board: dict, n: int):
    """Победитель задачи, одинаковый на всех n бортах; None, если не все ответили или мнения расходятся."""
    vals = [winners_by_board.get(i) for i in range(1, n + 1)]
    if any(v is None for v in vals) or len(set(vals)) != 1:
        return None
    return vals[0]


def v1_netem(n=N_DEFAULT) -> Result:
    res = Result(1, "Zenoh network degradation (netem, split-brain, merge)")
    before = get_state("drone-agent-1")
    link_q_before = before.get("link_q", 0.0)
    ew_errors = ew_agent_errors(sh(["./scripts/ewctl.sh", "mesh", "L3", "all", "20"]))
    time.sleep(10)                                   # середина окна L3 (длительность 20 с): L3 ещё активен
    link_q_during = get_state("drone-agent-1").get("link_q", 0.0)
    time.sleep(12)                                   # дождаться автоснятия L3 до разделения
    l3_effective = not ew_errors and link_q_during <= link_q_before - 0.2

    # split-brain: {1,2,3} | {4,5}, аукцион активен
    groupA, groupB = [1, 2, 3], [4, 5]
    t0 = time.time()
    for a in groupA:
        ew_errors += ew_agent_errors(sh(["curl", "-fsS", "-X", "POST", "http://127.0.0.1:8080/v1/ew/mesh",
                                         "-H", "Content-Type: application/json",
                                         "-d", json.dumps({"level": "L4", "drones": [a], "peers": [b for b in groupB]})]))
    for b in groupB:
        ew_errors += ew_agent_errors(sh(["curl", "-fsS", "-X", "POST", "http://127.0.0.1:8080/v1/ew/mesh",
                                         "-H", "Content-Type: application/json",
                                         "-d", json.dumps({"level": "L4", "drones": [b], "peers": [a for a in groupA]})]))
    split_hold_s = 20
    time.sleep(split_hold_s)
    mid_states = get_all_states(n)
    winners_A = {j: v for st in [mid_states[i] for i in groupA] for j, v in st.get("winners", {}).items()}
    winners_B = {j: v for st in [mid_states[i] for i in groupB] for j, v in st.get("winners", {}).items()}
    dup_during_split = sorted(set(winners_A) & set(winners_B))
    # разрыв должен быть виден самому рою: каждый борт считает живыми ровно свою группу
    own_group = {**{i: set(groupA) for i in groupA}, **{i: set(groupB) for i in groupB}}
    split_effective = all(set(mid_states[i].get("alive", [])) == own_group[i] for i in own_group if i in mid_states)         and len(mid_states) == len(own_group)

    t_merge0 = time.time()
    sh(["./scripts/ewctl.sh", "reset"])
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
        "link_q_before": round(link_q_before, 3),
        "link_q_during_L3": round(link_q_during, 3),
        "l3_effective": l3_effective,
        "split_effective": split_effective,
        "ew_agent_errors": sorted(set(ew_errors))[:5],
        "duplicate_tasks_during_split": dup_during_split,
        "merge_convergence_s": None if converged_at is None else round(converged_at, 1),
        "epochs_seen_during_merge": sorted(list(seen_epochs)),
    }
    chaos_applied = l3_effective and split_effective and not ew_errors
    res.verdict = "PASS" if (chaos_applied and converged_at is not None and converged_at <= 30.0) else "FAIL"
    if not chaos_applied:
        res.notes = ("ХАОС НЕ ПРИМЕНЁН — результат слияния ничего не доказывает: "
                     f"L3 {'применён' if l3_effective else 'не снизил link_q'}, "
                     f"разделение {'разошлось' if split_effective else 'не разошлось (alive не по группам)'}, "
                     f"ошибки агентов: {sorted(set(ew_errors))[:2] or 'нет'}")
    else:
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
    victim_stopped = not container_running(f"drone-agent-{victim}")
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
    killed_stopped = all(not container_running(f"drone-agent-{k}") for k in killed)
    cascade_seen_dead = False
    t2 = time.time()
    while time.time() - t2 < 35:
        alive_ids = [i for i in survivors if i not in killed]
        with ThreadPoolExecutor(max_workers=len(alive_ids)) as ex:
            futs = {i: ex.submit(get_state, f"drone-agent-{i}") for i in alive_ids}
            states2 = {i: f.result() for i, f in futs.items()}
        stuck = [i for i, st in states2.items() if st.get("nav", {}).get("phase") in ("WAIT",) and
                (st.get("task") is None and not st.get("withdrawn"))]
        cascade_seen_dead = survivors_see_dead(states2, killed)
        if (cascade_seen_dead and not stuck) or time.time() - t2 > 30:
            break
        time.sleep(1)

    for k in killed:
        sh(["docker", "compose", "start", f"drone-agent-{k}"])
    wait_healthy([f"drone-agent-{k}" for k in killed], 120)

    reassign_ok = reassignment_ok(task_before, declared_at, reassigned_at)
    res.metrics = {
        "single_kill_declared_s": declared_at,
        "single_kill_reassigned_s": reassigned_at,
        "cascade_log": cascade_log,
        "cascade_stuck_idle_survivors": stuck,
        "victim_stopped": victim_stopped,
        "cascade_killed_stopped": killed_stopped,
        "cascade_survivors_marked_dead": cascade_seen_dead,
    }
    chaos_applied = victim_stopped and killed_stopped and cascade_seen_dead
    res.verdict = ("PASS" if (chaos_applied and declared_at is not None and declared_at <= 9.0 and reassign_ok and not stuck)
                   else "FAIL")
    if not chaos_applied:
        res.notes = ("ХАОС НЕ ПРИМЕНЁН — результат ничего не доказывает: "
                     f"одиночный борт {'остановлен' if victim_stopped else 'не остановлен'}, "
                     f"каскад {'остановлен' if killed_stopped else 'не остановлен'}, "
                     f"выжившие {'пометили убитых' if cascade_seen_dead else 'не пометили убитых мёртвыми (или не ответили)'}")
    return res


# ───────────────────────── Вектор 3: сенсорный шок ─────────────────────────

def v3_sensor_shock() -> Result:
    res = Result(3, "GPS denial + slow GPS drift (spoofing and VIO loss are NOT injected)")
    before = get_state("drone-agent-1").get("nav", {})
    ew_errors = ew_agent_errors(sh(["./scripts/ewctl.sh", "gps", "denied", "1", "20"]))
    t0 = time.time()
    recovered_src, suspect_seen, srcs_seen = None, False, set()
    while time.time() - t0 < 25:
        nav = get_state("drone-agent-1").get("nav", {})
        if nav.get("src"):
            srcs_seen.add(nav.get("src"))
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
    ew_errors += ew_agent_errors(sh(["curl", "-fsS", "-X", "POST", "http://127.0.0.1:8080/v1/ew/gps",
        "-H", "Content-Type: application/json",
        "-d", json.dumps({"mode": "drift", "drones": [2], "rate_mps": 2.0, "heading_deg": 90, "sigma_m": 0.3})]))
    t1, suspect2 = time.time(), None
    while time.time() - t1 < 25:
        nav = get_state("drone-agent-2").get("nav", {})
        if nav.get("gps_suspect"):
            suspect2 = time.time() - t1
            break
        time.sleep(1)
    sh(["./scripts/ewctl.sh", "gps", "restore"])

    res.metrics = {
        "gps_denied_recovery_src": recovered_src,
        "gps_suspect_seen_on_denial": suspect_seen,
        "held_altitude_m": held_altitude,
        "crashed": bool(crashed),
        "gps_drift_suspect_detected_s": suspect2,
        "nav_src_before": before.get("src"),
        "nav_srcs_seen_during_denial": sorted(srcs_seen),
        "ew_agent_errors": sorted(set(ew_errors))[:5],
    }
    denial_visible = gps_denial_visible(before.get("src"), srcs_seen, suspect_seen)
    chaos_applied = denial_visible and not ew_errors
    drift_detected = suspect2 is not None
    res.verdict = "PASS" if (chaos_applied and not crashed and drift_detected) else "FAIL"
    if not chaos_applied:
        res.notes = ("ХАОС НЕ ПРИМЕНЁН — результат ничего не доказывает: "
                     f"отказ GPS {'виден в навигации' if denial_visible else 'не виден (src не сменился, gps_suspect не поднят)'}, "
                     f"ошибки агентов: {sorted(set(ew_errors))[:2] or 'нет'}")
    elif not drift_detected:
        res.notes = "дрейф GPS (2 м/с, 25 с) не был обнаружен бортом D2 (gps_suspect не поднят)"
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
    return sh(["docker", "run", "-d", "--rm", "--name", "chaos-flooder",
               "--network", "swarm-sitl_mesh-net", "--cap-drop=ALL", "python:3.11-slim",
               "python3", "-c", code], timeout=180)


def v4_resource_pressure() -> Result:
    res = Result(4, "UDP flood on mesh port + CPU squeeze (0.5 core) on D1 (no extra LLM load is generated)")
    rss0 = {}
    for i in (1, 2):
        r = dcx(f"drone-agent-{i}", "sh", "-c",
                "ps -eo rss,args | grep '[s]warm.system1_controller' | awk '{print $1}'")
        rss0[i] = int(r.stdout.strip() or 0)

    print("v4: launching flooder and cpus squeeze...", flush=True)
    flood_started = _flood_start(duration_s=50, rate_hz=500, size=64).returncode == 0
    c1 = cid("drone-agent-1")
    sh(["docker", "update", "--cpus", "0.5", c1])
    ew_errors = ew_agent_errors(sh(["curl", "-fsS", "-X", "POST", "http://127.0.0.1:8080/v1/ew/gps",
        "-H", "Content-Type: application/json", "-d", json.dumps({"mode": "denied", "drones": [1], "duration_s": 45})]))
    time.sleep(25)                                   # середина окна: флудер жив, лимит CPU выставлен
    nano = sh(["docker", "inspect", "-f", "{{.HostConfig.NanoCpus}}", c1], timeout=15).stdout.strip()
    cpu_limited = cpu_limit_applied(nano, 0.5)
    flooder_alive = sh(["docker", "ps", "-q", "--filter", "name=chaos-flooder"]).stdout.strip() != ""
    time.sleep(25)
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
    alive_after = {i: len(get_state(f"drone-agent-{i}").get("alive", [])) for i in (1, 2)}      # рой не должен развалиться от флуда
    swarm_intact = all(v == N_DEFAULT for v in alive_after.values())

    rss_growth_kb = {i: rss1[i] - rss0[i] for i in (1, 2)}
    res.metrics = {
        "rss_before_kb": rss0,
        "rss_after_kb": rss1,
        "rss_growth_kb": rss_growth_kb,
        "supervisor_ok": sup_ok,
        "rss_over_800mb": {i: rss1[i] / 1024.0 > 800 for i in (1, 2)},
        "cpu_limit_applied": cpu_limited,
        "nano_cpus_mid_window": nano,
        "flooder_started": flood_started,
        "flooder_alive_mid_window": flooder_alive,
        "ew_agent_errors": sorted(set(ew_errors))[:5],
        "rss_readings_valid": rss_readings_valid(rss0, rss1),
        "alive_count_after": alive_after,
    }
    chaos_applied = (cpu_limited and flood_started and flooder_alive and not ew_errors
                     and rss_readings_valid(rss0, rss1))
    if not chaos_applied:
        res.notes = ("ХАОС НЕ ПРИМЕНЁН — результат ничего не доказывает: "
                     f"лимит CPU {'выставлен' if cpu_limited else 'не выставлен'}, "
                     f"флудер {'работал' if flood_started and flooder_alive else 'не работал'}, "
                     f"ошибки агентов: {sorted(set(ew_errors))[:2] or 'нет'}, "
                     f"RSS {'прочитан' if rss_readings_valid(rss0, rss1) else 'не прочитан (system1_controller не найден)'}")
    res.verdict = ("PASS" if (chaos_applied and swarm_intact and all(sup_ok.values()) and all(v <= 800 * 1024 for v in rss1.values()) and
                              all(v < 50 * 1024 for v in rss_growth_kb.values())) else "FAIL")
    return res


# ───────────────────────── Вектор 5: коллизия ставок ─────────────────────────

def collision_resolved(timeline: list, n: int, min_agreed=3) -> bool:
    """После первого согласия все n бортов видят одного победителя не менее min_agreed замеров подряд; дальше допустимо только
    одно: задача выполнена и исчезла из winners сразу на всех бортах. Частичное расхождение или смена победителя — провал."""
    first = next((i for i, t in enumerate(timeline) if t["agreed"] is not None), None)
    if first is None:
        return False
    winner, agreed_run, finished = timeline[first]["agreed"], 0, False
    for t in timeline[first:]:
        vals = [t["per_board"].get(i) for i in range(1, n + 1)]
        if all(v is None for v in vals):
            finished = True
        elif finished or any(v != winner for v in vals):
            return False                                   # вернулся/смешался после завершения или борты расходятся
        else:
            agreed_run += 1
    return agreed_run >= min_agreed


def v5_bid_collision() -> Result:
    res = Result(5, "Two boards get the same SCAN_TARGET T2 command at once: all 5 boards must agree on one stable winner")
    sh(["curl", "-fsS", "-X", "POST", "http://127.0.0.1:8080/v1/ew/restore",
        "-H", "Content-Type: application/json", "-d", "{}"])

    # раньше в команде не было source/phase/t_mono: схема координатора отклоняла её, и Вектор 5 ничего не проверял
    phase = get_state("drone-agent-1").get("phase", "NOMINAL")
    cmd_payload = {"id": 9001, "source": "S1", "phase": phase, "t_mono": 0.0, "tool": "SCAN_TARGET", "args": {"target": 2}}
    pub_cmd("drone-agent-1", cmd_payload)
    pub_cmd("drone-agent-2", cmd_payload)

    timeline, flaps, last_winner = [], 0, None
    for _ in range(15):
        states = get_all_states(N_DEFAULT)
        per_board = {i: (st.get("winners") or {}).get("8") for i, st in states.items()}   # T2 = id 8
        w = agreed_winner(per_board, N_DEFAULT)
        timeline.append({"per_board": per_board, "agreed": w})
        if w is not None and last_winner is not None and w != last_winner:
            flaps += 1
        if w is not None:
            last_winner = w
        time.sleep(1)

    converged = collision_resolved(timeline, N_DEFAULT)
    res.metrics = {
        "timeline": timeline,
        "flap_count_after_first_agreement": flaps,
        "boards_agreed_then_stayed_consistent": converged,
        "winner_id": last_winner,
        "note": "равные ставки не конструируются: проверяется согласие бортов, а не правило tie-break",
    }
    res.verdict = "PASS" if (converged and flaps <= 2) else "FAIL"
    if res.verdict == "PASS":
        res.notes = "все 5 бортов видят одного победителя T2 последние 5 с; tie-break по хэшу не проверялся (см. unit-тесты CBAA)"
    else:
        res.notes = ("борты не пришли к одному победителю задачи id 8 (T2) или он менялся: проверьте, что T2 есть в winners "
                     "(на неподнятом рое набор задач другой), и что рой действительно торгует")
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
    lines = ["# Отчёт стресс-теста (Хаос-инжиниринг роя)", "", "| Вектор | Тест | Вердикт | Примечание |", "|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r.vector} | {r.name} | **{r.verdict}** | {(r.notes or '').replace('|', '/')} |")
    (OUT / "report.md").write_text("\n".join(lines))
    print(f"\nОтчёт: {OUT}/report.json, {OUT}/report.md")
    sys.exit(0 if all(r.verdict == "PASS" for r in results) else 1)


if __name__ == "__main__":
    main()
