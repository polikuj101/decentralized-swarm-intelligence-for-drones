#!/usr/bin/env python3
"""gen_failure_data.py — прогон для детектора отказа соседа (docs/PLAN_LEARNED_FAILURE_DETECTOR.md, шаг 2).

Меняет помехи на mesh по случайному расписанию и «убивает» борта паузой контейнера (`docker pause` на 10-60 с, потом `unpause`).
Метка «мёртв» — интервалы паузы по `docker events`, не по расписанию скрипта. Журналы бортов (swarm/fdlog.py) копируются в каталог прогона,
офлайн-оценка: scripts/fd_eval.py.

Перед запуском (иначе координаторы молчат и журнал пуст):
  SWARM_FD_LOG=/opt/swarm/state/fd.jsonl ./scripts/swarm_fresh.sh     # журнал включается переменной, compose пересоздаёт агентов
  приказ оператора (HUD или DEMO_AUTOPILOT=1 scripts/run_demo.sh) в течение MANIFEST_WAIT_S
  python3 scripts/gen_failure_data.py --duration 600

Сегменты помех: L0 (без помех), L1-L3 на всех бортах; B — пачки (L3 включается и снимается каждые 0.5-3 с: nftables теряет пакеты
равномерно, настоящий канал — пачками); L4 — жёсткий разрыв 1-2 бортов от остальных (как /api/split HUD), метка «недостижим».
Полный перезапуск агента не делается: без перезапуска PX4 он ломает сессию DDS (scripts/swarm_fresh.sh).
"""
import argparse
import json
import os
import random
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
N = 5
EW_URL = os.environ.get("EW_URL", "http://127.0.0.1:8080")
WATCHDOG_PAUSE = ROOT / "logs" / ".watchdog_pause"


def cname(i):
    return f"swarm-sitl-drone-agent-{i}-1"


def sh(cmd, timeout=60):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def admin_token():
    tok = os.environ.get("EW_ADMIN_TOKEN", "")
    if not tok and (ROOT / ".env").exists():
        for line in (ROOT / ".env").read_text().splitlines():
            if line.startswith("EW_ADMIN_TOKEN="):
                tok = line.split("=", 1)[1].strip().strip('"')
    return tok


