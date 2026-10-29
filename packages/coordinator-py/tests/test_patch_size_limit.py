"""
A patch cannot grow an artefact without limit.

The patch engine capped operations and nodes, and nothing capped size or
depth. Each ``copy`` of the root doubles the document, its strings included,
so fifteen of them grew a 64 KiB artefact to about 2 GiB within the node cap,
and a patch that puts one value inside another nests the document as deep as
it likes. Any reviewer could send either as a ``decide.override``. The engine
now counts the UTF-8 bytes of the document's strings and keys, which the
coordinator caps at its ``max_envelope_bytes``, caps its depth at 64 levels,
as a request's is, and caps what a patch's copies carry.

Mirrors packages/coordinator/tests/patch_size_limit.test.ts.
"""
from __future__ import annotations

import copy
import random

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator import patch as patch_module
from chap_coordinator.jsonrpc import E
from chap_coordinator.patch import DEFAULT_MAX_PATCHED_BYTES, PatchError, apply_json_patch


def _nest(levels: int):
    """A value ``levels`` deep, itself included: _nest(1) is 0, _nest(2) is [0]."""
    v = 0
    for _ in range(levels - 1):
        v = [v]
    return v


def _stacked() -> list[dict]:
    """Four 20-level values, each put in at the innermost point of the one before: 78 levels."""
    ops, path = [], "/a"
    for _ in range(4):
        ops.append({"op": "add", "path": path, "value": _nest(20)})
        path += "/0" * 19
    return ops


def test_the_limit_counts_the_utf8_bytes_of_strings_and_keys():
    # é is 2 bytes, each € 3, the emoji 4: 2 + 30 + 1 + 4 = 37, and the copy adds 1 + 30.
    doc = {"é": "€" * 10, "e": "😀"}
    op = [{"op": "copy", "from": "/é", "path": "/k"}]
    assert apply_json_patch(doc, op, max_string_bytes=68) == {**doc, "k": "€" * 10}
    with pytest.raises(PatchError):
        apply_json_patch(doc, op, max_string_bytes=67)


def test_the_default_is_the_default_envelope_limit():
    assert DEFAULT_MAX_PATCHED_BYTES == 1_048_576


def test_fifteen_copies_of_a_64_kib_artefact_are_refused():
    ops = [{"op": "copy", "from": "", "path": f"/c{i}"} for i in range(1, 16)]
    with pytest.raises(PatchError, match="exceeds the size limit"):
        apply_json_patch({"s": "x" * 65_536}, ops)


def test_a_copy_counts_what_it_copies_where_inserting_into_the_array_moves_its_source():
    # Each copy reads the last element and inserts it at the front, which moves
    # that element along the array. Sixty copies of 64 KiB come to about 4 MB.
    ops = [{"op": "copy", "from": f"/a/{i}", "path": "/a/0"} for i in range(1, 61)]
    with pytest.raises(PatchError, match="exceeds the size limit"):
        apply_json_patch({"a": ["s", "x" * 65_536]}, ops)


# Each case is refused one byte below the size of its result, and applies at it.
EXACT = [
    ("a replaced value", {"t": ""}, [{"op": "replace", "path": "/t", "value": "yyyy"}], 5),
    ("a copy at its source", {"a": "xxxx", "b": ""}, [{"op": "copy", "from": "/a", "path": "/b"}], 10),
    ("an added key", {}, [{"op": "add", "path": "/abcd", "value": 0}], 4),
    ("a moved key", {"a": 0}, [{"op": "move", "from": "/a", "path": "/bcd"}], 3),
    ("a key in UTF-8 bytes", {}, [{"op": "add", "path": "/€", "value": 0}], 3),
]


@pytest.mark.parametrize("name, doc, ops, size", EXACT, ids=[c[0] for c in EXACT])
def test_the_limit_counts_each_change_exactly(name, doc, ops, size):
    apply_json_patch(doc, ops, max_string_bytes=size)
    with pytest.raises(PatchError, match="exceeds the size limit"):
        apply_json_patch(doc, ops, max_string_bytes=size - 1)


def test_a_document_that_passes_the_limit_part_way_is_refused_even_if_a_later_operation_shrinks_it():
    ops = [{"op": "copy", "from": "/s", "path": "/t"}, {"op": "remove", "path": "/t"}]
    with pytest.raises(PatchError, match="exceeds the size limit"):
        apply_json_patch({"s": "x" * 600_000}, ops)


