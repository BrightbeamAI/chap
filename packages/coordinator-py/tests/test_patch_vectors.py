"""
Cross-implementation JSON Patch conformance.

Loads the shared vectors in conformance/json-patch-vectors.json and asserts
this implementation produces the exact patched document for every `accept`
case and raises for every `reject` case (including prototype-pollution
paths). The identical vectors are checked by the TypeScript reference, so a
decide.override applies to the same result on both sides.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chap_coordinator.patch import apply_json_patch, PatchError

_VECTORS = json.loads(
    (Path(__file__).resolve().parents[3] / "conformance" / "json-patch-vectors.json")
    .read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", _VECTORS["accept"], ids=lambda c: c["name"])
def test_accept_produces_expected(case):
    assert apply_json_patch(case["doc"], case["patch"]) == case["expected"]


@pytest.mark.parametrize("case", _VECTORS["reject"], ids=lambda c: c["name"])
def test_reject_raises(case):
    with pytest.raises(PatchError):
        apply_json_patch(case["doc"], case["patch"])


def test_input_document_is_not_mutated():
    doc = {"a": [1, 2], "b": {"c": 3}}
    apply_json_patch(doc, [{"op": "remove", "path": "/a/0"}, {"op": "add", "path": "/b/d", "value": 4}])
    assert doc == {"a": [1, 2], "b": {"c": 3}}


def test_prototype_pollution_does_not_leak():
    # Object() has no attribute named after the payload; and a rejected
    # patch must not have created one anywhere reachable.
    with pytest.raises(PatchError):
        apply_json_patch({}, [{"op": "add", "path": "/__proto__/polluted", "value": "x"}])
    assert not hasattr(dict, "polluted")
