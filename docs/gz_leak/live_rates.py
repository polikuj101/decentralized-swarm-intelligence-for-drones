import json, re, subprocess, sys, time, urllib.request
def sh(c, t=60):
    return subprocess.run(c, shell=True, capture_output=True, text=True, timeout=t).stdout
def gz_truth():
    out = sh("docker exec swarm-sitl-simulator-core-1 sh -c 'timeout 10 gz topic -e -n 1 -t /world/swarm_world/pose/info 2>/dev/null'")
    r = {}
    for blk in re.split(r'\npose \{', out):
        m = re.search(r'name: "x500_swarm_(\d)"', blk); p = re.search(r'position \{(.*?)\}', blk, re.S) if m else None
        if m and p:
            v = dict(re.findall(r'(x|y|z): (-?[\d.e+-]+)', p.group(1))); r[int(m.group(1))] = (float(v.get('x', 0)), float(v.get('y', 0)), float(v.get('z', 0)))
    return r
def hud():
    return json.load(urllib.request.urlopen("http://127.0.0.1:8000/api/state", timeout=8))
def rss():
    return sh("ps -eo rss,comm --sort=-rss | awk '$2==\"ruby\"{print int($1/1024); exit}'").strip()
def line(tag):
    s = hud(); tr = gz_truth(); parts = []
    for i, d in sorted(s['drones'].items()):
        n = ((d.get('pkt') or {}).get('nav') or {}); i = int(i); t = tr.get(i)
        e = '-'
        if t and n.get('x') is not None: e = '%.1f' % ((n['x'] - t[0])**2 + (n['y'] - t[1])**2)**0.5
        parts.append('D%d %s/%s armed=%s vio=%s obst=%s truth_z=%s err=%sm' % (i, n.get('state'), n.get('src'), n.get('armed'), n.get('vio_ok'), n.get('obst_min'), '-' if not t else round(t[2], 1), e))
    print(time.strftime('%H:%M:%S'), tag, 'gzRSS=%sMB' % rss()); print('   ' + '\n   '.join(parts), flush=True)
if __name__ == '__main__':
    line(sys.argv[1] if len(sys.argv) > 1 else '')
