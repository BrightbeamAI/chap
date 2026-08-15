#!/usr/bin/env bash
# Codespaces / devcontainer post-create setup.
#
# After this completes, the user can:
#   cd reference/playground && npm start    -> playground on :8080
#   cd reference/a2a-server-ts && npm start -> A2A server on :9090
#   cd packages/coordinator && npm test     -> 84 tests
#   cd packages/chap-langgraph && python3 -m pytest tests/  -> 10 tests
#
# Idempotent: safe to re-run.

set -euo pipefail

echo "==> Installing TypeScript package deps"
cd /workspaces/chap || cd "$(dirname "$0")/.."
ROOT=$(pwd)

for pkg in packages/coordinator packages/coordinator-mcp packages/coordinator-a2a \
           reference/playground reference/core-plus-review \
           reference/mcp-server-ts reference/a2a-server-ts; do
  if [ -d "$ROOT/$pkg" ] && [ -f "$ROOT/$pkg/package.json" ]; then
    echo "  -> $pkg"
    (cd "$ROOT/$pkg" && npm install --no-audit --no-fund) || {
      echo "     install failed; continuing"
    }
  fi
done

echo ""
echo "==> Installing Python package deps"
cd "$ROOT"
pip install --break-system-packages --quiet -e packages/coordinator-py 2>/dev/null \
  || pip install --quiet -e packages/coordinator-py 2>/dev/null \
  || echo "  (skipped: pip install failed; run manually if needed)"

pip install --break-system-packages --quiet pytest 2>/dev/null \
  || pip install --quiet pytest 2>/dev/null || true

echo ""
echo "==> Building all TypeScript packages"
for pkg in packages/coordinator packages/coordinator-mcp packages/coordinator-a2a; do
  if [ -d "$ROOT/$pkg" ]; then
    (cd "$ROOT/$pkg" && npm run build) 2>/dev/null || true
  fi
done

echo ""
echo "==> Done. Try:"
echo "      cd reference/playground && npm start    # http://localhost:8080"
echo "      cd packages/coordinator && npm test     # 84 tests"
