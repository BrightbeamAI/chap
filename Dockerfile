FROM node:20-bookworm-slim

WORKDIR /chap

# Install minimal system deps. python3 is here so the chap-langgraph
# bridge examples also work in the same container without a second image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip git ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# Bring in only the manifests first so layer caching works.
COPY packages/coordinator/package.json          packages/coordinator/
COPY packages/coordinator-mcp/package.json      packages/coordinator-mcp/
COPY packages/coordinator-a2a/package.json      packages/coordinator-a2a/
COPY reference/playground/package.json          reference/playground/

# Then the source so each install can resolve its file: deps.
COPY packages/coordinator     packages/coordinator/
COPY packages/coordinator-mcp packages/coordinator-mcp/
COPY packages/coordinator-a2a packages/coordinator-a2a/
COPY reference/playground     reference/playground/

# Install playground deps. The coordinator is a file: dep so this also
# wires up the workspace via symlink, no separate install needed for it.
RUN cd reference/playground && npm install --no-audit --no-fund

# Playground listens on 8080 by default.
EXPOSE 8080

ENV CHAP_NO_LLM=1
ENV PORT=8080

WORKDIR /chap/reference/playground

# The mock-drafter mode means no model download, no Ollama, no GPU.
# Override CHAP_NO_LLM=0 + OLLAMA_URL=http://host.docker.internal:11434
# to point at a real local Ollama instance.
CMD ["npx", "tsx", "src/server.ts"]
