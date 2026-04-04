FROM gcc:14-bookworm

WORKDIR /workspace

# cmake + GoogleTest (libgtest-dev on bookworm ships prebuilt static libs)
RUN apt-get update && apt-get install -y --no-install-recommends \
    cmake \
    libgtest-dev \
    libgmock-dev \
    && rm -rf /var/lib/apt/lists/*

COPY docker/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
