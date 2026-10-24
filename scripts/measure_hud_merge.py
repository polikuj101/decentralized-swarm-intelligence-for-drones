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


def one_run(hud: Hud, idx: int, hold_s: float, max_wait_s: float) -> dict:
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
    t0 = time.time()
    c = {}
    while time.time() - t0 < max_wait_s:
        c = hud.state().get("converge", {})
        if c.get("phase") == "converged":
            break
        time.sleep(0.1)
    ok = c.get("phase") == "converged" and bool(c.get("split_effective"))
    row = {"run": idx, "ok": ok, "merge_s": c.get("merge_s"), "split_effective": bool(c.get("split_effective")),
           "epochs_merge": c.get("epochs_merge"), "epoch_count": len(c.get("epochs_merge") or []),
           "dup_split": c.get("dup_split"), "dup_merge": c.get("dup_merge"),
           "epochs_start": base, "epochs_end_of_split": end_split, "split_phase_at_hold_end": c_split.get("phase")}
    why = "" if ok else (" (разделение не разошлось, прогон не засчитан)" if not row["split_effective"] else " (таймаут)")
    print(f"  merge_s={row['merge_s']} эпох={row['epoch_count']} dup_split={row['dup_split']} "
          f"dup_merge={row['dup_merge']}{why}", flush=True)
    time.sleep(3)
    return row


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")     # Windows-консоль по умолчанию cp1252
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--hold", type=float, default=20.0, help="удержание разделения, с")
    ap.add_argument("--max-wait", type=float, default=40.0, help="сколько ждать сходимости после Heal, с")
    ap.add_argument("--out", default=None, help="каталог результата (по умолчанию logs/verify/<время>)")
    a = ap.parse_args()

    hud = Hud(a.url)
    info = stand_info()
    print(f"стенд: {info}", flush=True)
    rows = [one_run(hud, i, a.hold, a.max_wait) for i in range(1, a.runs + 1)]
    hud.call("POST", "/api/ew/restore", {"channel": "all", "drones": None})

    good = [r["merge_s"] for r in rows if r.get("ok") and r.get("merge_s") is not None]
    summary = {"runs": a.runs, "counted": len(good),
               "min_s": min(good) if good else None,
               "median_s": round(statistics.median(good), 2) if good else None,
               "max_s": max(good) if good else None,
               "mean_s": round(statistics.mean(good), 2) if good else None}
    print("\n" + "=" * 60)
    print(f"{'прогон':<8}{'merge, с':<12}{'эпох':<7}{'dup split':<12}{'dup merge':<12}зачёт")
    for r in rows:
        if r.get("error"):
            print(f"{r['run']:<8}{'—':<12}{'—':<7}{'—':<12}{'—':<12}нет: {r['error'][:60]}")
        else:
            print(f"{r['run']:<8}{str(r['merge_s']):<12}{r['epoch_count']:<7}{str(r['dup_split']):<12}"
                  f"{str(r['dup_merge']):<12}{'да' if r['ok'] else 'нет'}")
    print("-" * 60)
    print(f"засчитано {summary['counted']}/{a.runs}: min {summary['min_s']}  медиана {summary['median_s']}  "
          f"max {summary['max_s']}  среднее {summary['mean_s']} с")
    print("=" * 60)

    out = Path(a.out) if a.out else Path(__file__).resolve().parent.parent / "logs" / "verify" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "hud_merge.json").write_text(json.dumps({"stand": info, "hold_s": a.hold, "summary": summary, "runs": rows},
                                                   ensure_ascii=False, indent=2))
    print(f"результат: {out / 'hud_merge.json'}")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
