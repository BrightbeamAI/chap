"""
The shared dispatch-gate vectors, replayed against this reference.

Every response is compared whole. A refusal that changed its message, its code
or the detail in `data` would be a change to what a client sees, and the
TypeScript suite reads the same file, so a drift in one reference fails in
both. conformance/profile-gate-vectors.md says what each case covers.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions

ROOT = Path(__file__).resolve().parents[3]
VECTORS = json.loads(
    (ROOT / "conformance/profile-gate-vectors.json").read_text(encoding="utf-8"))["vectors"]


@pytest.mark.parametrize("vector", VECTORS, ids=[v["name"] for v in VECTORS])
def test_the_response_is_the_recorded_one(vector):
    coord = Coordinator(CoordinatorOptions(
        deterministic_ids=True, deterministic_clock=True,
        default_profiles=vector["profiles"]))
    for envelope in vector["setup"]:
        assert "error" not in coord.dispatch(envelope), envelope["method"]

    assert coord.dispatch(vector["envelope"]) == vector["response"]


def test_the_fixture_covers_both_halves():
    # A file of refusals alone would pass against a coordinator that refused
    # everything.
    refused = [v for v in VECTORS if v["response"].get("error", {}).get("code") == -32601]
    allowed = [v for v in VECTORS if v not in refused]
    assert len(refused) >= 4 and len(allowed) >= 4
