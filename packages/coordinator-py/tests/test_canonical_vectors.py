"""
Cross-implementation canonicalisation conformance.

Loads the shared vectors in conformance/canonical-number-vectors.json and
asserts this implementation produces the exact canonical bytes for every
`accept` case and raises for every `reject` case. The identical vectors are
checked by the TypeScript reference, so any divergence in number handling
between the two implementations fails here.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chap_coordinator.canonical import canonicalize

_VECTORS = json.loads(
    (Path(__file__).resolve().parents[3] / "conformance" / "canonical-number-vectors.json")
    .read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", _VECTORS["accept"], ids=lambda c: repr(c["value"]))
def test_accept_produces_exact_canonical_bytes(case):
    assert canonicalize(case["value"]).decode("utf-8") == case["canonical"]


@pytest.mark.parametrize("case", _VECTORS["reject"], ids=lambda c: c["json"])
def test_reject_raises(case):
    # Parse the raw JSON first (mirrors how an envelope is received), then
    # canonicalise; the disallowed values must raise.
    value = json.loads(case["json"])
    with pytest.raises(ValueError):
        canonicalize(value)


def test_json_2_0_is_integer_valued():
    # JSON 2.0 parses to a float in Python but is integer-valued; it must
    # canonicalise identically to the integer 2 (and to the TS side).
    assert canonicalize(json.loads("2.0")).decode("utf-8") == "2"
    assert canonicalize(2).decode("utf-8") == "2"


def test_negative_zero_canonicalises_to_zero():
    assert canonicalize(-0.0).decode("utf-8") == "0"
