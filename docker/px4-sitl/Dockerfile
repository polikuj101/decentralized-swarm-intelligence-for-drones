FROM ubuntu:22.04
ARG DEBIAN_FRONTEND=noninteractive
ARG PX4_REF=v1.15.4
ARG BUILD_JOBS=8

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl wget gnupg lsb-release git sudo build-essential cmake ninja-build \
      python3 python3-pip python3-dev python3-setuptools python3-wheel \
      iproute2 procps netcat-openbsd gettext-base \
    && rm -rf /var/lib/apt/lists/*

# Gazebo Harmonic (нужны dev-библиотеки gz-transport/gz-msgs/gz-sim для сборки gz_bridge и gz_plugins)
RUN wget -q https://packages.osrfoundation.org/gazebo.gpg -O /usr/share/keyrings/pkgs-osrf-archive-keyring.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg] http://packages.osrfoundation.org/gazebo/ubuntu-stable $(lsb_release -cs) main" \
      > /etc/apt/sources.list.d/gazebo-stable.list \
 && apt-get update && apt-get install -y --no-install-recommends gz-harmonic \
 && rm -rf /var/lib/apt/lists/*

# PX4 нужного тега + системные зависимости самого PX4 (без NuttX и без установки симуляторов)
RUN git clone --recursive --shallow-submodules --depth 1 --branch ${PX4_REF} \
      https://github.com/PX4/PX4-Autopilot.git /opt/px4 \
 && bash /opt/px4/Tools/setup/ubuntu.sh --no-nuttx --no-sim-tools \
 && rm -rf /var/lib/apt/lists/*

# Патчи роя (применяются по якорям; при несовпадении upstream сборка падает)
COPY apply_swarm_patches.py /opt/swarm-patches/apply_swarm_patches.py
COPY airframes/ /opt/swarm-patches/airframes/
COPY params/ /opt/swarm-patches/params/
RUN python3 /opt/swarm-patches/apply_swarm_patches.py /opt/px4

RUN cd /opt/px4 && make px4_sitl_default -j${BUILD_JOBS}

COPY entrypoint.sh /usr/local/bin/px4-entrypoint.sh
COPY px4-shell /usr/local/bin/px4-shell
COPY verify_params.sh /usr/local/bin/verify_params.sh
RUN chmod +x /usr/local/bin/px4-entrypoint.sh /usr/local/bin/px4-shell /usr/local/bin/verify_params.sh

ENV GZ_PARTITION=swarm HEADLESS=1
ENTRYPOINT ["/usr/local/bin/px4-entrypoint.sh"]