def test_a_long_patch_of_small_edits_to_a_large_artefact_applies():
    ops = [{"op": "replace", "path": "/n", "value": i} for i in range(1000)]
    assert apply_json_patch({"s": "x" * 1_000_000, "n": 0}, ops)["n"] == 999


def test_an_operations_value_goes_in_as_a_copy_so_the_patch_stays_as_it_was_sent():
    ops = [{"op": "add", "path": "/x", "value": []}, {"op": "add", "path": "/x/-", "value": 1}]
    assert apply_json_patch({}, ops) == {"x": [1]}
    assert ops[0] == {"op": "add", "path": "/x", "value": []}


def test_a_patched_document_may_nest_64_levels_counting_itself_as_one_and_no_more():
    assert apply_json_patch({}, [{"op": "add", "path": "/a", "value": _nest(63)}]) == {"a": _nest(63)}
    with pytest.raises(PatchError, match="nests deeper than 64 levels"):
        apply_json_patch({}, [{"op": "add", "path": "/a", "value": _nest(64)}])


def test_values_that_each_sit_within_the_limit_cannot_be_stacked_past_it():
    ops = _stacked()
    apply_json_patch({}, ops[:3])
    with pytest.raises(PatchError, match="nests deeper than 64 levels"):
        apply_json_patch({}, ops)


@pytest.mark.parametrize("kind", ["move", "copy"])
def test_a_move_or_a_copy_cannot_carry_a_value_past_the_limit(kind):
    # /a reaches 41 levels, and the place under /b is 31 segments down.
    doc = {"a": _nest(40), "b": _nest(31)}
    with pytest.raises(PatchError, match="nests deeper than 64 levels"):
        apply_json_patch(doc, [{"op": kind, "from": "/a", "path": "/b" + "/0" * 30}])
    apply_json_patch(doc, [{"op": kind, "from": "/a", "path": "/c"}])


