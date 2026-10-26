#!/usr/bin/env python3
"""gz_watchdog.py — сторож стенда: (1) перезапуск симулятора до OOM при росте памяти gz-sim; (2) перезапуск PX4 борта с протухшей сессией DDS.

Утечка gz-sim с 2026-10-26 устранена исправленной Mesa (docker/simulator-core/mesa/), пункт (1) остаётся страховкой.
(2) Живые прогоны: после перезапуска агента PX4 показывает «connected», но Payload tx = 0 B/s (борт не получает данных и не взлетает);
сторож замечает это три проверки подряд и перезапускает только этот PX4.

Причина (замеры 2026-10-26): рендер сенсоров через GPU в WSL2 (Mesa D3D12) течёт ~100-240 МБ/мин пока на сенсоры кто-то подписан;
ядро убивало `gz sim` примерно раз в час, мир сбрасывался, и рой оказывался на земле с недостоверными оценками позиции.

Правило:
  RSS gz-sim >= SOFT и все борты на земле  -> перезапуск сейчас (ничего не прерываем)
  RSS gz-sim >= HARD                       -> перезапуск сразу, в полёте или нет
Перезапуск: остановить агентов и PX4, перезапустить simulator-core, дождаться healthy, поднять PX4, затем агентов.
Миссия начинается заново с сохранённым в томе приказом (выполненные задачи не сохраняются).
Пауза на время тестов: создать файл logs/.watchdog_pause (удалить, чтобы возобновить).
Журнал: logs/gz_watchdog.log (JSON-строки).

    python3 scripts/gz_watchdog.py                 # цикл
    python3 scripts/gz_watchdog.py --once --dry-run
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "logs" / "gz_watchdog.log"
PAUSE = ROOT / "logs" / ".watchdog_pause"
SIM = "swarm-sitl-simulator-core-1"
N = 5
SOFT_MB_DEFAULT, HARD_MB_DEFAULT = 4500, 6500
GROUND_Z_M = 0.6          # выше этой высоты по Gazebo борт считается в воздухе


def decide(rss_mb, airborne, soft_mb=SOFT_MB_DEFAULT, hard_mb=HARD_MB_DEFAULT) -> str:
    """-> "none" | "restart_idle" | "restart_now" | "wait" (выше SOFT, но борты ещё летят или состояние неизвестно)."""
    if rss_mb is None:
        return "none"
    if rss_mb >= hard_mb:
        return "restart_now"
    if rss_mb >= soft_mb:
        return "restart_idle" if airborne is False else "wait"
    return "none"


def parse_gz_rss(ps_output: str):
    """Наибольший RSS процесса ruby (gz sim) в МБ из вывода `ps -eo rss,comm`."""
    best = None
    for line in ps_output.splitlines():
        m = re.match(r"\s*(\d+)\s+ruby\b", line)
        if m:
            best = max(best or 0, int(m.group(1)) // 1024)
    return best


def parse_airborne(gz_pose_output: str):
    """True/False по позам x500_swarm_N из `gz topic -e -n 1 -t /world/.../pose/info`; None, если борты не найдены."""
    zs = []
    for blk in re.split(r"\npose \{", gz_pose_output):
        if re.search(r'name: "x500_swarm_\d"', blk):
            p = re.search(r"position \{(.*?)\}", blk, re.S)
            if p:
                v = dict(re.findall(r"(x|y|z): (-?[\d.e+-]+)", p.group(1)))
                zs.append(float(v.get("z", 0.0)))
    if not zs:
        return None
    return any(z > GROUND_Z_M for z in zs)


def parse_dds_tx(status_output: str):
    """Payload tx (Б/с) из `uxrce_dds_client status`; None, если строки нет."""
    m = re.search(r"Payload tx:\s*(\d+)\s*B/s", status_output)
    return int(m.group(1)) if m else None


def dds_stale_boards(tx_by_board: dict, zero_counts: dict, need=3) -> list:
    """Обновляет zero_counts на месте; возвращает борта, у которых передача нулевая need проверок подряд (None не считается нулём)."""
    stale = []
    for b, tx in tx_by_board.items():
        if tx == 0:
            zero_counts[b] = zero_counts.get(b, 0) + 1
            if zero_counts[b] >= need:
                stale.append(b)
        else:
            zero_counts[b] = 0
    return stale


def sh(cmd, timeout=60):
    """cmd — список аргументов (без shell): все значения здесь константы, но так проще не ошибиться."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(ROOT)).stdout
    except subprocess.TimeoutExpired:
        return ""


