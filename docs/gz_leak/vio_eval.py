#!/usr/bin/env python3
"""Покадровая сверка VIO и EKF PX4 с позами Gazebo (HANDOFF п. 1).

    python3 docs/gz_leak/vio_eval.py poses.jsonl frames_1.jsonl [frames_2.jsonl ...]

poses.jsonl — docs/gz_leak/pose_log.py (время симуляции); frames_N.jsonl — журнал VIO_FRAME_LOG из swarm/vio_node.py (номер борта —
цифра в имени файла). Для каждого кадра истинное смещение за [t − dt, t] сравнивается с измеренным в осях борта (вперёд/вправо);
остаток регрессируется на высота × изменение тангажа/крена (коэффициент ≈ ±1 означает, что поворот не скомпенсирован, и даёт знак
VIO_DEROT). Ошибка позиции EKF считается в окнах без GPS после совмещения начала координат по участкам с GPS."""
import bisect
import json
import math
import re
import statistics as st
import sys

FX = 160.0 / math.tan(0.6)          # 320 px, hfov 1,2 рад


def load(path):
    return [json.loads(l) for l in open(path) if l.strip().startswith("{")]


def truth_series(poses, n):
    ts, xs, ys = [], [], []
    for r in poses:
        p = r["p"].get(str(n))
        if p and (not ts or r["t"] > ts[-1]):
            ts.append(r["t"]); xs.append(p[0]); ys.append(p[1])
    return ts, xs, ys


def interp(ts, vs, t):
    i = bisect.bisect_left(ts, t)
    if i <= 0 or i >= len(ts) or ts[i] - ts[i - 1] > 0.5:
        return None
    a = (t - ts[i - 1]) / (ts[i] - ts[i - 1])
    return vs[i - 1] + a * (vs[i] - vs[i - 1])


def lstsq1(x, y):
    sxx = sum(v * v for v in x)
    return sum(a * b for a, b in zip(x, y)) / sxx if sxx > 1e-12 else float("nan")