def ew(path, body):
    req = urllib.request.Request(EW_URL + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {admin_token()}"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            status = r.status
    except Exception as e:  # noqa: BLE001
        print(f"  EW {path} {body}: {e}", flush=True)
        status = 0
    if time.time() - t > 2.0:
        print(f"  EW {path} {body}: {time.time() - t:.1f} с", flush=True)
    return status


def ew_level(level, drones):
    """только борта не на паузе: ew-агент борта на паузе не отвечает, вызов ждёт таймаут 6 с и сбивает расписание
    (пробный прогон 2026-11-02); борту после снятия паузы профиль применяется заново (reapply)"""
    if not drones:
        return 200
    if level == "L0":
        return ew("/v1/ew/restore", {"channel": "mesh", "drones": drones})
    return ew("/v1/ew/mesh", {"level": level, "drones": drones})


def ew_split(group):
    other = [d for d in range(1, N + 1) if d not in group]
    ok = True
    for grp, oth in ((group, other), (other, group)):
        for d in grp:
            ok &= ew("/v1/ew/mesh", {"level": "L4", "drones": [d], "peers": oth}) == 200
    return ok


def ew_state_emulation():
    """как реально применён профиль (nftables или tc, что не воспроизведено): пишется в отчёт прогона"""
    st = sh(["./scripts/ewctl.sh", "state"])
    try:
        prof = ((json.loads(st.stdout).get("drones") or {}).get("1") or {}).get("mesh", {}).get("profile") or {}
        return {"emulation": prof.get("emulation"), "unsupported": prof.get("unsupported")}
    except Exception:  # noqa: BLE001
        return None


MESH_PROBE = ("import json; m = json.load(open('/dev/shm/swarm_state.json'))['mesh']; "
              "print(json.dumps([m.get('zenoh_peers'), m.get('rx_backlog'), m.get('stalled')]))")


def mesh_health(skip):
    """по каждому борту не на паузе: [число соседей Zenoh, очередь сокета 7447, заклинило] (swarm/mesh_cfg.py)"""
    out = {}
    for i in range(1, N + 1):
        if i in skip:
            continue
        r = sh(["docker", "exec", cname(i), "python3", "-c", MESH_PROBE], timeout=10)
        try:
            out[i] = json.loads(r.stdout)
        except json.JSONDecodeError:
            out[i] = None
    return out


def fd_path(i):
    r = sh(["docker", "exec", cname(i), "printenv", "SWARM_FD_LOG"])
    return r.stdout.strip()


def preflight():
    paths, problems = {}, []
    for i in range(1, N + 1):
        p = fd_path(i)
        if not p:
            problems.append(f"D{i}: SWARM_FD_LOG не задан (поднять стенд: SWARM_FD_LOG=/opt/swarm/state/fd.jsonl ./scripts/swarm_fresh.sh)")
            continue
        paths[i] = p
        r = sh(["docker", "exec", cname(i), "sh", "-c", f"grep -c '\"k\":\"live\"' {p} 2>/dev/null || true"])
        if not r.stdout.strip() or r.stdout.strip() == "0":
            problems.append(f"D{i}: в журнале нет строки live — приказа не было, координатор молчит")
    return paths, problems


def log_size(i, p):
    r = sh(["docker", "exec", cname(i), "stat", "-c", "%s", p])
    return int(r.stdout.strip() or 0)


def events_start(path):
    """docker events читается с начала прогона в файл: буфер событий демона короткий (256), healthcheck-и его вытесняют,
    и запрос --since после прогона терял половину пауз (пробный прогон 2026-11-02: 5 из 11)"""
    f = open(path, "w")
    return subprocess.Popen(["docker", "events", "--filter", "type=container", "--filter", "event=pause",
                             "--filter", "event=unpause", "--format", "{{json .}}"], stdout=f, stderr=subprocess.DEVNULL), f


def pause_events(path, t1):
    """интервалы паузы контейнеров агентов по docker events (часы те же, что time.time() в контейнерах)"""
    open_, out = {}, {i: [] for i in range(1, N + 1)}
    for line in Path(path).read_text().splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = (e.get("Actor") or {}).get("Attributes", {}).get("name", "")
        ids = [i for i in range(1, N + 1) if name == cname(i)]
        if not ids:
            continue
        i, t = ids[0], e["timeNano"] / 1e9
        if e.get("Action") == "pause":
            open_[i] = t
        elif e.get("Action") == "unpause" and i in open_:
            out[i].append((open_.pop(i), t))
    for i, t in open_.items():          # не снятая пауза (не должно быть: finally снимает все)
        out[i].append((t, t1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=600)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--pause-min", type=float, default=10)
    ap.add_argument("--pause-max", type=float, default=60)
    ap.add_argument("--gap-min", type=float, default=15, help="между паузами, с")
    ap.add_argument("--gap-max", type=float, default=45)
    ap.add_argument("--no-l4", action="store_true")
    ap.add_argument("--no-burst", action="store_true")
    ap.add_argument("--levels", default="L0,L1,L2,L3")
    a = ap.parse_args()
    seed = a.seed if a.seed is not None else int(time.time()) % 100000
    rnd = random.Random(seed)
    out = Path(a.out or ROOT / "logs" / "fd_runs" / time.strftime("%Y%m%d-%H%M%S"))
    out.mkdir(parents=True, exist_ok=True)

    paths, problems = preflight()
    if problems:
        print("\n".join(problems))
        sys.exit(2)
    offsets = {i: log_size(i, p) for i, p in paths.items()}
    ev_proc, ev_file = events_start(out / "docker_events.jsonl")
    time.sleep(1.0)
    made_wd_pause = not WATCHDOG_PAUSE.exists()
    WATCHDOG_PAUSE.touch()

    levels_pool = a.levels.split(",")
    kinds = levels_pool + ([] if a.no_burst else ["B"]) + ([] if a.no_l4 else ["L4"])
    weights = [1.0] * len(levels_pool) + ([] if a.no_burst else [0.5]) + ([] if a.no_l4 else [0.3])
    segs, unreach, planned, load, mesh = [], [], [], [], []
    paused = {}                                   # борт -> время снятия паузы
    t0 = time.time()
    t_end = t0 + a.duration
    seg = None                                    # (kind, t_start, t_stop, group)
    burst_on, t_burst = False, 0.0
    emulation = None

    def up():
        return [i for i in range(1, N + 1) if i not in paused]

    def current_level():
        if seg is None or seg[0] == "L4":
            return "L0"
        if seg[0] == "B":
            return "L3" if burst_on else "L0"
        return seg[0]
    t_next_pause = t0 + rnd.uniform(a.gap_min, a.gap_max)
    t_load = 0.0
    print(f"прогон {out.name}, seed {seed}, {a.duration:.0f} с", flush=True)
    try:
        while time.time() < t_end:
            now = time.time()
            if seg is None or now >= seg[2]:
                if seg is not None:
                    segs.append((seg[1], now, seg[0]))
                    if seg[0] == "L4":
                        unreach.append((seg[1], now, seg[3]))
                kind = rnd.choices(kinds, weights)[0]
                if kind == "L4" and paused:              # разрыв только при всех живых: ew-агент борта на паузе правило не примет
                    kind = "L0"
                dur = rnd.uniform(10, 25) if kind == "L4" else rnd.uniform(30, 90)
                group = sorted(rnd.sample(range(1, N + 1), rnd.choice([1, 2]))) if kind == "L4" else None
                ew_level("L0", up())
                if kind == "L4":
                    ew_split(group)
                elif kind == "B":
                    burst_on, t_burst = False, now
                else:
                    ew_level(kind, up())
                    if emulation is None and kind != "L0":
                        emulation = ew_state_emulation()
                t_set = time.time()
                seg = (kind, t_set, t_set + dur, group)
                print(f"  {t_set - t0:6.1f} {kind}{' ' + str(group) if group else ''} на {dur:.0f} с", flush=True)
            if seg[0] == "B" and now >= t_burst:
                burst_on = not burst_on
                ew_level("L3" if burst_on else "L0", up())
                t_burst = time.time() + rnd.uniform(0.5, 3.0)
            for i, t_un in list(paused.items()):
                if now >= t_un:
                    sh(["docker", "unpause", cname(i)])
                    del paused[i]
                    ew_level(current_level(), [i])       # профиль, который борт пропустил на паузе
            if now >= t_next_pause and seg[0] == "L4":
                t_next_pause = seg[2] + 1.0              # паузы не во время разрыва (см. выше)
            if now >= t_next_pause:
                free = [i for i in range(1, N + 1) if i not in paused]
                n_kill = 2 if rnd.random() < 0.15 and len(free) >= 2 and not paused else 1
                for i in rnd.sample(free, min(n_kill, len(free))) if len(paused) < 2 else []:
                    dur = rnd.uniform(a.pause_min, a.pause_max)
                    sh(["docker", "pause", cname(i)])
                    paused[i] = time.time() + dur
                    planned.append({"j": i, "t": time.time(), "dur": round(dur, 1)})
                    print(f"  {time.time() - t0:6.1f} пауза D{i} на {dur:.0f} с", flush=True)
                t_next_pause = time.time() + rnd.uniform(a.gap_min, a.gap_max)
            if now >= t_load:
                t_load = now + 10.0
                load.append((round(now, 1), os.getloadavg()[0]))
                h = mesh_health(paused)
                mesh.append((round(now, 1), h))
                if h and all(v is not None and v[0] == 0 for v in h.values()):
                    print(f"  {now - t0:6.1f} ВНИМАНИЕ: ни у одного работающего борта нет соседей Zenoh {h}", flush=True)
            time.sleep(0.1)
    finally:
        for i in list(paused):
            sh(["docker", "unpause", cname(i)])
        ew("/v1/ew/restore", {"channel": "mesh", "drones": None})
        t1 = time.time()
        time.sleep(1.0)
        ev_proc.terminate()
        ev_file.close()
        if seg is not None:
            segs.append((seg[1], t1, seg[0]))
            if seg[0] == "L4":
                unreach.append((seg[1], t1, seg[3]))
        if made_wd_pause:
            WATCHDOG_PAUSE.unlink(missing_ok=True)

    time.sleep(2.0)                              # журналы сбрасываются раз в секунду
    for i, p in paths.items():
        r = subprocess.run(["docker", "exec", cname(i), "tail", "-c", f"+{offsets[i] + 1}", p], capture_output=True, timeout=120)
        (out / f"fd_{i}.jsonl").write_bytes(r.stdout)
    pauses = pause_events(out / "docker_events.jsonl", t1)
    run = {"t0": t0, "t1": t1, "seed": seed, "n": N, "levels": segs, "unreach": unreach,
           "pauses": {str(i): v for i, v in pauses.items()}, "pauses_planned": planned,
           "load_1m": load, "mesh": mesh, "cpus": os.cpu_count(), "emulation": emulation, "args": vars(a)}
    (out / "run.json").write_text(json.dumps(run, indent=1))
    n_p = sum(len(v) for v in pauses.values())
    print(f"готово: {out}, пауз по docker events {n_p} (по расписанию {len(planned)}), "
          f"load 1m {min(x for _, x in load):.1f}-{max(x for _, x in load):.1f}", flush=True)
    if n_p != len(planned):
        print("ВНИМАНИЕ: число пауз по docker events не совпадает с расписанием, разметку проверить", flush=True)


if __name__ == "__main__":
    main()
