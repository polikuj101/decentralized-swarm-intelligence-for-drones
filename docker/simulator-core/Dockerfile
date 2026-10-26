FROM ubuntu:22.04
ARG DEBIAN_FRONTEND=noninteractive
ARG PX4_REF=v1.15.4

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates wget gnupg lsb-release git supervisor \
      python3 python3-pip python3-lxml python3-numpy python3-pil \
      libegl1 libgl1-mesa-dri libglx-mesa0 libegl-mesa0 mesa-utils \
    && rm -rf /var/lib/apt/lists/*

RUN wget -q https://packages.osrfoundation.org/gazebo.gpg -O /usr/share/keyrings/pkgs-osrf-archive-keyring.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg] http://packages.osrfoundation.org/gazebo/ubuntu-stable $(lsb_release -cs) main" \
      > /etc/apt/sources.list.d/gazebo-stable.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends gz-harmonic python3-gz-transport13 python3-gz-msgs10 \
 && rm -rf /var/lib/apt/lists/*

# Mesa с исправлением утечки в d3d12 (кэш command signature), см. mesa/README.md
COPY mesa/*.deb /tmp/mesa/
RUN dpkg -i /tmp/mesa/*.deb \
 && apt-mark hold libgl1-mesa-dri libglx-mesa0 libegl-mesa0 libglapi-mesa libgbm1 \
 && dpkg-query -W -f='${Package} ${Version}\n' libgl1-mesa-dri libglx-mesa0 libegl-mesa0 libglapi-mesa libgbm1 | grep -c '+swarm1$' | grep -qx 5 \
 && rm -rf /tmp/mesa

RUN pip3 install --no-cache-dir "fastapi==0.115.*" "uvicorn[standard]==0.30.*" "pydantic==2.*"

# Модели из PX4 (меши x500_base) скопированы локально в контекст сборки
COPY models /opt/px4-gz/models

COPY gen_swarm.py /opt/swarm/gen_swarm.py
COPY gps_jam_bridge.py /opt/gps_jam_bridge.py
COPY range_sim.py /opt/range_sim.py
COPY supervisord.conf /etc/supervisor/conf.d/swarm.conf
COPY entrypoint.sh /usr/local/bin/sim-entrypoint.sh
RUN chmod +x /usr/local/bin/sim-entrypoint.sh

ENV GZ_PARTITION=swarm
ENTRYPOINT ["/usr/local/bin/sim-entrypoint.sh"]
