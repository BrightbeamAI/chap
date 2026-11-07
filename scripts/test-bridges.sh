#!/usr/bin/env bash
# Install each framework bridge with its framework's current release, in a
# virtual environment of its own, then run the bridge's tests, its README's
# quick start as written, and its examples.
#
#   scripts/test-bridges.sh                    every bridge
#   scripts/test-bridges.sh chap-ag2 ...       the named bridges only
#
# The Python CI job runs the bridges' tests with no framework installed, and
# skips every test that drives a real framework. Here a skipped test is a
# failure: it means the framework, or what its tests need, did not install
# or did not import. chap-ag2 runs twice: with AG2 0.x, which its extra
# installs, for request_reply, and with AG2 1.x, for tool_approval.
#
# PYTHON names the interpreter (python3 by default). uv is used when it is on
# PATH, and pip otherwise. One environment exists at a time, and each is
# removed when its run ends.
set -uo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
PYTHON=${PYTHON:-python3}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# label | bridge | framework distribution | the bridge with its extras | more requirements | tests | quick start and examples | more scripts
RUNS=(
  "chap-langgraph|chap-langgraph|langgraph|packages/chap-langgraph[langgraph,dev]||tests|yes|"
  "chap-pydantic-ai|chap-pydantic-ai|pydantic-ai|packages/chap-pydantic-ai[pydantic-ai,dev]||tests|yes|scenarios/01-solo-dev-overrides/system/system.py"
  "chap-llama-index|chap-llama-index|llama-index-workflows|packages/chap-llama-index[llama-index,dev]||tests|yes|"
  "chap-ag2|chap-ag2|ag2|packages/chap-ag2[ag2,dev]||tests/test_bridge.py tests/test_conversation.py|yes|"
  "chap-ag2 on AG2 1.x|chap-ag2|ag2|packages/chap-ag2[dev]|ag2>=1.2,<2|tests/test_bridge.py tests/test_tool_approval.py|no|"
  "chap-google-adk|chap-google-adk|google-adk|packages/chap-google-adk[google-adk,dev]||tests|yes|"
)

install() {
  local venv=$1; shift
  if command -v uv >/dev/null 2>&1; then
    uv venv -q --python "$PYTHON" "$venv" && VIRTUAL_ENV="$venv" uv pip install -q "$@"
  else
    "$PYTHON" -m venv "$venv" && "$venv/bin/python" -m pip install -q --disable-pip-version-check "$@"
  fi
}

# The first python block under "## Quick start" in a README, as written.
quick_start() {
  "$1/bin/python" - "$2" <<'PY'
import re
import sys

text = open(sys.argv[1], encoding="utf-8").read()
section = text[text.index("## Quick start"):]
print(re.search(r"```python\n(.*?)```", section, re.S).group(1))
PY
}

FAILED=()
SUMMARY=()

run() {
  local label=$1 bridge=$2 dist=$3 spec=$4 more=$5 tests=$6 programs_too=$7 scripts=$8
  local venv="$WORK/venv" passed=yes version script test_paths=()
  echo
  echo "== $label"
  local reqs=(-e "packages/coordinator-py[crypto]" -e "$spec")
  [ -n "$more" ] && reqs+=("$more")
  if ! (cd "$ROOT" && install "$venv" "${reqs[@]}"); then
    FAILED+=("$label: the install")
    rm -rf "$venv"
    return
  fi
  version=$("$venv/bin/python" -c "from importlib.metadata import version; print(version('$dist'))")
  echo "   $dist $version"

  for test in $tests; do test_paths+=("packages/$bridge/$test"); done
  if ! (cd "$ROOT" && "$venv/bin/python" -m pytest "${test_paths[@]}" -q -rs -p no:cacheprovider) >"$WORK/tests.txt" 2>&1; then
    passed=no
    FAILED+=("$label: the tests")
  fi
  cat "$WORK/tests.txt"
  if grep -Eq '[0-9]+ skipped' "$WORK/tests.txt"; then
    passed=no
    FAILED+=("$label: tests skipped, so they did not run against $dist")
  fi

  local programs=()
  if [ "$programs_too" = yes ]; then
    quick_start "$venv" "$ROOT/packages/$bridge/README.md" >"$WORK/quickstart.py"
    programs+=("$WORK/quickstart.py")
    for script in "$ROOT/packages/$bridge"/examples/*.py; do programs+=("$script"); done
  fi
  [ -n "$scripts" ] && programs+=("$ROOT/$scripts")
  for script in "${programs[@]+"${programs[@]}"}"; do
    case "$script" in
      "$WORK/quickstart.py") echo "   the README's quick start" ;;
      *) echo "   ${script#"$ROOT"/}" ;;
    esac
    if ! (cd "$(dirname "$script")" && "$venv/bin/python" "$script" >"$WORK/out.txt" 2>&1); then
      passed=no
      case "$script" in
        "$WORK/quickstart.py") FAILED+=("$label: the README's quick start") ;;
        *) FAILED+=("$label: ${script#"$ROOT"/}") ;;
      esac
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
  IFS='|' read -r label bridge dist spec more tests programs_too scripts <<<"$entry"
  if [ "$#" -gt 0 ]; then
    chosen=no
    for name in "$@"; do [ "$name" = "$bridge" ] && chosen=yes; done
    [ "$chosen" = yes ] || continue
  fi
  run "$label" "$bridge" "$dist" "$spec" "$more" "$tests" "$programs_too" "$scripts"
done

echo
echo "== summary"
for line in "${SUMMARY[@]+"${SUMMARY[@]}"}"; do echo "   $line"; done
for line in "${FAILED[@]+"${FAILED[@]}"}"; do echo "   failed: $line"; done
[ "${#FAILED[@]}" -eq 0 ]
