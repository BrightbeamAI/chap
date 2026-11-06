#!/usr/bin/env bash
# Install each framework bridge with its framework's current release, in a
# virtual environment of its own, then run the bridge's tests and examples.
#
#   scripts/test-bridges.sh                    every bridge
#   scripts/test-bridges.sh chap-ag2 ...       the named bridges only
#
# The Python CI job runs the bridges' tests with no framework installed, and
# skips every test that drives a real framework. This runs them with one.
# chap-ag2 runs twice: with AG2 0.x, which its extra installs, and with AG2
# 1.x, for tool_approval.
#
# PYTHON names the interpreter (python3 by default). uv is used when it is on
# PATH, and pip otherwise. One environment exists at a time, and each is
# removed when its run ends.
set -uo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
PYTHON=${PYTHON:-python3}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# label | bridge | framework distribution | the bridge with its extras | more requirements | run the examples | more scripts
RUNS=(
  "chap-langgraph|chap-langgraph|langgraph|packages/chap-langgraph[langgraph,dev]||yes|"
  "chap-pydantic-ai|chap-pydantic-ai|pydantic-ai|packages/chap-pydantic-ai[pydantic-ai,dev]||yes|scenarios/01-solo-dev-overrides/system/system.py"
  "chap-llama-index|chap-llama-index|llama-index-workflows|packages/chap-llama-index[llama-index,dev]||yes|"
  "chap-ag2|chap-ag2|ag2|packages/chap-ag2[ag2,dev]||yes|"
  "chap-ag2 on AG2 1.x|chap-ag2|ag2|packages/chap-ag2[dev]|ag2>=1.2,<2|no|"
  "chap-google-adk|chap-google-adk|google-adk|packages/chap-google-adk[google-adk,dev]||yes|"
)

install() {
  local venv=$1; shift
  if command -v uv >/dev/null 2>&1; then
    uv venv -q --python "$PYTHON" "$venv" && VIRTUAL_ENV="$venv" uv pip install -q "$@"
  else
    "$PYTHON" -m venv "$venv" && "$venv/bin/python" -m pip install -q --disable-pip-version-check "$@"
  fi
}

FAILED=()
SUMMARY=()

run() {
  local label=$1 bridge=$2 dist=$3 spec=$4 more=$5 examples=$6 scripts=$7
  local venv="$WORK/venv" passed=yes version script
  echo
  echo "== $label"
  local reqs=(-e packages/coordinator-py -e "$spec")
  [ -n "$more" ] && reqs+=("$more")
  if ! (cd "$ROOT" && install "$venv" "${reqs[@]}"); then
    FAILED+=("$label: the install")
    rm -rf "$venv"
    return
  fi
  version=$("$venv/bin/python" -c "from importlib.metadata import version; print(version('$dist'))")
  echo "   $dist $version"

  (cd "$ROOT" && "$venv/bin/python" -m pytest "packages/$bridge/tests" -q -p no:cacheprovider) \
    || { passed=no; FAILED+=("$label: the tests"); }

  local programs=()
  if [ "$examples" = yes ]; then
    for script in "$ROOT/packages/$bridge"/examples/*.py; do programs+=("$script"); done
  fi
  [ -n "$scripts" ] && programs+=("$ROOT/$scripts")
  for script in "${programs[@]+"${programs[@]}"}"; do
    echo "   ${script#"$ROOT"/}"
    if ! (cd "$(dirname "$script")" && "$venv/bin/python" "$script" >"$WORK/out.txt" 2>&1); then
      passed=no
      FAILED+=("$label: ${script#"$ROOT"/}")
      cat "$WORK/out.txt"
    fi
  done

  SUMMARY+=("$label: $dist $version, $([ "$passed" = yes ] && echo passed || echo FAILED)")
  rm -rf "$venv"
}

for name in "$@"; do
  case " chap-langgraph chap-pydantic-ai chap-llama-index chap-ag2 chap-google-adk " in
    *" $name "*) ;;
    *) echo "No bridge is called $name." >&2; exit 2 ;;
  esac
done

for entry in "${RUNS[@]}"; do
  IFS='|' read -r label bridge dist spec more examples scripts <<<"$entry"
  if [ "$#" -gt 0 ]; then
    chosen=no
    for name in "$@"; do [ "$name" = "$bridge" ] && chosen=yes; done
    [ "$chosen" = yes ] || continue
  fi
  run "$label" "$bridge" "$dist" "$spec" "$more" "$examples" "$scripts"
done

echo
echo "== summary"
for line in "${SUMMARY[@]+"${SUMMARY[@]}"}"; do echo "   $line"; done
for line in "${FAILED[@]+"${FAILED[@]}"}"; do echo "   failed: $line"; done
[ "${#FAILED[@]}" -eq 0 ]
