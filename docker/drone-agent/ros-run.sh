#!/usr/bin/env bash
# ros-run.sh <команда…> — окружение ROS 2 + px4_msgs + пакет swarm
set -eo pipefail
source /opt/ros/humble/setup.bash
source /opt/px4_ws/install/setup.bash
export PYTHONPATH="/opt/swarm_agent:${PYTHONPATH:-}"
exec "$@"