def _escape(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


def _pointers(doc, base=""):
    out = [base]
    if isinstance(doc, list):
        for i, v in enumerate(doc):
            out += _pointers(v, f"{base}/{i}")
    elif isinstance(doc, dict):
        for k, v in doc.items():
            out += _pointers(v, f"{base}/{_escape(k)}")
    return out


def _measured(value, level=1):
    """A document's values, string and key bytes, and depth, measured whole."""
    nodes, size, depth = 1, 0, level
    if isinstance(value, str):
        size = len(value.encode("utf-8"))
    elif isinstance(value, dict):
        for k, v in value.items():
            n, b, d = _measured(v, level + 1)
            nodes, size, depth = nodes + n, size + len(k.encode("utf-8")) + b, max(depth, d)
    elif isinstance(value, list):
        for v in value:
            n, b, d = _measured(v, level + 1)
            nodes, size, depth = nodes + n, size + b, max(depth, d)
    return nodes, size, depth


def _at(doc, pointer):
    """The value at a JSON Pointer."""
    for token in pointer.split("/")[1:] if pointer else []:
        token = token.replace("~1", "/").replace("~0", "~")
        doc = doc[int(token)] if isinstance(doc, list) else doc[token]
    return doc


def _outcome(f):
    try:
        return "ok", f()
    except PatchError as e:
        return "refused", str(e)


def test_the_size_kept_by_each_operation_agrees_with_measuring_the_whole_document_after_it(monkeypatch):
    # Small limits, so that all four refusals come up.
    monkeypatch.setattr(patch_module, "MAX_DOCUMENT_NODES", 40)
    monkeypatch.setattr(patch_module, "MAX_DOCUMENT_DEPTH", 5)
    monkeypatch.setattr(patch_module, "MAX_CARRIED_TIMES", 2)
    r = random.Random(20261029)

    def text(n):
        return "".join(r.choice("aé€😀/~") for _ in range(r.randint(0, n)))

    def value(depth=0):
        k = r.random()
        if depth > 2 or k < 0.35:
            return r.choice([text(10), r.randint(0, 9), True, None])
        if k < 0.65:
            return [value(depth + 1) for _ in range(r.randint(0, 3))]
        return {text(3): value(depth + 1) for _ in range(r.randint(0, 3))}

    for i in range(1500):
        doc = {text(3): value() for _ in range(r.randint(1, 4))}
        current, ops = copy.deepcopy(doc), []
        for _ in range(r.randint(1, 12)):
            at = _pointers(current)
            kind = r.choice(["add", "replace", "remove", "move", "copy", "copy", "test"])
            where = (f"{r.choice(at)}/{r.choice(['0', '-', _escape(text(3))])}"
                     if r.random() < 0.7 else r.choice(at))
            if kind in ("move", "copy"):
                op = {"op": kind, "from": r.choice(at), "path": where}
            elif kind == "remove":
                op = {"op": kind, "path": r.choice(at)}
            else:
                op = {"op": kind, "path": where if kind == "add" else r.choice(at), "value": value()}
            ops.append(op)
            try:
                current = patch_module._apply_one(copy.deepcopy(current), copy.deepcopy(op))
            except PatchError:
                pass
        limit = r.randint(5, 160)

        def reference():
            out = copy.deepcopy(doc)
            carried_nodes = carried_bytes = 0
            for op in ops:
                # A copy, and a move to a deeper place or onto the whole
                # document, carries its value.
                carried = None
                if op["op"] == "copy" or (op["op"] == "move" and (
                        op["path"].count("/") > op["from"].count("/") or op["path"] == "")):
                    try:
                        carried = _measured(_at(out, op["from"]))
                    except (KeyError, IndexError, ValueError, TypeError):
                        pass  # the operation is refused below
                out = patch_module._apply_one(out, op)
                if carried:
                    carried_nodes, carried_bytes = carried_nodes + carried[0], carried_bytes + carried[1]
                nodes, size, depth = _measured(out)
                if nodes > 40:
                    raise PatchError("Patched document exceeds the node limit (40)")
                if size > limit:
                    raise PatchError(
                        f"Patched document exceeds the size limit ({limit} bytes of strings and keys)")
                if depth > 5:
                    raise PatchError("Patched document nests deeper than 5 levels")
                if carried_nodes > 80 or carried_bytes > 2 * limit:
                    raise PatchError(
                        f"Patch copies or moves more than its limit "
                        f"(80 values or {2 * limit} bytes of strings and keys)")
            return out

        got = _outcome(lambda: apply_json_patch(doc, ops, max_string_bytes=limit))
        assert got == _outcome(reference), (i, doc, ops, limit)


def test_a_patchs_copies_and_its_moves_to_a_deeper_place_carry_at_most_three_times_the_documents_limits():
    # 1,001 values copied onto themselves: 299 copies carry 299,299 values, and 300 carry 300,300.
    doc = {"a": [0] * 1000}
    apply_json_patch(doc, [{"op": "copy", "from": "/a", "path": "/a"}] * 299)
    with pytest.raises(PatchError, match=r"copies or moves more than its limit \(300000 values or"):
        apply_json_patch(doc, [{"op": "copy", "from": "/a", "path": "/a"}] * 300)
    # 99 bytes copied onto themselves, against a limit of 100: three carry 297 bytes, and four 396.
    text = {"s": "x" * 99}
    apply_json_patch(text, [{"op": "copy", "from": "/s", "path": "/s"}] * 3, max_string_bytes=100)
    with pytest.raises(PatchError, match="300 bytes of strings and keys"):
        apply_json_patch(text, [{"op": "copy", "from": "/s", "path": "/s"}] * 4, max_string_bytes=100)
    # A copy onto the whole document carries the document.
    apply_json_patch([0] * 1000, [{"op": "copy", "from": "", "path": ""}] * 299)
    with pytest.raises(PatchError, match="copies or moves more than its limit"):
        apply_json_patch([0] * 1000, [{"op": "copy", "from": "", "path": ""}] * 300)
    # A move onto the whole document carries the value that becomes it: about 99,915 values each time.
    wrapped = [0] * 99_900
    for _ in range(15):
        wrapped = {"a": wrapped}
    apply_json_patch(wrapped, [{"op": "move", "from": "/a", "path": ""}] * 3)
    with pytest.raises(PatchError, match="copies or moves more than its limit"):
        apply_json_patch(wrapped, [{"op": "move", "from": "/a", "path": ""}] * 4)
    # A move to a deeper place carries its 2,501 values, and a move back costs nothing.
    big = {"a": [0] * 2500, "d": {}}
    def moves(n):
        return [{"op": "move", "from": "/a", "path": "/d/a"} if i % 2 == 0
                else {"op": "move", "from": "/d/a", "path": "/a"} for i in range(n)]
    apply_json_patch(big, moves(237))
    with pytest.raises(PatchError, match="copies or moves more than its limit"):
        apply_json_patch(big, moves(239))


def test_the_value_count_is_exact_at_the_limit():
    # 1 for the document, 1 for the array and 99,998 zeros: 100,000 values.
    doc = {"a": [0] * 99_998}
    with pytest.raises(PatchError, match=r"exceeds the node limit \(100000\)"):
        apply_json_patch(doc, [{"op": "add", "path": "/b", "value": 0}])
    apply_json_patch(doc, [{"op": "remove", "path": "/a/0"}, {"op": "add", "path": "/b", "value": 0}])
    with pytest.raises(PatchError, match="exceeds the node limit"):
        apply_json_patch(doc, [{"op": "replace", "path": "/a/0", "value": [0]}])
    apply_json_patch(doc, [{"op": "replace", "path": "/a", "value": 0},
                           {"op": "add", "path": "/b", "value": [0] * 99_997}])
    apply_json_patch(doc, [{"op": "move", "from": "/a", "path": "/b"}])
    with pytest.raises(PatchError, match="exceeds the node limit"):
        apply_json_patch(doc, [{"op": "move", "from": "/a", "path": "/b"}, {"op": "add", "path": "/c", "value": 0}])


def test_the_limits_are_checked_in_a_fixed_order_values_then_size_then_depth():
    doc = {"a": [0] * 99_998}
    with pytest.raises(PatchError, match="exceeds the node limit"):
        apply_json_patch(doc, [{"op": "add", "path": "/b", "value": "xxxxxxxx"}], max_string_bytes=2)
    deep = "x" * 100
    for _ in range(70):
        deep = [deep]
    with pytest.raises(PatchError, match="exceeds the size limit"):
        apply_json_patch({}, [{"op": "add", "path": "/a", "value": deep}], max_string_bytes=10)


def test_a_document_that_starts_deeper_than_the_limit_is_refused_unless_the_patch_removes_the_depth():
    doc = {"a": _nest(70)}
    assert apply_json_patch(doc, [{"op": "remove", "path": "/a"}]) == {}
    with pytest.raises(PatchError, match="nests deeper than 64 levels"):
        apply_json_patch(doc, [{"op": "test", "path": "/a", "value": _nest(70)}])


def _override_on(c, diff=None):
    def send(method, actor="agent:b", **params):
        return c.dispatch({"jsonrpc": "2.0", "id": method, "method": method,
                           "params": {"workspace": "w", "from": actor, **params}})

    send("workspace.create", profiles=["core/1.0", "review/1.0"])
    for uri, kind in (("human:a", "human"), ("agent:b", "agent")):
        send("participant.join", actor=uri, type=kind)
    tid = send("task.create", kind="k", input={}, assignee="agent:b")["result"]["task_id"]
    send("review.request", task_id=tid, to=["human:a"], artefact={"s": "x" * 1000})
    if diff is None:
        diff = [{"op": "copy", "from": "", "path": f"/c{i}"} for i in (1, 2, 3)]
    return tid, send("decide.override", actor="human:a", task_id=tid, diff=diff, rationale="r")


def test_the_coordinator_caps_an_override_at_its_max_envelope_bytes():
    c = Coordinator(CoordinatorOptions(deterministic_ids=True, max_envelope_bytes=4096))
    tid, reply = _override_on(c)
    assert reply["error"]["code"] == E.PATCH_FAILED
    assert "size limit (4096 bytes" in reply["error"]["message"]
    assert c.get_workspace("w").tasks[tid].state == "review_requested"


def test_the_same_override_applies_under_the_default_limit():
    c = Coordinator(CoordinatorOptions(deterministic_ids=True))
    tid, reply = _override_on(c)
    assert "error" not in reply, reply.get("error")
    assert c.get_workspace("w").tasks[tid].state == "completed"


def test_the_coordinator_refuses_an_override_that_nests_the_artefact_too_deep_and_keeps_the_task_as_it_was():
    c = Coordinator(CoordinatorOptions(deterministic_ids=True))
    tid, reply = _override_on(c, _stacked())
    assert reply["error"]["code"] == E.PATCH_FAILED
    assert "nests deeper than 64 levels" in reply["error"]["message"]
    task = c.get_workspace("w").tasks[tid]
    assert task.state == "review_requested"
    assert task.output is None
