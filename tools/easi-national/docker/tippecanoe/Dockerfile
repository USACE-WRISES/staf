# tippecanoe (felt/tippecanoe) for the EASI national tile stage.
# Build once:  docker build -t staf-tippecanoe tools/easi-national/docker/tippecanoe
# Run:         docker run --rm -v "<dir>:/data" staf-tippecanoe tippecanoe --version
# python:3.12-slim is Debian bookworm underneath and is usually already local
# (the debian:bookworm-slim pull stalled on this workstation, 2026-09-10).
FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential git ca-certificates \
        libsqlite3-dev zlib1g-dev \
 && git clone --depth 1 https://github.com/felt/tippecanoe.git /src \
 && cd /src && make -j"$(nproc)" && make install \
 && cd / && rm -rf /src \
 && apt-get purge -y build-essential git && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /data
