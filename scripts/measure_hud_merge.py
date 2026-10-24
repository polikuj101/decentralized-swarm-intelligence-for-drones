#!/usr/bin/env python3
"""scripts/measure_hud_merge.py — воспроизводимый замер времени слияния через кнопки Split/Heal Web-HUD.

Сценарий как в verify_ground_truth.py: reset, пауза 2 с, Split, удержание (по умолчанию 20 с), Heal, ожидание
сходимости. Время считает сам HUD (docker/web-hud/convergence.py) от момента, когда restore mesh принят
ew-controller; в отличие от verify_ground_truth.py, часы не включают длительность `ewctl reset` (~2,2 с).

Прогон засчитывается только если разделение реально применилось (split_effective): иначе сеть не расходилась,
и цифра ничего не значит (так было, пока в образе лежал ew_agent.py без nftables-fallback для WSL2).

Запуск (из WSL/Linux, нужен только python3): ./scripts/measure_hud_merge.py --runs 5
Результат: таблица в stdout и logs/verify/<время>/hud_merge.json (с параметрами стенда).
"""
import argparse
import json
import os
import platform
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HDR = {"X-HUD": "1", "Content-Type": "application/json"}


class Hud:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def call(self, method: str, path: str, body: dict | None = None, timeout: float = 30.0):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, headers=HDR, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {}

    def state(self) -> dict:
        return self.call("GET", "/api/state")[1]

    def epochs(self) -> dict:
        d = self.state().get("drones", {})
        return {i: ((v.get("pkt") or {}).get("swarm") or {}).get("epoch") for i, v in d.items()}


