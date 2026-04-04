FROM node:20-alpine

WORKDIR /workspace

RUN npm install -g jest vitest 2>/dev/null || true

COPY docker/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
