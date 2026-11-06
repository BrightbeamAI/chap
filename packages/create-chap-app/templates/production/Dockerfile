# One image for both services; docker-compose.yml picks the command.
#
# The install stage carries the build tools better-sqlite3 needs on a
# platform with no prebuilt binary, and installs exactly the versions
# package-lock.json pins: `npm install` on the host writes the lock file, and
# committing it keeps every build on the same packages. The runtime stage
# carries the installed modules and the project, and runs as the
# unprivileged node user.

FROM node:24-bookworm-slim AS install
WORKDIR /app
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 make g++ \
 && rm -rf /var/lib/apt/lists/*
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

FROM node:24-bookworm-slim
WORKDIR /app
ENV NODE_ENV=production
COPY --from=install /app/node_modules ./node_modules
COPY package.json chap.config.json server.mjs agent.mjs doctor.mjs messages.csv ./
RUN mkdir -p /app/data /app/keys /app/outbox /app/analytics && chown -R node:node /app
USER node
EXPOSE 8790
CMD ["node", "server.mjs"]
