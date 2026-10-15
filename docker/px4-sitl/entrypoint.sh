#!/usr/bin/env bash
set -euo pipefail

: "${PX4_INSTANCE:?}" "${PX4_SYS_ID:?}" "${PX4_GZ_MODEL_NAME:?}" \
  "${PX4_ONBOARD_HOST:?}" "${PX4_XRCE_HOST:?}"

export PX4_SYS_AUTOSTART="${PX4_SYS_AUTOSTART:-4901}"
export PX4_SIMULATOR=gz PX4_GZ_STANDALONE=1 HEADLESS=1
export PX4_GZ_WORLD="${PX4_GZ_WORLD:-swarm_world}"
export PX4_XRCE_PORT="${PX4_XRCE_PORT:-8888}"
BUILD=/opt/px4/build/px4_sitl_default

# 1. Чистое состояние при каждом старте: SIM_GPS_JAM и прочие параметры не «залипают» между перезапусками
find "$BUILD/rootfs" -maxdepth 3 \( -name 'parameters*' -o -name dataman -o -name 'eeprom*' \) \
     -exec rm -rf {} + 2>/dev/null || true

# 2. Ждём, пока simulator-core опубликует модель этого борта (до 3 минут)
topic="/world/${PX4_GZ_WORLD}/model/${PX4_GZ_MODEL_NAME}/link/base_link/sensor/imu_sensor/imu"
ready=0
for _ in $(seq 1 90); do
  if gz topic -l 2>/dev/null | grep -qx "$topic"; then ready=1; break; fi
  sleep 2
done
if [ "$ready" != 1 ]; then
  echo "FATAL: $topic не виден через gz-transport (GZ_PARTITION=${GZ_PARTITION:-?} GZ_IP=${GZ_IP:-?})" >&2
  exit 1
fi

# 3. Запуск экземпляра в standalone-режиме (gz sim уже работает в simulator-core)
cd /opt/px4
exec ./build/px4_sitl_default/bin/px4 -d -i "$PX4_INSTANCE"
