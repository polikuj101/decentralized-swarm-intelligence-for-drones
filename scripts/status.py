import urllib.request
import json

data = json.loads(urllib.request.urlopen('http://127.0.0.1:8888/api/state').read())
drones = data.get('drones', {})
for k in sorted(drones.keys()):
    pkt = drones[k].get('pkt')
    if not pkt:
        print(f"Drone {k}: No telemetry pkt yet")
        continue
    nav = pkt.get('nav', {})
    swarm = pkt.get('swarm', {})
    print(f"Drone {k}: Phase={nav.get('phase')} State={nav.get('state')} Armed={nav.get('armed')} Pos=({nav.get('x')}, {nav.get('y')}, {nav.get('z')}) Src={nav.get('src')} Task={swarm.get('task_name')} Done={swarm.get('done')}")
