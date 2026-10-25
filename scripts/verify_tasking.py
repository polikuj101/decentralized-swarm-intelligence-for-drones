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

# (язык, приказ, ожидаемые поля манифеста). Секторы: alpha=0 charlie=1 delta=2 echo=3 bravo=4 foxtrot=5 (ZONE_TO_SECTOR).
# Поле указано, только если приказ его определяет явно (или это дефолт из SYSTEM: dwell_s=8, rtb=True без слов об удержании).
ALL = [0, 1, 2, 3, 4, 5]
CASES = [
    ("ru", "Разведать квадрат Браво, вскрыть позиции, при обнаружении целей — доразведка и зависание",
     {"active_sectors": [4], "on_target": "DWELL_RECON"}),
    ("en", "Search sector Alpha only, then stay in place", {"active_sectors": [0], "rtb_on_complete": False}),
    ("ru", "Обследовать все квадраты, цели игнорировать", {"active_sectors": ALL, "on_target": "SKIP"}),
    ("en", "Scout Delta and Echo, hover 12 seconds over any targets", {"active_sectors": [2, 3], "dwell_s": 12}),
    ("ru", "Проверить Фокстрот и вернуться на базу", {"active_sectors": [5], "rtb_on_complete": True}),
    ("en", "Sweep zone Charlie, investigate any contacts for 5 seconds, then return to base",
     {"active_sectors": [1], "on_target": "DWELL_RECON", "dwell_s": 5, "rtb_on_complete": True}),
    ("ru", "Обследуй Дельту, при обнаружении цели только подтверди присутствие, без доразведки",
     {"active_sectors": [2], "on_target": "DWELL_ONLY"}),
    ("en", "Search everything, ignore all detections, come home when finished",
     {"active_sectors": ALL, "on_target": "SKIP", "rtb_on_complete": True}),
    ("ru", "Альфа и Чарли: найти цели, зависнуть над каждой на 15 секунд, остаться на месте после поиска",
     {"active_sectors": [0, 1], "dwell_s": 15, "rtb_on_complete": False}),
    ("en", "Check Bravo, Echo and Foxtrot, loiter 3 seconds on contact",
     {"active_sectors": [3, 4, 5], "dwell_s": 3}),
    ("ru", "Эхо — осмотреть, цели не трогать", {"active_sectors": [3], "on_target": "SKIP"}),
    ("en", "Survey the entire area and investigate every target you find",
     {"active_sectors": ALL, "on_target": "DWELL_RECON"}),
    ("ru", "Пройдись по Фокстроту и Браво, на целях зависни секунд на десять, потом домой",
     {"active_sectors": [4, 5], "dwell_s": 10, "rtb_on_complete": True}),
    ("en", "Alpha, Delta. Confirm presence of any targets only. Hold position after.",
     {"active_sectors": [0, 2], "on_target": "DWELL_ONLY", "rtb_on_complete": False}),
    ("ru", "Все зоны, при цели доразведка 6 секунд и возврат", {"active_sectors": ALL, "on_target": "DWELL_RECON", "dwell_s": 6, "rtb_on_complete": True}),
    ("en", "Look at Charlie please", {"active_sectors": [1]}),
    ("ru", "Глянь что в Чарли, если кто там есть — зависни и пригляди", {"active_sectors": [1], "on_target": "DWELL_RECON"}),
    ("en", "Scan sector Echo, skip targets, stay put when done", {"active_sectors": [3], "on_target": "SKIP", "rtb_on_complete": False}),
    ("ru", "Delta и Echo осмотреть, цели зафиксировать и зависнуть на 9 секунд", {"active_sectors": [2, 3], "dwell_s": 9}),
    ("en", "Reconnoiter Alpha, Bravo, Charlie, Delta, Echo and Foxtrot; dwell 7 seconds on targets; return to base",
     {"active_sectors": ALL, "dwell_s": 7, "rtb_on_complete": True}),
]
FIELDS = ("active_sectors", "on_target", "dwell_s", "rtb_on_complete")
FIELD_LABEL = {"active_sectors": "зоны", "on_target": "on_target", "dwell_s": "dwell", "rtb_on_complete": "rtb"}
REPEAT_CASE = 0          # этот приказ повторяется для проверки детерминизма
REPEATS = 3
AGENTS = [f"swarm-sitl-drone-agent-{i}-1" for i in range(1, 6)]
MANIFEST_PATHS = ("/opt/swarm/state/manifest-{n}.json", "/opt/swarm/config/manifest.json")   # том agent-state, затем старое место


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
    """problems — сбои стенда (HTTP, борты не ответили, манифесты бортов различаются);
    wrong — расхождения с ожидаемым (точность компиляции, только измерение)."""
    code, body, wall = post(url, prompt)
    out = {"prompt": prompt, "http": code, "wall_s": round(wall, 1), "problems": [], "wrong": {}}
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
                out["wrong"][k] = {"want": want, "got": ref.get(k)}
    return out