def stand_info() -> dict:
    load = os.getloadavg() if hasattr(os, "getloadavg") else None
    return {"kernel": platform.release(), "cpus": os.cpu_count(), "loadavg": load,
            "time": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


def one_run(hud: Hud, idx: int, hold_s: float, max_wait_s: float, watch_s: float, gap_s: float) -> dict:
    print(f"\n=== прогон {idx} ===", flush=True)
    hud.call("POST", "/api/ew/restore", {"channel": "all", "drones": None})
    time.sleep(2)
    base = hud.epochs()
    code, body = hud.call("POST", "/api/split")
    if code != 200:
        print(f"  split НЕ применён (HTTP {code}): {str(body.get('detail'))[:200]}", flush=True)
        return {"run": idx, "ok": False, "error": f"split HTTP {code}: {str(body.get('detail'))[:300]}"}
    time.sleep(hold_s)
    c_split = hud.state().get("converge", {})
    end_split = hud.epochs()
    code, body = hud.call("POST", "/api/heal")
    if code != 200:
        print(f"  heal НЕ применён (HTTP {code}): {str(body.get('detail'))[:200]}", flush=True)
        return {"run": idx, "ok": False, "error": f"heal HTTP {code}: {str(body.get('detail'))[:300]}"}
    t0 = time.time()                       # момент Heal (unix) — по нему разбираются выбросы в логах координаторов
    c = {}
    while time.time() - t0 < max_wait_s:
        c = hud.state().get("converge", {})
        if c.get("phase") == "converged":
            break
        time.sleep(0.1)
    ok = c.get("phase") == "converged" and bool(c.get("split_effective"))
    # «Сошлось» (одна эпоха, все живы, нет дублей) — не то же самое, что «стабилизировалось»: координаторы могут
    # повысить эпоху ещё раз уже после этого (перезапуск аукциона). Наблюдаем и записываем моменты смен эпохи.
    # «Стабильно с» — конец ПЕРВОГО кластера смен после Heal (следующая смена не раньше, чем через gap_s: как T_DEAD).
    # Всё позднее — посторонние смены (например, мерцание одного борта), к слиянию они не относятся.
    change_times = []
    if ok and watch_s > 0:
        prev, t_end = hud.epochs(), time.time() + watch_s
        while time.time() < t_end:
            time.sleep(0.2)
            now_ep = hud.epochs()
            if now_ep != prev:
                prev = now_ep
                change_times.append(round(time.time() - t0, 2))
                t_end = max(t_end, time.time() + watch_s)
    settled_s, late_changes = c.get("merge_s"), 0
    cluster_end = c.get("merge_s") or 0.0
    first_after = [t for t in change_times if t >= 0]
    for t in first_after:
        if t - cluster_end <= gap_s:
            cluster_end = t
        else:
            late_changes += 1
    settled_s = round(max(cluster_end, c.get("merge_s") or 0.0), 2) if ok else None
    row = {"run": idx, "ok": ok, "merge_s": c.get("merge_s"), "split_effective": bool(c.get("split_effective")),
           "epochs_merge": c.get("epochs_merge"), "epoch_count": len(c.get("epochs_merge") or []),
           "dup_split": c.get("dup_split"), "dup_merge": c.get("dup_merge"),
           "heal_at_unix": round(t0, 2), "settled_s": settled_s, "unrelated_epoch_changes": late_changes, "epoch_change_times": change_times, "epochs_start": base, "epochs_end_of_split": end_split, "split_phase_at_hold_end": c_split.get("phase")}
    why = "" if ok else (" (разделение не разошлось, прогон не засчитан)" if not row["split_effective"] else " (таймаут)")
    print(f"  merge_s={row['merge_s']} стабильно с {row['settled_s']} (посторонних смен эпохи позже: {late_changes}) "
          f"эпох={row['epoch_count']} dup_split={row['dup_split']} dup_merge={row['dup_merge']}{why}", flush=True)
    time.sleep(3)
    return row


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")     # Windows-консоль по умолчанию cp1252
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--hold", type=float, default=20.0, help="удержание разделения, с")
    ap.add_argument("--watch", type=float, default=6.0, help="сколько наблюдать смены эпохи после «сошлось», с")
    ap.add_argument("--gap", type=float, default=3.5, help="пауза без смен эпохи, после которой смены считаются посторонними, с")
    ap.add_argument("--max-wait", type=float, default=40.0, help="сколько ждать сходимости после Heal, с")
    ap.add_argument("--out", default=None, help="каталог результата (по умолчанию logs/verify/<время>)")
    a = ap.parse_args()

    hud = Hud(a.url)
    info = stand_info()
    print(f"стенд: {info}", flush=True)
    rows = [one_run(hud, i, a.hold, a.max_wait, a.watch, a.gap) for i in range(1, a.runs + 1)]
    hud.call("POST", "/api/ew/restore", {"channel": "all", "drones": None})

    good = [r["merge_s"] for r in rows if r.get("ok") and r.get("merge_s") is not None]
    summary = {"runs": a.runs, "counted": len(good),
               "min_s": min(good) if good else None,
               "median_s": round(statistics.median(good), 2) if good else None,
               "max_s": max(good) if good else None,
               "mean_s": round(statistics.mean(good), 2) if good else None}
    settled = [r["settled_s"] for r in rows if r.get("ok") and r.get("settled_s") is not None]
    summary.update({"settled_median_s": round(statistics.median(settled), 2) if settled else None,
                    "settled_max_s": max(settled) if settled else None,
                    "runs_with_unrelated_epoch_changes": sum(1 for r in rows if r.get("unrelated_epoch_changes"))})
    print("\n" + "=" * 60)
    print(f"{'прогон':<8}{'merge, с':<11}{'стабильно, с':<14}{'эпох':<6}{'dup split':<11}{'dup merge':<11}зачёт")
    for r in rows:
        if r.get("error"):
            print(f"{r['run']:<8}{'—':<12}{'—':<7}{'—':<12}{'—':<12}нет: {r['error'][:60]}")
        else:
            print(f"{r['run']:<8}{str(r['merge_s']):<11}{str(r['settled_s']):<14}{r['epoch_count']:<6}{str(r['dup_split']):<11}"
                  f"{str(r['dup_merge']):<11}{'да' if r['ok'] else 'нет'}")
    print("-" * 60)
    print(f"засчитано {summary['counted']}/{a.runs}: min {summary['min_s']}  медиана {summary['median_s']}  "
          f"max {summary['max_s']}  среднее {summary['mean_s']} с")
    print(f"стабилизация: медиана {summary['settled_median_s']} с, max {summary['settled_max_s']} с; "
          f"прогонов с посторонними сменами эпохи позже: {summary['runs_with_unrelated_epoch_changes']}/{summary['counted']}")
    print("=" * 60)

    out = Path(a.out) if a.out else Path(__file__).resolve().parent.parent / "logs" / "verify" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "hud_merge.json").write_text(json.dumps({"stand": info, "hold_s": a.hold, "summary": summary, "runs": rows},
                                                   ensure_ascii=False, indent=2))
    print(f"результат: {out / 'hud_merge.json'}")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
