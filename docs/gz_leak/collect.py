import json, os, re, subprocess, sys, threading, time
DUR = int(sys.argv[1]) if len(sys.argv) > 1 else 600
OUT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/collect.jsonl"
os.chdir("/mnt/c/Users/nurbolatte/Desktop/Swarm of Drones")
CODE = r'''
import rclpy, json, time, math
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data as q
from geometry_msgs.msg import PointStamped
from std_msgs.msg import String
from px4_msgs.msg import VehicleStatus, VehicleLocalPosition
import os
ns = "/" + os.environ["PX4_NS"] + "/fmu"
rclpy.init(); n = Node("collect")
S = {"vio": None, "nav": {}, "ns": None, "lpz": None, "xyv": None, "arm": None}
n.create_subscription(PointStamped, "/swarm/local/vio_disp", lambda m: S.update(vio=[m.point.x, m.point.y, m.point.z]), 10)
n.create_subscription(String, "/swarm/local/nav_status", lambda m: S.update(nav=json.loads(m.data)), 10)
n.create_subscription(VehicleStatus, ns + "/out/vehicle_status", lambda m: S.update(ns=int(m.nav_state), arm=int(m.arming_state)), q)
n.create_subscription(VehicleLocalPosition, ns + "/out/vehicle_local_position", lambda m: S.update(lpz=float(m.z), xyv=bool(m.xy_valid)), q)
last = 0
while True:
    rclpy.spin_once(n, timeout_sec=0.1)
    if time.time() - last >= 1.0:
        last = time.time()
        nv = S["nav"]
        print(json.dumps({"t": time.time(), "vio": S["vio"], "src": nv.get("src"), "state": nv.get("state"), "phase": nv.get("phase"),
                          "nx": nv.get("x"), "ny": nv.get("y"), "nz": nv.get("z"), "susp": nv.get("gps_suspect"),
                          "px4_nav_state": S["ns"], "px4_arm": S["arm"], "lpz": S["lpz"], "xyv": S["xyv"]}), flush=True)
'''
procs = []
f = open(OUT, "w")
lock = threading.Lock()
def reader(i, p):
    for line in p.stdout:
        line = line.strip()
        if line.startswith("{"):
            with lock:
                f.write(json.dumps({"k": "d", "i": i, **json.loads(line)}) + "\n"); f.flush()
for i in range(1, 6):
    p = subprocess.Popen(["docker", "exec", "-i", f"swarm-sitl-drone-agent-{i}-1", "/usr/local/bin/ros-run.sh", "python3", "-u", "-c", CODE],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    procs.append(p); threading.Thread(target=reader, args=(i, p), daemon=True).start()
def truth():
    out = subprocess.run(["docker", "exec", "swarm-sitl-simulator-core-1", "sh", "-c", "timeout 10 gz topic -e -n 1 -t /world/swarm_world/pose/info 2>/dev/null"],
                         capture_output=True, text=True, timeout=30).stdout
    r = {}
    for blk in re.split(r'\npose \{', out):
        m = re.search(r'name: "x500_swarm_(\d)"', blk); p = re.search(r'position \{(.*?)\}', blk, re.S) if m else None
        if m and p:
            v = dict(re.findall(r'(x|y|z): (-?[\d.e+-]+)', p.group(1))); r[int(m.group(1))] = [float(v.get('x', 0)), float(v.get('y', 0)), float(v.get('z', 0))]
    return r
t_end = time.time() + DUR
while time.time() < t_end:
    t0 = time.time(); tr = truth()
    with lock:
        f.write(json.dumps({"k": "g", "t": (t0 + time.time()) / 2, "pos": tr}) + "\n"); f.flush()
    time.sleep(max(0.0, 1.5 - (time.time() - t0)))
for p in procs: p.terminate()
print("collected", OUT)
