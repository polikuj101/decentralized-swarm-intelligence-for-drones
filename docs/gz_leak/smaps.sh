#!/usr/bin/env bash
C=swarm-sitl-simulator-core-1
snap(){ docker exec $C sh -c 'P=$(pgrep -x ruby | head -1); echo PID=$P; grep -E "VmRSS|RssAnon|RssFile|RssShmem|VmSwap|Threads" /proc/$P/status; cat /proc/$P/smaps' > $1; }
snap /tmp/sm1.txt; sleep 90; snap /tmp/sm2.txt
python3 - <<'PY'
import re, collections
def parse(p):
    d = collections.OrderedDict(); head = {}; cur = None
    for l in open(p):
        if l.startswith(("PID=", "VmRSS", "RssAnon", "RssFile", "RssShmem", "VmSwap", "Threads")):
            k, v = l.split(":", 1) if ":" in l else l.split("=", 1); head[k.strip()] = v.strip(); continue
        m = re.match(r"^([0-9a-f]+)-([0-9a-f]+) (\S+) \S+ \S+ \S+\s*(.*)$", l)
        if m: cur = (m.group(1), m.group(3), m.group(4) or "[anon]"); d[cur] = {"Rss": 0}; continue
        if cur and l.startswith("Rss:"): d[cur]["Rss"] = int(l.split()[1])
    return head, d
h1, a = parse("/tmp/sm1.txt"); h2, b = parse("/tmp/sm2.txt")
print("status t0:", h1); print("status t+90s:", h2)
grow = []
for k, v in b.items():
    grow.append((v["Rss"] - a.get(k, {"Rss": 0})["Rss"], k, v["Rss"]))
grow.sort(reverse=True)
tot = sum(g[0] for g in grow); print("\nсуммарный прирост Rss по маппингам, КБ:", tot)
byname = collections.Counter()
for g, k, r in grow: byname[k[2] if k[2] != "[anon]" else "[anon]"] += g
print("\nприрост по объектам (КБ):"); [print(f"  {v:>8}  {n}") for n, v in byname.most_common(8)]
print("\nтоп-5 растущих маппингов (адрес, права, объект, прирост КБ, Rss КБ):"); [print("  ", k[0], k[1], k[2], g, r) for g, k, r in grow[:5]]
PY
