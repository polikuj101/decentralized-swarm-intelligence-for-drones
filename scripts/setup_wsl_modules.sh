#!/usr/bin/env bash
set -e
cat << 'EOF' > /etc/modprobe.d/wsl-netem.conf
install sch_netem /bin/true
install sch_prio /bin/true
EOF
modprobe sch_netem
modprobe sch_prio
modprobe cls_u32
modprobe nf_tables
echo "All modules ok!"
