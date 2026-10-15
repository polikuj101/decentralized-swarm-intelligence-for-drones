#!/usr/bin/env bash
# entrypoint.sh
set -euo pipefail
# U10: multicast на loopback для DDS-discovery внутри борта (нужен NET_ADMIN, он есть)
ip link set lo multicast on || true
ip route add 224.0.0.0/4 dev lo 2>/dev/null || true
echo "/opt/ros/humble/lib" > /etc/ld.so.conf.d/ros.conf && ldconfig
mkdir -p /etc/mavlink-router
envsubst '${MAVLINK_ONBOARD_PORT}' < /etc/mavlink-router/main.conf.tpl > /etc/mavlink-router/main.conf
exec /usr/bin/supervisord -n -c /etc/supervisor/supervisord.conf
