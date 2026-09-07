"""
The lifecycle table in SPECIFICATION.md §8.1 is checked against the coordinator.

The table is normative and nothing enforced it, so it described `task.assign`,
`task.accept` and `task.start`, three methods no coordinator implements, and
two states (`assigned`, `accepted`) that are not in `TaskState`.

This test reads it. It drives a task into every state, attempts every
state-changing method from each, and requires the result to match the table
exactly: a transition the table permits must work, and one it does not list
must be refused. The same check runs against the TypeScript coordinator in
packages/coordinator/tests/spec_lifecycle_table.test.ts, so the two
implementations and the specification move together or not at all.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions

SPEC = Path(__file__).resolve().parents[3] / "SPECIFICATION.md"
PROFILES = ["core/1.0", "review/1.0", "control/1.0"]
AGENT, HUMAN, OTHER = "agent:b", "human:a", "human:c"
ARTEFACT = {"text": "draft"}

STATES = ["created", "in_progress", "review_requested", "completed", "declined",
          "abstained", "escalated", "paused", "cancelled", "superseded"]

# Methods the table covers. task.update is handled separately because its row
# carries a list of legal target states rather than a single outcome.
METHODS = ["task.complete", "review.request", "decide.approve", "decide.reject",
           "decide.override", "abstain.declare", "escalate.raise",
           "control.pause", "control.resume", "control.cancel", "control.supersede"]


# ---------------------------------------------------------------- the table

def _table_rows():
    text = SPEC.read_text(encoding="utf-8")
    section = text[text.index("### 8.1 Lifecycle"):text.index("### 8.2")]
    rows = re.findall(r"^\| (.+?) \| (.+?) \| (.+?) \|$", section, re.MULTILINE)
    return [r for r in rows if r[0] not in ("From", "------")
            and not set(r[0]) <= set("-| ")]


def spec_machine():
    """(permitted, update_targets) as the specification describes them."""
    permitted, update_targets = {}, {}
    for froms, method_cell, to_cell in _table_rows():
        method = re.search(r"`([a-z_]+\.[a-z_]+)`", method_cell).group(1)
        refused = to_cell.strip().startswith("refused")
        states = STATES if froms.strip() == "any state" else [
            s.strip() for s in froms.split(",")]
        for state in states:
            assert state in STATES, f"§8.1 names an unknown state: {state}"
            if refused:
                # A refusal row documents a precondition. Its To cell names
                # an error, not a reachable state, so it contributes nothing
                # to either map.
                continue
            if method == "task.update":
                update_targets.setdefault(state, set()).update(
                    t.strip() for t in to_cell.split(","))
            else:
                permitted.setdefault(method, set()).add(state)
    return permitted, update_targets


# ------------------------------------------------------------ the coordinator

def _ready():
    c = Coordinator(CoordinatorOptions(default_profiles=PROFILES, deterministic_ids=True))

    def send(method, actor=AGENT, **params):
        return c.dispatch({"jsonrpc": "2.0", "id": method, "method": method,
                           "params": {"workspace": "w", "from": actor, **params}})

    send("workspace.create", profiles=PROFILES)
    for uri, kind in ((HUMAN, "human"), (OTHER, "human"), (AGENT, "agent")):
        send("participant.join", actor=uri, type=kind)
    return c, send


def _drive(send, state):
    tid = send("task.create", kind="k", input={}, assignee=AGENT)["result"]["task_id"]
    steps = {
        "created": [],
        "in_progress": [("task.update", AGENT, dict(state="in_progress"))],
        "review_requested": [("review.request", AGENT, dict(artefact=ARTEFACT, to=HUMAN))],
        "completed": [("review.request", AGENT, dict(artefact=ARTEFACT, to=HUMAN)),
                      ("decide.approve", HUMAN, dict(comment="ok", rationale="ok"))],
        "declined": [("review.request", AGENT, dict(artefact=ARTEFACT, to=HUMAN)),
                     ("decide.reject", HUMAN, dict(comment="no", rationale="no"))],
        "abstained": [("review.request", AGENT, dict(artefact=ARTEFACT, to=HUMAN)),
                      ("abstain.declare", HUMAN, dict(reason="conflict of interest"))],
        "escalated": [("escalate.raise", HUMAN,
                       dict(reason="above me",
                            new_task={"kind": "k", "input": {}, "assignee": AGENT}))],
        "paused": [("control.pause", HUMAN, dict(reason="hold"))],
        "cancelled": [("control.cancel", HUMAN, dict(reason="not needed"))],
        "superseded": [("control.supersede", HUMAN,
                        dict(reason="redone",
                             successor_task={"kind": "k", "input": {}, "assignee": AGENT}))],
    }[state]
    for method, actor, params in steps:
        key = "original_task_id" if method == "escalate.raise" else "task_id"
        send(method, actor=actor, **{key: tid}, **params)
    return tid


def _attempt(send, method, tid):
    common = dict(actor=HUMAN, task_id=tid)
    calls = {
        "task.complete": lambda: send("task.complete", actor=AGENT, task_id=tid, output=ARTEFACT),
        "review.request": lambda: send("review.request", actor=AGENT, task_id=tid,
                                       artefact=ARTEFACT, to=HUMAN),
        "decide.approve": lambda: send("decide.approve", comment="ok", rationale="ok", **common),
        "decide.reject": lambda: send("decide.reject", comment="no", rationale="no", **common),
        "decide.override": lambda: send(
            "decide.override", comment="fix", rationale="fix",
            diff=[{"op": "replace", "path": "/text", "value": "x"}], **common),
        "abstain.declare": lambda: send("abstain.declare", reason="conflict of interest", **common),
        "escalate.raise": lambda: send("escalate.raise", actor=HUMAN, original_task_id=tid,
                                       reason="above me",
                                       new_task={"kind": "k", "input": {}, "assignee": AGENT}),
        "control.pause": lambda: send("control.pause", reason="hold", **common),
        "control.resume": lambda: send("control.resume", reason="carry on", **common),
        "control.cancel": lambda: send("control.cancel", reason="stop", **common),
        "control.supersede": lambda: send(
            "control.supersede", reason="redone",
            successor_task={"kind": "k", "input": {}, "assignee": AGENT}, **common),
    }
    return calls[method]()


def implemented_machine():
    permitted, update_targets = {}, {}
    for state in STATES:
        c, send = _ready()
        tid = _drive(send, state)
        assert c.get_workspace("w").tasks[tid].state == state, \
            f"the fixture for {state} did not reach it"
        for method in METHODS:
            c2, send2 = _ready()
            tid2 = _drive(send2, state)
            if "error" not in _attempt(send2, method, tid2):
                permitted.setdefault(method, set()).add(state)
        for target in STATES:
            c3, send3 = _ready()
            tid3 = _drive(send3, state)
            if "error" not in send3("task.update", task_id=tid3, state=target):
                update_targets.setdefault(state, set()).add(target)
    return permitted, update_targets


# ------------------------------------------------------------------ the check

@pytest.fixture(scope="module")
def machines():
    return spec_machine(), implemented_machine()


def test_the_table_is_not_empty(machines):
    # Without this the comparisons below would pass by having nothing to compare.
    (spec_permitted, spec_updates), _ = machines
    assert len(spec_permitted) >= 8, "SPECIFICATION.md 8.1 parsed to almost nothing"
    assert spec_updates, "no task.update rows found in 8.1"


@pytest.mark.parametrize("method", METHODS)
def test_the_table_matches_the_coordinator(method, machines):
    (spec_permitted, _), (real_permitted, _) = machines
    documented = spec_permitted.get(method, set())
    actual = real_permitted.get(method, set())
    assert documented == actual, (
        f"SPECIFICATION.md 8.1 and the coordinator disagree about {method}.\n"
        f"  the table permits it from : {sorted(documented) or 'nothing'}\n"
        f"  the coordinator permits it: {sorted(actual) or 'nothing'}\n"
        f"  documented but refused   : {sorted(documented - actual) or 'none'}\n"
        f"  works but undocumented   : {sorted(actual - documented) or 'none'}"
    )


@pytest.mark.parametrize("state", STATES)
def test_the_task_update_rows_match_the_transition_map(state, machines):
    (_, spec_updates), (_, real_updates) = machines
    assert spec_updates.get(state, set()) == real_updates.get(state, set()), (
        f"SPECIFICATION.md 8.1 and task.update disagree about {state}.\n"
        f"  the table says : {sorted(spec_updates.get(state, set())) or 'nothing'}\n"
        f"  the map allows : {sorted(real_updates.get(state, set())) or 'nothing'}"
    )


def test_the_table_names_no_method_the_coordinator_lacks():
    # The table named task.assign, task.accept and task.start, which no
    # coordinator implements. A named method must be dispatchable.
    c, _ = _ready()
    named = {re.search(r"`([a-z_]+\.[a-z_]+)`", cell).group(1)
             for _, cell, _ in _table_rows()}
    for method in sorted(named):
        result = c.dispatch({"jsonrpc": "2.0", "id": "x", "method": method, "params": {}})
        assert result.get("error", {}).get("code") != -32601, \
            f"SPECIFICATION.md 8.1 names {method}, which the coordinator does not implement"
