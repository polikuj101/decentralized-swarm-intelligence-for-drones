# syntax=docker/dockerfile:1
ARG QWEN_VARIANT=0.5b
ARG LLAMA_CPP_REF=b4600
ARG XRCE_REF=v2.4.2
ARG PX4_MSGS_REF=release/1.15
ARG GGML_FLAGS="-DGGML_NATIVE=OFF -DGGML_AVX2=ON -DGGML_FMA=ON -DGGML_F16C=ON"

# ───────── stage 1: llama.cpp + модель ─────────
FROM ubuntu:22.04 AS llama
ARG DEBIAN_FRONTEND=noninteractive
ARG LLAMA_CPP_REF
ARG QWEN_VARIANT
ARG GGML_FLAGS
RUN echo "precedence ::ffff:0:0/96  100" >> /etc/gai.conf \
 && apt-get update && apt-get install -y --no-install-recommends \
      build-essential cmake git ca-certificates curl tar && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /src \
 && curl -4 -fL --retry 5 --retry-delay 3 "https://github.com/ggml-org/llama.cpp/archive/refs/tags/${LLAMA_CPP_REF}.tar.gz" | tar -xz --strip-components=1 -C /src \
 && cmake -S /src -B /src/build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
      -DLLAMA_CURL=OFF ${GGML_FLAGS} \
 && cmake --build /src/build --target llama-server -j8
RUN case "${QWEN_VARIANT}" in \
      0.5b) R=Qwen/Qwen2.5-0.5B-Instruct-GGUF; F=qwen2.5-0.5b-instruct-q4_k_m.gguf ;; \
      1.5b) R=Qwen/Qwen2.5-1.5B-Instruct-GGUF; F=qwen2.5-1.5b-instruct-q4_k_m.gguf ;; \
      *) echo "QWEN_VARIANT must be 0.5b or 1.5b"; exit 1 ;; esac \
 && curl -4 -fL --retry 5 --retry-delay 3 -o /model.gguf "https://huggingface.co/${R}/resolve/main/${F}" \
 && test "$(stat -c %s /model.gguf)" -gt 100000000

# ───────── stage 2: XRCE-агент, mavlink-router, px4_msgs ─────────
FROM ros:humble-ros-base-jammy AS rosbuild
ARG DEBIAN_FRONTEND=noninteractive
ARG XRCE_REF
ARG PX4_MSGS_REF
RUN echo "precedence ::ffff:0:0/96  100" >> /etc/gai.conf \
 && apt-get update && apt-get install -y --no-install-recommends \
      build-essential cmake git ninja-build meson pkg-config libssl-dev systemd \
      python3-colcon-common-extensions && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /xrce \
 && curl -4 -fL --retry 5 --retry-delay 3 "https://github.com/eProsima/Micro-XRCE-DDS-Agent/archive/refs/tags/${XRCE_REF}.tar.gz" | tar -xz --strip-components=1 -C /xrce \
 && . /opt/ros/humble/setup.sh \
 && cmake -S /xrce -B /xrce/build -DCMAKE_BUILD_TYPE=Release \
      -DUAGENT_SUPERBUILD=OFF -DUAGENT_P2P_PROFILE=OFF \
      -DUAGENT_USE_SYSTEM_FASTDDS=ON -DUAGENT_USE_SYSTEM_FASTCDR=ON \
 && cmake --build /xrce/build -j8 && cmake --install /xrce/build
RUN git clone --depth 1 --recurse-submodules --shallow-submodules \
      https://github.com/mavlink-router/mavlink-router.git /mr \
 && cd /mr && meson setup build . --buildtype=release --prefix /usr/local && ninja -C build install
RUN mkdir -p /px4_ws/src/px4_msgs \
 && curl -4 -fL --retry 5 --retry-delay 3 "https://github.com/PX4/px4_msgs/archive/refs/heads/${PX4_MSGS_REF}.tar.gz" | tar -xz --strip-components=1 -C /px4_ws/src/px4_msgs \
 && cd /px4_ws && . /opt/ros/humble/setup.sh \
 && colcon build --packages-select px4_msgs --parallel-workers 4 --cmake-args -DCMAKE_BUILD_TYPE=Release

# ───────── stage 3: итоговый образ ─────────
FROM ros:humble-ros-base-jammy
ARG DEBIAN_FRONTEND=noninteractive
ARG QWEN_VARIANT
RUN echo "precedence ::ffff:0:0/96  100" >> /etc/gai.conf \
 && apt-get update && apt-get install -y --no-install-recommends \
      supervisor gettext-base iproute2 nftables iputils-ping iperf3 curl tcpdump \
      python3-pip python3-opencv python3-numpy lsb-release wget gnupg ca-certificates \
 && wget -4 -q https://packages.osrfoundation.org/gazebo.gpg -O /usr/share/keyrings/pkgs-osrf-archive-keyring.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg] http://packages.osrfoundation.org/gazebo/ubuntu-stable $(lsb_release -cs) main" \
      > /etc/apt/sources.list.d/gazebo-stable.list \
 && apt-get update && apt-get install -y --no-install-recommends python3-gz-transport13 python3-gz-msgs10 \
 && rm -rf /var/lib/apt/lists/*
RUN pip3 install --no-cache-dir "fastapi==0.115.*" "uvicorn[standard]==0.30.*" "pydantic==2.*" \
      "pymavlink==2.4.*" "eclipse-zenoh>=1.0,<2"

COPY --from=rosbuild /usr/local /usr/local
COPY --from=rosbuild /px4_ws/install /opt/px4_ws/install
COPY --from=llama /src/build/bin/llama-server /usr/local/bin/llama-server
COPY --from=llama /model.gguf /opt/models/model.gguf
RUN echo "/opt/ros/humble/lib" > /etc/ld.so.conf.d/ros.conf && ldconfig

COPY grammar/actions.gbnf /opt/grammar/actions.gbnf
COPY dds/fastdds_loopback.xml /opt/dds/fastdds_loopback.xml
COPY ew/ew_agent.py /opt/ew/ew_agent.py
COPY ew/ew-tc.sh /usr/local/bin/ew-tc.sh
COPY sensors/gz_sensor_bridge.py /opt/sensors/gz_sensor_bridge.py
COPY swarm/ /opt/swarm_agent/swarm/
COPY config/mission.json /opt/swarm/config/mission.json
COPY mavlink-router/main.conf.tpl /etc/mavlink-router/main.conf.tpl
COPY supervisord.conf /etc/supervisor/conf.d/swarm.conf
COPY ros-run.sh /usr/local/bin/ros-run.sh
COPY entrypoint.sh /usr/local/bin/agent-entrypoint.sh
RUN chmod +x /usr/local/bin/ew-tc.sh /usr/local/bin/ros-run.sh /usr/local/bin/agent-entrypoint.sh

ENV QWEN_VARIANT=${QWEN_VARIANT} RMW_IMPLEMENTATION=rmw_fastrtps_cpp
ENTRYPOINT ["/usr/local/bin/agent-entrypoint.sh"]