def read_manifest(container: str):
    n = container.split("-")[-2]
    for path in MANIFEST_PATHS:
        try:
            raw = subprocess.run(["docker", "exec", container, "cat", path.format(n=n)], capture_output=True, text=True, timeout=10).stdout
            return json.loads(raw)
        except Exception:  # noqa: BLE001
            continue
    return None


def pct(a: int, b: int) -> str:
    return f"{a}/{b} = {100.0 * a / b:.0f}%" if b else "н/д"


def accuracy_report(rows: list) -> list:
    """rows: [(lang, expected, wrong)]. Возвращает строки таблицы точности."""
    lines = []
    full = sum(1 for _, _, w in rows if not w)
    lines.append(f"полностью верных: {pct(full, len(rows))}")
    for f in FIELDS:
        tot = sum(1 for _, e, _ in rows if f in e)
        ok = sum(1 for _, e, w in rows if f in e and f not in w)
        lines.append(f"  поле {FIELD_LABEL[f]:<10} {pct(ok, tot)}")
    for lang, name in (("ru", "русский"), ("en", "английский")):
        sub = [r for r in rows if r[0] == lang]
        lines.append(f"  язык {name:<11} полностью верных {pct(sum(1 for _, _, w in sub if not w), len(sub))}")
    return lines


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--strict", action="store_true", help="код возврата 1 и при расхождениях с ожидаемым (иначе только измерение)")
    a = ap.parse_args()

    originals = {c: read_manifest(c) for c in AGENTS}
    original = (originals[AGENTS[0]] or {}).get("source_prompt")
    print(f"исходный приказ на борту: {original!r}", flush=True)
    lat, results, failed, rows = [], [], False, []
    for i, (lang, prompt, expected) in enumerate(CASES):
        r = run_case(a.url, prompt, expected, lat)
        results.append(r)
        rows.append((lang, expected, r["wrong"]) if not r["problems"] else (lang, expected, {f: {"want": expected[f], "got": None} for f in expected}))
        print(f"\n[{i + 1}] ({lang}) {prompt}\n    HTTP {r['http']}, {r['wall_s']} с, манифест: {json.dumps(r.get('manifest'), ensure_ascii=False)}", flush=True)
        for pr in r["problems"]:
            print(f"    ✗ СТЕНД: {pr}", flush=True)
        for k, w in r["wrong"].items():
            print(f"    ✗ {k}: ожидалось {w['want']}, получено {w['got']}", flush=True)
        failed |= bool(r["problems"])
        if not r["problems"] and not r["wrong"]:
            print("    ✓ идентичен на 5 бортах и совпал с ожидаемым", flush=True)

    print(f"\nдетерминизм: приказ [{REPEAT_CASE + 1}] ещё {REPEATS} раза", flush=True)
    first = stable(results[REPEAT_CASE].get("manifest") or {})
    for n in range(REPEATS):
        r = run_case(a.url, CASES[REPEAT_CASE][1], {}, lat)
        same = stable(r.get("manifest") or {}) == first
        print(f"    повтор {n + 1}: {'идентичен' if same else 'ОТЛИЧАЕТСЯ'}", flush=True)
        failed |= not same

    restored_ok = None
    if original:
        post(a.url, original)
        print(f"\nисходный приказ восстановлен: {original!r}", flush=True)
        after = {c: read_manifest(c) for c in AGENTS}
        keep = lambda m: {k: v for k, v in (m or {}).items() if k not in VOLATILE}  # noqa: E731
        restored_ok = all(keep(after[c]) == keep(originals[c]) for c in AGENTS if originals[c])
        print(f"манифест на 5 бортах совпадает с исходным (без manifest_id/compiled_at/latency_ms): {'да' if restored_ok else 'НЕТ'}", flush=True)
        failed |= not restored_ok

    print("\nТОЧНОСТЬ КОМПИЛЯЦИИ (измерение, tasking.py не менялся):")
    for ln in accuracy_report(rows):
        print("  " + ln, flush=True)
    wrong_all = [(i + 1, k, w) for i, r in enumerate(results) for k, w in r["wrong"].items()]
    if wrong_all:
        print("  ошибки: " + "; ".join(f"[{n}] {k}: {w['want']}→{w['got']}" for n, k, w in wrong_all), flush=True)
    if lat:
        lat.sort()
        p50 = statistics.median(lat)
        p95 = lat[min(len(lat) - 1, int(round(0.95 * (len(lat) - 1))))]
        verdict = "в норме" if p95 <= LATENCY_LIMIT_MS else "ПРЕВЫШЕН порог"
        print(f"\nлатентность компиляции на борту, {len(lat)} замеров: p50 {p50:.0f} мс, p95 {p95:.0f} мс, max {max(lat):.0f} мс "
              f"(порог p95 {LATENCY_LIMIT_MS:.0f} мс: {verdict})")
        failed |= p95 > LATENCY_LIMIT_MS
    if a.strict:
        failed |= bool(wrong_all)
    print("\nИТОГ:", "ЕСТЬ РАСХОЖДЕНИЯ СТЕНДА/ЛАТЕНТНОСТИ" if failed else "стенд и латентность в норме", "| расхождений точности:", len(wrong_all))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
