"""
The shared malformed-params vectors, replayed against this reference.

Each case is a param of the wrong type or shape that the differential fuzzer
found the two references answering differently, or answering with -32603
after changing state. A refused case must be answered as the file says and
leave the workspace as it was, its log apart: a refusal the file marks
`recorded` adds one entry (SPECIFICATION 10.1). The TypeScript suite reads
the same file, so a drift in either reference fails in both. Responses are
compared as JSON text, so 1.0 where the file has 1 fails too.

Mirrors packages/coordinator/tests/malformed_params_vectors.test.ts.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions

ROOT = Path(__file__).resolve().parents[3]
FILE = json.loads((ROOT / "conformance/malformed-params-vectors.json").read_text(encoding="utf-8"))


def _replay():
    coord = Coordinator(CoordinatorOptions(
        deterministic_ids=True, deterministic_clock=True, default_profiles=FILE["profiles"]))
    for env in FILE["setup"]:
        r = coord.dispatch(copy.deepcopy(env))
        assert "error" not in r, (env["method"], r)
    return coord


def _state(coord):
    """Every workspace without its log, and the length of each log."""
    rest, logs = {}, {}
    for ws_id, ws in coord.workspaces.items():
        data = coord._snapshot_workspace(ws).data
        logs[ws_id] = len(data.pop("audit"))
        data.pop("chain_head")
        rest[ws_id] = data
    return json.dumps(rest, sort_keys=True, default=str), logs


def _text(value):
    return json.dumps(value, sort_keys=True)


@pytest.mark.parametrize("vector", FILE["refused"], ids=[v["name"] for v in FILE["refused"]])
def test_a_refused_call_is_answered_alike_and_changes_nothing(vector):
    coord = _replay()
    rest, logs = _state(coord)
    response = coord.dispatch(copy.deepcopy(vector["envelope"]))
    assert _text(response) == _text(vector["response"])
    after, grown = _state(coord)
    assert after == rest, "a refused call changed the workspace"
    assert grown == {ws: n + (1 if vector["recorded"] else 0) for ws, n in logs.items()}


@pytest.mark.parametrize("vector", FILE["accepted"], ids=[v["name"] for v in FILE["accepted"]])
def test_an_accepted_call_is_answered_alike(vector):
    coord = _replay()
    for env in vector.get("before", []):
        assert "error" not in coord.dispatch(copy.deepcopy(env))
    assert _text(coord.dispatch(copy.deepcopy(vector["envelope"]))) == _text(vector["response"])
