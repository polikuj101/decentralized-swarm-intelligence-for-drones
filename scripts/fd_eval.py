#!/usr/bin/env python3
"""fd_eval.py — офлайн-оценка детекторов отказа соседа по прогонам scripts/gen_failure_data.py (шаг 4 плана).

Для каждого прогона и каждого борта-наблюдателя: детектор прогоняется по журналу борта, переходы DEAD сверяются с паузами контейнеров.
По уровням помех: ложные DEAD за 5 минут работы роя (как regress_false_dead.py: сумма по всем наблюдателям), задержка обнаружения
настоящей паузы (медиана, p95, максимум), пропуски. «live» — решения самого координатора из журнала (сверка, что B0 воспроизведён).
Запуск на хосте, WSL не нужен:
  python scripts/fd_eval.py logs/fd_runs/*            # B0, live и сетка B1
  python scripts/fd_eval.py logs/fd_runs/* --b1 1e-3:3:0,1e-4:3:0
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "docker" / "drone-agent"))

from swarm.fd import B0, B1, live_transitions, replay, score  # noqa: E402


class Live:
    name = "live"


def load_run(d):
    run = json.loads((d / "run.json").read_text())
    logs = {}
    for f in sorted(d.glob("fd_*.jsonl")):
        rows = []
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue                      # обрезанная строка на границе tail -c
        rows.sort(key=lambda r: r["t"])
        if rows and not any(r["k"] in ("live", "start") for r in rows):
            rows.insert(0, {"k": "live", "t": rows[0]["t"]})      # срез журнала после приказа: генератор проверил live до старта
        logs[int(f.stem.split("_")[1])] = rows
    return run, logs


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None


def evaluate(runs, detectors):
    res = {}
    for det in detectors:
        agg = {}
        for run, logs in runs:
            pauses = {int(k): [tuple(x) for x in v] for k, v in run["pauses"].items()}
            levels = [(a, b, lv) for a, b, lv in run["levels"]]
            unreach = [(a, b, g) for a, b, g in run["unreach"]]
            for me, rows in logs.items():
                if isinstance(det, Live):
                    trans = live_transitions(rows)
                    _, active = replay(rows, me, run["n"], B0())
                else:
                    trans, active = replay(rows, me, run["n"], det)
                r = score(trans, active, me, pauses, levels, unreach)
                for lv, sec in r["exposure_s"].items():
                    a = agg.setdefault(lv, {"exp": 0.0, "false": 0, "unreach": 0, "lat": [], "miss": 0})
                    a["exp"] += sec
                for f in r["false_dead"]:
                    agg.setdefault(f["level"], {"exp": 0.0, "false": 0, "unreach": 0, "lat": [], "miss": 0})["false"] += 1
                for d in r["detections"]:
                    agg.setdefault(d["level"], {"exp": 0.0, "false": 0, "unreach": 0, "lat": [], "miss": 0})["lat"].append(d["latency"])
                for m in r["misses"]:
                    agg.setdefault(m["level"], {"exp": 0.0, "false": 0, "unreach": 0, "lat": [], "miss": 0})["miss"] += 1
                if r["unreachable"]:
                    agg.setdefault("L4", {"exp": 0.0, "false": 0, "unreach": 0, "lat": [], "miss": 0})["unreach"] += len(r["unreachable"])
        res[det.name] = agg
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--b1", default="1e-2:3:0,1e-3:3:0,1e-4:3:0,1e-3:3:1,1e-3:2:0",
                    help="сетка B1: eps:t_min:g_age через запятую")
    ap.add_argument("--json", default=None, help="записать сводку в файл")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    runs = [load_run(Path(d)) for d in a.runs if (Path(d) / "run.json").exists()]
    if not runs:
        sys.exit("нет прогонов с run.json")
    dets = [Live(), B0()] + [B1(eps=float(e), t_min=float(tm), g_age=float(g))
                             for e, tm, g in (x.split(":") for x in a.b1.split(",") if x)]
    res = evaluate(runs, dets)
    n_obs = sum(len(logs) for _, logs in runs)
    print(f"прогонов {len(runs)}, журналов борта {n_obs}, пауз {sum(len(v) for r, _ in runs for v in r['pauses'].values())}")
    lvls = sorted({lv for agg in res.values() for lv in agg})
    print(f"{'детектор':34s} {'ур.':4s} {'мин':>6s} {'лож/5мин':>9s} {'обнар':>6s} {'мед':>5s} {'p95':>5s} {'макс':>5s} {'проп':>5s}")
    for name, agg in res.items():
        for lv in lvls:
            x = agg.get(lv)
            if not x:
                continue
            swarm_min = x["exp"] / max(1, n_obs / len(runs)) / 60.0       # минуты работы роя: экспозиция на одного наблюдателя
            rate = x["false"] / (swarm_min / 5.0) if swarm_min > 0 else float("nan")
            lat = x["lat"]
            fmt = lambda v: f"{v:5.1f}" if v is not None else "    -"  # noqa: E731
            extra = f"  недостижим {x['unreach']}" if x["unreach"] else ""
            print(f"{name:34s} {lv:4s} {swarm_min:6.1f} {rate:9.1f} {len(lat):6d} {fmt(pct(lat, .5))} {fmt(pct(lat, .95))} "
                  f"{fmt(max(lat) if lat else None)} {x['miss']:5d}{extra}")
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1, default=list))


if __name__ == "__main__":
    main()
