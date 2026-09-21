"""
Every refusal the patch engine can give, in both references, word for word.

A divergence here is a divergence in the response the two coordinators return
for the same envelope. Python's repr and JavaScript's JSON.stringify quote
differently, and the two runtimes name their own types differently, so the
same refusal read two ways until both were made to speak JSON. Found by the
differential fuzzer once its overrides drew from the whole operation set.

The TypeScript side of this file is patch_message_parity.test.ts. The cases
below and the cases there are the same list in the same order.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chap_coordinator.patch import PatchError, apply_json_patch

CASES: list[tuple[str, dict, list[dict]]] = [
    ("array index with a leading zero", {"items": [1, 2]},
     [{"op": "replace", "path": "/items/01", "value": 1}]),
    ("array index in exponent form", {"items": [1, 2]},
     [{"op": "replace", "path": "/items/1e1", "value": 1}]),
    ("array index with a separator", {"items": [1, 2]},
     [{"op": "replace", "path": "/items/1_0", "value": 1}]),
    ("add into a string", {"a": "str"},
     [{"op": "add", "path": "/a/b", "value": 1}]),
    ("add into a number", {"a": {"b": 3}},
     [{"op": "add", "path": "/a/b/c", "value": 1}]),
    ("replace at an index of an object", {"a": {"x": 1}},
     [{"op": "replace", "path": "/a/0", "value": 1}]),
    ("remove from a string", {"a": "str"},
     [{"op": "remove", "path": "/a/x"}]),
    ("index past the end", {"items": [1]},
     [{"op": "remove", "path": "/items/5"}]),
    ("an operation that does not exist", {"a": 1},
     [{"op": "frobnicate", "path": "/a"}]),
    ("a pointer with no leading slash", {"a": 1},
     [{"op": "add", "path": "a", "value": 1}]),
    ("a prototype-polluting segment", {},
     [{"op": "add", "path": "/__proto__", "value": 1}]),
    ("test against the wrong value", {"a": 1},
     [{"op": "test", "path": "/a", "value": 2}]),
    ("move into a child of itself", {"a": {"b": {}}},
     [{"op": "move", "from": "/a", "path": "/a/b/c"}]),
    # RFC 6901: "-" names the position after the last element, which no
    # element occupies, so only "add" may use it.
    ("remove at the position after the last element", {"a": [1, 2]},
     [{"op": "remove", "path": "/a/-"}]),
    ("replace at the position after the last element", {"a": [1, 2]},
     [{"op": "replace", "path": "/a/-", "value": 9}]),
    ("test at the position after the last element", {"a": [1, 2]},
     [{"op": "test", "path": "/a/-", "value": 2}]),
    ("copy from the position after the last element", {"a": [1, 2], "b": 0},
     [{"op": "copy", "from": "/a/-", "path": "/b"}]),
]

VECTORS = Path(__file__).resolve().parents[3] / "conformance" / "patch-message-vectors.json"


@pytest.mark.parametrize("name,doc,diff", CASES, ids=[c[0] for c in CASES])
def test_every_case_is_refused(name, doc, diff):
    with pytest.raises(PatchError):
        apply_json_patch(doc, diff)


def test_the_messages_are_the_recorded_ones():
    """The vectors the TypeScript suite reads, so the two are compared."""
    recorded = json.loads(VECTORS.read_text(encoding="utf-8"))["messages"]
    got = {}
    for name, doc, diff in CASES:
        try:
            apply_json_patch(doc, diff)
        except PatchError as exc:
            got[name] = str(exc)
    assert got == recorded
