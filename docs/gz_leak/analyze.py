import json, math, sys, statistics as st, collections
path = sys.argv[1]
G, D = [], collections.defaultdict(list)
for l in open(path):
    r = json.loads(l)
    (G.append(r) if r["k"] == "g" else D[r["i"]].append(r))
NAV = {0:"MANUAL",1:"ALTCTL",2:"POSCTL",3:"AUTO_MISSION",4:"AUTO_LOITER",5:"AUTO_RTL",12:"DESCEND",13:"TERMINATION",14:"OFFBOARD",15:"STAB",17:"AUTO_TAKEOFF",18:"AUTO_LAND"}
def near(i, t):
    best = None
    for r in D[i]:
        if best is None or abs(r["t"] - t) < abs(best["t"] - t): best = r
    return best if best and abs(best["t"] - t) < 1.0 else None
print("samples: truth", len(G), "per-drone", {i: len(D[i]) for i in sorted(D)})
# 1. VIO vs truth over ~12 s windows
print("\n== VIO vs Gazebo: смещение за ~12 с окна, где борт прошёл > 6 м")
for i in sorted(D):
    rows = []
    for g in G:
        r = near(i, g["t"])
        if r and r["vio"] and str(i) in g["pos"]: rows.append((g["t"], g["pos"][str(i)], r["vio"]))
    ratios, angs = [], []
    k = 0
    while k < len(rows):
        j = k
        while j < len(rows) and rows[j][0] - rows[k][0] < 12: j += 1
        if j >= len(rows): break
        tx, ty = rows[j][1][0] - rows[k][1][0], rows[j][1][1] - rows[k][1][1]
        vx, vy = rows[j][2][0] - rows[k][2][0], rows[j][2][1] - rows[k][2][1]   # vio_disp: x=E, y=N
        dt_ = math.hypot(tx, ty)
        if dt_ > 6:
            ratios.append(math.hypot(vx, vy) / dt_)
            a = math.degrees(math.atan2(vy, vx) - math.atan2(ty, tx)); a = (a + 180) % 360 - 180; angs.append(a)
        k = j
    if ratios:
        print(f" D{i}: окон {len(ratios)}, масштаб VIO/истина: медиана {st.median(ratios):.2f}, min {min(ratios):.2f}, max {max(ratios):.2f}; угол ошибки: медиана {st.median(angs):.0f}°, |max| {max(abs(a) for a in angs):.0f}°")
    else:
        print(f" D{i}: нет окон с движением")
# 2. режимы PX4
print("\n== режим PX4 (nav_state) по бортам, доля секунд в воздухе (truth z > 0.8)")
for i in sorted(D):
    c = collections.Counter()
    for g in G:
        r = near(i, g["t"])
        if r and str(i) in g["pos"] and g["pos"][str(i)][2] > 0.8: c[NAV.get(r["px4_nav_state"], r["px4_nav_state"])] += 1
    print(f" D{i}:", dict(c))
# 3. борт у земли, пока flight_ctrl считает, что летит
print("\n== аномалии: truth z < 0.5, а flight_ctrl в NAV/HOLD/DWELL (первые 4 на борт)")
for i in sorted(D):
    shown = 0
    for g in G:
        r = near(i, g["t"])
        if r and str(i) in g["pos"] and g["pos"][str(i)][2] < 0.5 and r["state"] in ("NAV", "HOLD", "DWELL") and r["phase"] == "FLY":
            if shown < 4:
                print(f" D{i} t={g['t']-G[0]['t']:.0f}s truth_z={g['pos'][str(i)][2]:.2f} nav_z={r['nz']} lp_z={r['lpz']} px4={NAV.get(r['px4_nav_state'], r['px4_nav_state'])} armed={r['px4_arm']} xy_valid={r['xyv']} src={r['src']} state={r['state']}")
            shown += 1
    print(f" D{i}: всего таких секунд {shown}")
# 4. переходы на VIO / подозрение GPS и ошибка оценки
print("\n== переходы источника позиции и подозрение на GPS (ошибка оценки относительно Gazebo)")
for i in sorted(D):
    prev = None; ev = []
    for r in D[i]:
        key = (r["src"], bool(r["susp"]))
        if key != prev and prev is not None:
            g = min(G, key=lambda g: abs(g["t"] - r["t"]))
            e = None
            if str(i) in g["pos"] and r["nx"] is not None: e = round(math.hypot(r["nx"] - g["pos"][str(i)][0], r["ny"] - g["pos"][str(i)][1]), 1)
            ev.append(f"t={r['t']-G[0]['t']:.0f}s {prev}->{key} err={e}")
        prev = key
    print(f" D{i}:", "; ".join(ev[:8]) or "без переходов", "(+%d)" % max(0, len(ev) - 8) if len(ev) > 8 else "")