def drone_report(n, frames, poses):
    ts, xs, ys = truth_series(poses, n)
    if len(ts) < 10:
        print(f"D{n}: нет истины"); return
    rows = []
    for f in frames:
        t0, t1 = f["t"] - f["dt"], f["t"]
        e0, e1, n0, n1 = interp(ts, xs, t0), interp(ts, xs, t1), interp(ts, ys, t0), interp(ts, ys, t1)
        if None in (e0, e1, n0, n1):
            continue
        dEt, dNt = e1 - e0, n1 - n0
        c, s = math.cos(f["hdg"]), math.sin(f["hdg"])
        fwd_t, right_t = dNt * c + dEt * s, -dNt * s + dEt * c
        sc = f["h"] / FX
        fwd_m, right_m = f["dy"] * sc, -f["dx"] * sc                 # без поправки на поворот (сырая фазовая корреляция)
        rows.append(dict(f=f, fwd_t=fwd_t, right_t=right_t, fwd_m=fwd_m, right_m=right_m,
                         rot_f=f["h"] * f.get("dpitch", 0.0), rot_r=f["h"] * f.get("droll", 0.0), true_v=math.hypot(dEt, dNt) / f["dt"]))
    if not rows:
        print(f"D{n}: кадры не совпали по времени с истиной"); return
    dur = rows[-1]["f"]["t"] - rows[0]["f"]["t"]
    moving = [r for r in rows if r["true_v"] > 0.3]
    print(f"\nD{n}: кадров {len(rows)} за {dur:.0f} с ({len(rows) / max(dur, 1e-6):.1f} Гц), отброшено фильтром {sum(r['f']['bad'] for r in rows)}, "
          f"в движении (>0,3 м/с) {len(moving)}")
    if moving:
        ef = [r["fwd_m"] - r["fwd_t"] for r in moving]
        er = [r["right_m"] - r["right_t"] for r in moving]
        kf, kr = lstsq1([r["rot_f"] for r in moving], ef), lstsq1([r["rot_r"] for r in moving], er)
        print(f"  остаток вперёд ~ {kf:+.2f} × h·Δтангаж, вправо ~ {kr:+.2f} × h·Δкрен (≈ −1/+1 — поворот не скомпенсирован)")
        sm = sum(r["fwd_m"] for r in moving), sum(r["right_m"] for r in moving)
        stt = sum(r["fwd_t"] for r in moving), sum(r["right_t"] for r in moving)
        scale = math.hypot(*sm) / max(math.hypot(*stt), 1e-6)
        ang = math.degrees(math.atan2(sm[1], sm[0]) - math.atan2(stt[1], stt[0]))
        ang = (ang + 180) % 360 - 180
        for name, k_f, k_r in (("без поправки", 0.0, 0.0), ("с поправкой −1/+1", -1.0, 1.0), ("с поправкой +1/−1", 1.0, -1.0)):
            err = [math.hypot(r["fwd_m"] - k_f * r["rot_f"] - r["fwd_t"], r["right_m"] - k_r * r["rot_r"] - r["right_t"]) for r in moving]
            print(f"  покадровая ошибка смещения {name}: медиана {st.median(err) * 100:.1f} см, p90 {sorted(err)[int(0.9 * len(err))] * 100:.1f} см")
        print(f"  сумма за движение: масштаб VIO/истина {scale:.3f}, угол {ang:+.1f}°")
        bad = [r for r in moving if r["f"]["bad"]]
        if bad:
            eb = [math.hypot(r["fwd_m"] - r["fwd_t"], r["right_m"] - r["right_t"]) for r in bad]
            print(f"  отброшенные в движении: {len(bad)}, их ошибка медиана {st.median(eb) * 100:.0f} см (большая — фильтр прав, малая — отбрасывал верные)")
    # ошибка позиции EKF без GPS
    good = [r for r in rows if r["f"].get("gps_ok") and r["f"].get("px4_n") is not None]
    if good:
        off_n = st.median(interp(ts, ys, r["f"]["t"]) - r["f"]["px4_n"] for r in good)
        off_e = st.median(interp(ts, xs, r["f"]["t"]) - r["f"]["px4_e"] for r in good)
        denied = [r for r in rows if not r["f"].get("gps_ok") and r["f"].get("px4_n") is not None]
        if denied:
            t_start = denied[0]["f"]["t"]
            pts = []
            for r in denied:
                tn, te = interp(ts, ys, r["f"]["t"]), interp(ts, xs, r["f"]["t"])
                pts.append((r["f"]["t"] - t_start, math.hypot(r["f"]["px4_n"] + off_n - tn, r["f"]["px4_e"] + off_e - te), r["f"].get("xy_valid")))
            for mark in (30, 60, 90, 120, 180):
                near = [p for p in pts if abs(p[0] - mark) < 2.0]
                if near:
                    print(f"  без GPS {mark:3d} с: ошибка позиции EKF {near[0][1]:.1f} м, xy_valid={near[0][2]}")
            print(f"  без GPS всего {pts[-1][0]:.0f} с: макс. ошибка {max(p[1] for p in pts):.1f} м, xy_valid=False {sum(1 for p in pts if p[2] is False)} кадров")


def main():
    poses = load(sys.argv[1])
    print(f"истина: {len(poses)} сообщений, {poses[-1]['t'] - poses[0]['t']:.0f} с симуляции")
    for path in sys.argv[2:]:
        m = re.search(r"(\d+)\D*$", path)
        frames = load(path)
        skips = {}
        for f in frames:
            if "skip" in f:
                skips[f["skip"]] = skips.get(f["skip"], 0) + 1
        drone_report(int(m.group(1)), [f for f in frames if "dt" in f], poses)
        if skips:
            print("  кадры, не отданные в EKF: " + ", ".join(f"{k} {v}" for k, v in sorted(skips.items())))


if __name__ == "__main__":
    main()
