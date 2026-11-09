#!/usr/bin/env python3
import subprocess
import time

for i in range(1, 6):
    fc = f"swarm-sitl-fc-{i}-1"
    port = 8888 + (i - 1)
    agent_ip = f"172.30.0.{20 + i}"
    print(f"Configuring {fc} for {agent_ip}:{port}...")
    subprocess.run(["docker", "cp", "docker/px4-sitl/airframes/4901_gz_x500_swarm", f"{fc}:/opt/px4/ROMFS/px4fmu_common/init.d-posix/airframes/4901_gz_x500_swarm"], check=False)
    subprocess.run(["docker", "exec", fc, "px4-shell", "param", "set", "UXRCE_DDS_PTCFG", "1"], check=False)
    subprocess.run(["docker", "exec", fc, "px4-shell", "uxrce_dds_client", "stop"], check=False)
    time.sleep(1)
    subprocess.run(["docker", "exec", fc, "px4-shell", "uxrce_dds_client", "start", "-t", "udp", "-h", agent_ip, "-p", str(port), "-n", f"px4_{i}"], check=False)
    print(f"{fc} configured with UXRCE_DDS_PTCFG=1 on port {port}.")
