FROM gcc:14-bookworm

WORKDIR /workspace

# make + check (C 단위 테스트 프레임워크) + cmocka
RUN apt-get update && apt-get install -y --no-install-recommends \
    make \
    check \
    pkg-config \
    libcmocka-dev \
    && rm -rf /var/lib/apt/lists/*

COPY docker/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
