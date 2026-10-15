#!/usr/bin/env bash
set -euo pipefail
mkdir -p /opt/swarm/generated /opt/swarm/scene
python3 /opt/swarm/gen_swarm.py          # модели, текстура, мир, scene.json (размер роя берётся из SWARM_SIZE)
export GZ_SIM_RESOURCE_PATH="/opt/px4-gz/models:/opt/swarm/generated/models"
exec /usr/bin/supervisord -n -c /etc/supervisor/supervisord.conf
