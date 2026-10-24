#!/usr/bin/env python3
"""scripts/verify_tasking.py — живая проверка компиляции приказа (Раздел 9, TODO 1) через Web-HUD.

Для каждого приказа: POST /api/tasking (fan-out на 5 бортов) и проверка
  1. все борты ответили 200;
  2. манифесты идентичны на всех бортах (без manifest_id / compiled_at / latency_ms);
  3. результат совпал с ОЖИДАЕМЫМ для этого приказа (сектора, on_target, dwell_s, rtb) — отдельная от (2) проверка:
     пять бортов могут одинаково ошибаться (детерминированный выход Qwen-0.5B не гарантирует правильность);
  4. латентность компиляции на борту (latency_ms): p50/p95 против порога 4 с.
Повтор одного приказа проверяет детерминизм (temperature=0, seed=42).
Исходный приказ восстанавливается в конце: manifest.json читается только при старте координатора, поэтому
текущий полёт не затрагивается, но перезапуск агентов подхватит последний записанный манифест.

Запуск (WSL): python3 scripts/verify_tasking.py [--url http://127.0.0.1:8000]
Код возврата: 0 — все проверки (1)-(3) пройдены, 1 — есть расхождения.
"""
import argparse
import json
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request

HDR = {"X-HUD": "1", "Content-Type": "application/json"}
VOLATILE = {"manifest_id", "compiled_at", "latency_ms"}
LATENCY_LIMIT_MS = 4000.0

# (приказ, ожидаемые поля манифеста). Секторы: alpha=0 charlie=1 delta=2 echo=3 bravo=4 foxtrot=5 (ZONE_TO_SECTOR).
CASES = [
    ("Разведать квадрат Браво, вскрыть позиции, при обнаружении целей — доразведка и зависание",
     {"active_sectors": [4], "on_target": "DWELL_RECON"}),
    ("Search sector Alpha only, then stay in place", {"active_sectors": [0], "rtb_on_complete": False}),
    ("Обследовать все квадраты, цели игнорировать", {"active_sectors": [0, 1, 2, 3, 4, 5], "on_target": "SKIP"}),
    ("Scout Delta and Echo, hover 12 seconds over any targets", {"active_sectors": [2, 3], "dwell_s": 12}),
    ("Проверить Фокстрот и вернуться на базу", {"active_sectors": [5], "rtb_on_complete": True}),
]
REPEAT_CASE = 0          # этот приказ повторяется для проверки детерминизма
REPEATS = 3


def post(url: str, prompt: str):
    req = urllib.request.Request(url + "/api/tasking", data=json.dumps({"prompt": prompt}).encode(), headers=HDR, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read()), time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, {"detail": e.read().decode()[:200]}, time.time() - t0


def stable(m: dict) -> dict:
    return {k: v for k, v in m.items() if k not in VOLATILE and k != "source_prompt"}


def run_case(url: str, prompt: str, expected: dict, lat: list) -> dict:
    code, body, wall = post(url, prompt)
    out = {"prompt": prompt, "http": code, "wall_s": round(wall, 1), "problems": []}
    if code != 200:
        out["problems"].append(f"HTTP {code}: {body.get('detail')}")
        return out
    res = body.get("results", [])
    bad = [r for r in res if not r.get("ok")]
    if len(res) != 5 or bad:
        out["problems"].append("ответили не все борты: " + "; ".join(f"D{r['drone']}: {str(r.get('error') or r.get('body'))[:80]}" for r in bad))
    manifests = [r["body"] for r in res if r.get("ok") and isinstance(r.get("body"), dict)]
    for m in manifests:
        if m.get("latency_ms") is not None:
            lat.append(float(m["latency_ms"]))
    if manifests:
        ref = stable(manifests[0])
        diffs = [i for i, m in enumerate(manifests[1:], 2) if stable(m) != ref]
        if diffs:
            out["problems"].append(f"манифесты различаются, борты {diffs} против D1")
        out["manifest"] = ref
        for k, want in expected.items():
            if ref.get(k) != want:
                out["problems"].append(f"ожидалось {k}={want}, получено {ref.get(k)}")
    return out


def current_prompt(container: str = "swarm-sitl-drone-agent-1-1"):
    try:
        raw = subprocess.run(["docker", "exec", container, "cat", "/opt/swarm/config/manifest.json"], capture_output=True, text=True, timeout=10).stdout
        return json.loads(raw).get("source_prompt")
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    a = ap.parse_args()

    original = current_prompt()
    print(f"исходный приказ на борту: {original!r}", flush=True)
    lat, results, failed = [], [], False
    for i, (prompt, expected) in enumerate(CASES):
        r = run_case(a.url, prompt, expected, lat)
        results.append(r)
        print(f"\n[{i + 1}] {prompt}\n    HTTP {r['http']}, {r['wall_s']} с, манифест: {json.dumps(r.get('manifest'), ensure_ascii=False)}", flush=True)
        for pr in r["problems"]:
            print(f"    ✗ {pr}", flush=True)
        failed |= bool(r["problems"])
        if not r["problems"]:
            print("    ✓ идентичен на 5 бортах и совпал с ожидаемым", flush=True)

    print(f"\nдетерминизм: приказ [{REPEAT_CASE + 1}] ещё {REPEATS} раза", flush=True)
    first = stable(results[REPEAT_CASE].get("manifest") or {})
    for n in range(REPEATS):
        r = run_case(a.url, CASES[REPEAT_CASE][0], {}, lat)
        same = stable(r.get("manifest") or {}) == first
        print(f"    повтор {n + 1}: {'идентичен' if same else 'ОТЛИЧАЕТСЯ'}", flush=True)
        failed |= not same

    if original:
        post(a.url, original)
        print(f"\nисходный приказ восстановлен: {original!r}", flush=True)

    if lat:
        lat.sort()
        p50 = statistics.median(lat)
        p95 = lat[min(len(lat) - 1, int(round(0.95 * (len(lat) - 1))))]
        verdict = "в норме" if p95 <= LATENCY_LIMIT_MS else "ПРЕВЫШЕН порог"
        print(f"\nлатентность компиляции на борту, {len(lat)} замеров: p50 {p50:.0f} мс, p95 {p95:.0f} мс, max {max(lat):.0f} мс "
              f"(порог p95 {LATENCY_LIMIT_MS:.0f} мс: {verdict})")
        failed |= p95 > LATENCY_LIMIT_MS
    print("\nИТОГ:", "ЕСТЬ РАСХОЖДЕНИЯ" if failed else "всё совпало")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
