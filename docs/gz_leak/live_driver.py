import json, os, subprocess, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from live_rates import line, sh, hud
os.chdir("/mnt/c/Users/nurbolatte/Desktop/Swarm of Drones")
for _ in range(60):
    try:
        if sum(1 for d in hud()['drones'].values() if d.get('pkt')) == 5: break
    except Exception: pass
    time.sleep(10)
req = urllib.request.Request("http://127.0.0.1:8000/api/tasking", json.dumps({"prompt": "Обследовать все квадраты"}).encode(),
                             {"Content-Type": "application/json", "X-HUD": "1"})
urllib.request.urlopen(req, timeout=90).read()
code = """
import rclpy,time
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data as q
from sensor_msgs.msg import Image,LaserScan,Range
rclpy.init();n=Node('hz');c={'cam':0,'ring':0,'down':0}
def mk(k):
    def f(m): c[k]+=1
    return f
n.create_subscription(Image,'/sensors/cam_down/image_raw',mk('cam'),q)
n.create_subscription(LaserScan,'/sensors/lidar_ring/scan',mk('ring'),q)
n.create_subscription(Range,'/sensors/lidar_down/range',mk('down'),q)
t=time.time()
while time.time()-t<10: rclpy.spin_once(n,timeout_sec=0.1)
print({k:round(v/10,1) for k,v in c.items()},'Hz (set: cam 8, ring 5, down 10)')
"""
r = subprocess.run(["docker", "exec", "swarm-sitl-drone-agent-1-1", "/usr/local/bin/ros-run.sh", "python3", "-c", code], capture_output=True, text=True, timeout=60)
print("sensor rates on D1:", (r.stdout.strip().splitlines() or ['?'])[-1], flush=True)
for k in range(1, 15):
    time.sleep(45)
    if k == 5:
        print("== GPS denied on D1, 90 s", flush=True)
        print(sh("./scripts/ewctl.sh gps denied 1 90")[:200], flush=True)
    line("t+%ds%s" % (k * 45, "  [GPS denied D1]" if 5 <= k <= 7 else ""))
print(sh("./scripts/ewctl.sh gps restore")[:100])
print("OOM kills total:", sh("dmesg 2>/dev/null | grep -c 'Out of memory'").strip(), "finished")
