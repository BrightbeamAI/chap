# The CHAP playground: two humans and a scripted agent on one workspace, in a
# web UI on port 8080. docker-compose.yml builds and runs it.
#
# The MCP server is the other image, Dockerfile.mcp, where CHAP_PARTICIPANT
# names the participant it acts as.

FROM node:24-bookworm-slim

WORKDIR /chap

# Install minimal system deps. python3 is here so the chap-langgraph bridge
# examples also work in the same container without a second image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip git ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# npm ci installs exactly what the lock file pins, for the coordinator and the
# playground, and stops if the lock file disagrees with their manifests. The
# playground keeps its state in files, so no install script needs to run:
# --ignore-scripts leaves better-sqlite3, the coordinator's optional store,
# unbuilt.
COPY package.json package-lock.json ./
COPY packages/coordinator/package.json packages/coordinator/
COPY reference/playground/package.json reference/playground/
RUN npm ci --ignore-scripts --no-audit --no-fund \
      --workspace=@brightbeamai/chap-coordinator \
      --workspace=@brightbeamai/playground

COPY packages/coordinator packages/coordinator/
COPY reference/playground reference/playground/

# The playground imports the coordinator's build.
RUN npm run build --workspace=@brightbeamai/chap-coordinator

# The playground writes its state under data/, as the user it runs as.
RUN mkdir -p reference/playground/data && chown node:node reference/playground/data

# Playground listens on 8080 by default.
EXPOSE 8080

ENV CHAP_NO_LLM=1
ENV PORT=8080
# Bind all interfaces so the published port is reachable; docker-compose maps only 127.0.0.1:8080 on the host.
ENV CHAP_HOST=0.0.0.0

USER node
WORKDIR /chap/reference/playground

# The scripted agent means no model download and no GPU. Set CHAP_NO_LLM=0
# and ANTHROPIC_API_KEY, OPENAI_API_KEY or OLLAMA_URL to draft with a model.
CMD ["node", "--import", "tsx", "src/server.ts"]
