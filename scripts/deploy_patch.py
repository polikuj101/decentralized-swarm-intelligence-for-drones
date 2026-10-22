#!/usr/bin/env python3
import subprocess

for i in range(1, 6):
    cid = f"swarm-sitl-drone-agent-{i}-1"
    for f in ["mesh.py", "coordinator.py", "features.py", "cbaa.py", "flight_ctrl.py"]:
        subprocess.check_call(["docker", "cp", f"docker/drone-agent/swarm/{f}", f"{cid}:/opt/swarm_agent/swarm/{f}"])
    subprocess.check_call(["docker", "exec", cid, "supervisorctl", "restart", "flight"])
    subprocess.check_call(["docker", "exec", cid, "supervisorctl", "restart", "coordinator"])
    print(f"Drone {i} updated and coordinator + flight restarted.")
