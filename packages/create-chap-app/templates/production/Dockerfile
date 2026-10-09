# One image for both services; docker-compose.yml picks the command.
#
# The install stage carries the build tools better-sqlite3 needs on a
# platform with no prebuilt binary. The runtime stage carries the installed
# modules and the project, and runs as the unprivileged node user.

FROM node:20-bookworm-slim AS install
WORKDIR /app
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 make g++ \
 && rm -rf /var/lib/apt/lists/*
COPY package.json ./
RUN npm install --omit=dev

FROM node:20-bookworm-slim
WORKDIR /app
ENV NODE_ENV=production
COPY --from=install /app/node_modules ./node_modules
COPY package.json chap.config.json server.mjs agent.mjs keys.mjs doctor.mjs messages.csv ./
COPY desk ./desk
COPY lib/oidc.mjs lib/profiles-diff.mjs lib/providers.mjs ./lib/
RUN mkdir -p /app/data /app/keys /app/outbox && chown -R node:node /app
USER node
EXPOSE 8790
CMD ["node", "server.mjs"]
