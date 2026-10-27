FROM ubuntu:22.04
ARG DEBIAN_FRONTEND=noninteractive
ARG LLAMA_CPP_REF
RUN echo "precedence ::ffff:0:0/96  100" >> /etc/gai.conf \
 && apt-get update && apt-get install -y --no-install-recommends build-essential cmake git ca-certificates curl tar libgomp1 && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /src && curl -4 -fL --retry 5 -o /tmp/llama.tar.gz "https://github.com/ggml-org/llama.cpp/archive/refs/tags/${LLAMA_CPP_REF}.tar.gz" \
 && sha256sum /tmp/llama.tar.gz | tee /src/ARCHIVE_SHA256 \
 && tar -xzf /tmp/llama.tar.gz --strip-components=1 -C /src && rm /tmp/llama.tar.gz \
 && cmake -S /src -B /src/build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DLLAMA_CURL=OFF \
 && cmake --build /src/build --target llama-server -j6
ENTRYPOINT ["/src/build/bin/llama-server"]