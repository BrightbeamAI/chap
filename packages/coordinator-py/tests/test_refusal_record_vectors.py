"""
The shared refusal-recording vectors, replayed against this reference.

Every response and every recorded entry is compared whole, and the chain
head after the call is compared too, which pins the bytes a refusal's link
hashes. The TypeScript suite reads the same file, so a drift in one
reference fails in both. conformance/refusal-record-vectors.md says what
each case covers.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions

ROOT = Path(__file__).resolve().parents[3]
VECTORS = json.loads(
    (ROOT / "conformance/refusal-record-vectors.json").read_text(encoding="utf-8"))["vectors"]


def _replay(vector):
    coord = Coordinator(CoordinatorOptions(
        deterministic_ids=True, deterministic_clock=True,
        default_profiles=vector["profiles"]))
    for envelope in vector["setup"]:
        assert "error" not in coord.dispatch(envelope), envelope["method"]
    response = coord.dispatch(vector["envelope"])
    entries = coord.dispatch({"jsonrpc": "2.0", "id": "r", "method": "audit.read",
                              "params": {"workspace": vector["workspace"],
                                         "from": "human:a"}})["result"]["entries"]
    return coord, response, entries


@pytest.mark.parametrize("vector", VECTORS, ids=[v["name"] for v in VECTORS])
def test_the_refusal_and_the_log_are_the_recorded_ones(vector):
    coord, response, entries = _replay(vector)
    assert response == vector["response"]
    assert len(entries) == vector["audit_count"]
    if vector["recorded"]:
        assert entries[-1] == vector["entry"]
    else:
        assert all("outcome" not in e for e in entries)
    assert coord.get_workspace(vector["workspace"]).chain_head == vector["evidence_head"]


def _jcs(value) -> bytes:
    # Enough of RFC 8785 for these vectors: ASCII keys and strings, integers.
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


@pytest.mark.parametrize("vector", VECTORS, ids=[v["name"] for v in VECTORS])
def test_the_chain_head_follows_from_the_entries_alone(vector):
    # Recomputed without the coordinator's canonicaliser: an accepted entry's
    # link hashes its envelope, and a refusal's hashes its outcome together
    # with its request.
    _, _, entries = _replay(vector)
    prev = "sha256:" + "0" * 64
    for e in entries:
        record = ({"outcome": e["outcome"], "request": e["request"]}
                  if "outcome" in e else e["envelope"])
        prev = "sha256:" + hashlib.sha256(_jcs(record) + prev.encode("utf-8")).hexdigest()
    assert prev == vector["evidence_head"]


def test_the_fixture_covers_both_halves():
    # A file of recorded refusals alone would pass against a coordinator that
    # recorded every refusal.
    recorded = [v for v in VECTORS if v["recorded"]]
    assert len(recorded) >= 4 and len(VECTORS) - len(recorded) >= 4
