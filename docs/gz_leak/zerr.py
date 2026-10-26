import json, sys, statistics as st, collections
G, D = [], collections.defaultdict(list)
for l in open(sys.argv[1]):
    r = json.loads(l); (G.append(r) if r["k"] == "g" else D[r["i"]].append(r))
def near(i, t):
    b = min(D[i], key=lambda r: abs(r["t"] - t)); return b if abs(b["t"] - t) < 1.0 else None
print("ошибка высоты оценки (nav_z) относительно Gazebo (truth_z - 0.13), борт в воздухе (truth_z > 0.6)")
for i in sorted(D):
    es, bysrc = [], collections.defaultdict(list)
    for g in G:
        r = near(i, g["t"])
        if r and r["nz"] is not None and g["pos"][str(i)][2] > 0.6:
            e = r["nz"] - (g["pos"][str(i)][2] - 0.13); es.append(e); bysrc[r["src"]].append(e)
    if es:
        print(f" D{i}: n={len(es)} медиана {st.median(es):+.2f} м, |e|>1 м: {sum(1 for e in es if abs(e)>1)}, |e|>2 м: {sum(1 for e in es if abs(e)>2)}, max {max(es,key=abs):+.2f}; по src:",
              {k: (len(v), round(st.median(v), 2)) for k, v in bysrc.items()})
print("\nэпизоды |ошибка| > 1.5 м (D, время, truth_z, nav_z, src, state)")
for i in sorted(D):
    last = -99
    for g in G:
        r = near(i, g["t"])
        if r and r["nz"] is not None:
            e = r["nz"] - (g["pos"][str(i)][2] - 0.13)
            if abs(e) > 1.5 and g["t"] - last > 8 and g["pos"][str(i)][2] > 0.3:
                last = g["t"]; print(f"  D{i} t={g['t']-G[0]['t']:.0f}s truth_z={g['pos'][str(i)][2]:.2f} nav_z={r['nz']:.2f} src={r['src']} state={r['state']} phase={r['phase']}")