def log(event, **kw):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event, **kw}
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps(rec, ensure_ascii=False), flush=True)


def read_state():
    rss = parse_gz_rss(sh(["docker", "exec", SIM, "ps", "-eo", "rss,comm"]))
    airborne = parse_airborne(sh(["docker", "exec", SIM, "sh", "-c",
                                  "timeout 10 gz topic -e -n 1 -t /world/swarm_world/pose/info 2>/dev/null"]))
    return rss, airborne


def telemetry_boards():
    try:
        s = json.load(urllib.request.urlopen("http://127.0.0.1:8000/api/state", timeout=6))
        return sum(1 for d in s["drones"].values() if d.get("pkt"))
    except Exception:
        return 0


def restart_stack(reason, rss_mb):
    agents = [f"drone-agent-{i}" for i in range(1, N + 1)]
    fcs = [f"fc-{i}" for i in range(1, N + 1)]
    log("restart_begin", reason=reason, gz_rss_mb=rss_mb)
    sh(["docker", "compose", "stop", *agents, *fcs], timeout=180)
    sh(["docker", "compose", "restart", "simulator-core"], timeout=180)
    healthy = False
    for _ in range(60):
        if sh(["docker", "inspect", "-f", "{{.State.Health.Status}}", SIM]).strip() == "healthy":
            healthy = True
            break
        time.sleep(3)
    if not healthy:
        log("restart_failed", why="simulator-core не стал healthy за 180 с")
        return False
    sh(["docker", "compose", "up", "-d", "--no-deps", *fcs], timeout=180)
    time.sleep(20)
    sh(["docker", "compose", "up", "-d", "--no-deps", *agents], timeout=180)
    boards = 0
    for _ in range(36):
        time.sleep(10)
        boards = telemetry_boards()
        if boards == N:
            break
    rss_after, _ = read_state()
    log("restart_done", telemetry_boards=boards, gz_rss_mb=rss_after, ok=boards == N)
    return boards == N


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--soft-mb", type=int, default=int(os.environ.get("WATCHDOG_SOFT_MB", SOFT_MB_DEFAULT)))
    ap.add_argument("--hard-mb", type=int, default=int(os.environ.get("WATCHDOG_HARD_MB", HARD_MB_DEFAULT)))
    ap.add_argument("--interval", type=int, default=30)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="решение только в журнал, без перезапуска")
    a = ap.parse_args()
    log("start", soft_mb=a.soft_mb, hard_mb=a.hard_mb, dry_run=a.dry_run)
    last_wait_log = 0.0
    zero_counts = {}
    while True:
        if PAUSE.exists():
            if a.once:
                log("paused")
                return
        else:
            rss, airborne = read_state()
            action = decide(rss, airborne, a.soft_mb, a.hard_mb)
            if action in ("restart_idle", "restart_now"):
                if a.dry_run:
                    log("would_restart", action=action, gz_rss_mb=rss, airborne=airborne)
                else:
                    restart_stack(action, rss)
            elif action == "wait" and time.time() - last_wait_log > 300:
                last_wait_log = time.time()
                log("waiting_for_landing", gz_rss_mb=rss, airborne=airborne)
            if not a.dry_run and action == "none":
                tx = {i: parse_dds_tx(sh(["docker", "exec", f"swarm-sitl-fc-{i}-1", "px4-shell", "uxrce_dds_client", "status"], timeout=20))
                      for i in range(1, N + 1)}
                for i in dds_stale_boards(tx, zero_counts):
                    log("dds_restart", board=i, why="Payload tx = 0 B/s три проверки подряд")
                    sh(["docker", "restart", f"swarm-sitl-fc-{i}-1"], timeout=120)
                    zero_counts[i] = 0
            if a.once:
                log("check", action=action, gz_rss_mb=rss, airborne=airborne)
                return
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())
