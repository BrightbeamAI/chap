#!/usr/bin/env bash
#
# Run the conformance harness against one of the reference servers.
#
#   bash scripts/run-conformance.sh python
#   bash scripts/run-conformance.sh typescript
#
# Both implementations answer the same 23 vectors. Running one language here
# and the other in the same checkout is what keeps them honest: a vector that
# passes on one side and fails on the other is a cross-language divergence,
# not a local bug.
set -euo pipefail

LANG_TARGET="${1:-}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

case "$LANG_TARGET" in
  python)
    PORT=8775
    START=(python3 reference/python/server.py --port "$PORT")
    ;;
  typescript|ts)
    PORT=8776
    START=(npx tsx reference/core-plus-review/server.ts)
    export PORT
    ;;
  *)
    echo "usage: $0 {python|typescript}" >&2
    exit 2
    ;;
esac

if [ ! -d conformance/harness/node_modules ]; then
  echo "Installing harness dependencies."
  (cd conformance/harness && npm install --no-audit --no-fund)
fi

echo "Starting the $LANG_TARGET reference server on port $PORT."
"${START[@]}" >/tmp/chap-conformance-server.log 2>&1 &
SERVER_PID=$!

cleanup() {
  kill "$SERVER_PID" 2>/dev/null || true
  wait "$SERVER_PID" 2>/dev/null || true
}
trap cleanup EXIT

for _ in $(seq 1 40); do
  if curl -fsS -o /dev/null "http://localhost:$PORT/chap" \
      -X POST -H 'content-type: application/json' \
      -d '{"jsonrpc":"2.0","id":"probe","method":"workspace.describe","params":{}}' 2>/dev/null; then
    break
  fi
  sleep 0.25
done

cd conformance/harness
npx tsx harness.ts --url="http://localhost:$PORT/chap"
