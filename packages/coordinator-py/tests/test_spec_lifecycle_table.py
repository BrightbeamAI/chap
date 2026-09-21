"""
The lifecycle table in SPECIFICATION.md §8.1 is checked against the coordinator.

The table is normative and nothing enforced it, so it described `task.assign`,
`task.accept` and `task.start`, three methods no coordinator implements, and
two states (`assigned`, `accepted`) that are not in `TaskState`.

This test reads it. It drives a task into every state, attempts every
state-changing method from each, and requires the result to match the table
exactly: a transition the table permits must work, and one it does not list
must be refused.

Both columns are read. The From column says where a method may be called, and
the To column says where it leaves the task. A state named in a To cell must be
one the method actually produces, and a state a method produces must be named,
so a cell cannot describe an outcome the coordinator does not have. Rows whose
outcome depends on more than the starting state, a review rule not yet
satisfied, a rejection sent back for revision, the state a pause captured, are
driven by the scenarios in OUTCOMES below.

The same check runs against the TypeScript coordinator in
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


def _states_named_in(cell: str) -> set[str]:
    """The states a To cell names, whatever prose surrounds them.

    A cell may carry an explanation beside the outcome, as
    "completed once the review rule is satisfied, otherwise review_requested"
    does. What is normative is which states it names.
    """
    return {s for s in STATES if re.search(rf"\b{s}\b", cell)}


def spec_machine():
    """(permitted, update_targets, outcomes) as the specification describes them."""
    permitted, update_targets, outcomes = {}, {}, {}
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
                named = _states_named_in(to_cell)
                assert named, f"§8.1 gives {method} a To cell that names no state: {to_cell}"
                outcomes.setdefault(method, set()).update(named)
    return permitted, update_targets, outcomes


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


# Outcomes a starting state alone does not settle: a review rule not yet
# satisfied, a rejection sent back for revision, the state a pause captured.
# Each entry drives the scenario and returns the method it exercised, so the
# state the task lands in is attributed to that method.
def _outcome_scenarios():
    def review_rule_unsatisfied(send):
        tid = send("task.create", kind="k", input={}, assignee=AGENT)["result"]["task_id"]
        send("review.request", task_id=tid, artefact=ARTEFACT, to=[HUMAN, OTHER],
             rule="quorum:2")
        send("decide.approve", actor=HUMAN, task_id=tid, comment="ok", rationale="ok")
        return "decide.approve", tid

    def rejection_sent_back(send):
        tid = send("task.create", kind="k", input={}, assignee=AGENT)["result"]["task_id"]
        send("review.request", task_id=tid, artefact=ARTEFACT, to=HUMAN)
        send("decide.reject", actor=HUMAN, task_id=tid, comment="no", rationale="no",
             request_revision=True)
        return "decide.reject", tid

    def completion_opening_a_review(send):
        tid = send("task.create", kind="k", input={}, assignee=AGENT,
                   review_required=True)["result"]["task_id"]
        send("task.complete", task_id=tid, output=ARTEFACT)
        return "task.complete", tid

    def resume_from(origin):
        def run(send):
            tid = _drive(send, origin)
            send("control.pause", actor=HUMAN, task_id=tid, reason="hold")
            send("control.resume", actor=HUMAN, task_id=tid)
            return "control.resume", tid
        run.__name__ = f"resume_from_{origin}"
        return run

    scenarios = [review_rule_unsatisfied, rejection_sent_back,
                 completion_opening_a_review]
    # control.pause names the states a pause can be entered from, so those are
    # the states a resume can restore.
    scenarios += [resume_from(origin) for origin in
                  ("created", "in_progress", "review_requested", "abstained",
                   "escalated")]
    return scenarios


def implemented_machine():
    permitted, update_targets, outcomes = {}, {}, {}
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
                outcomes.setdefault(method, set()).add(
                    c2.get_workspace("w").tasks[tid2].state)
        for target in STATES:
            c3, send3 = _ready()
            tid3 = _drive(send3, state)
            if "error" not in send3("task.update", task_id=tid3, state=target):
                update_targets.setdefault(state, set()).add(target)
    for scenario in _outcome_scenarios():
        c4, send4 = _ready()
        method, tid4 = scenario(send4)
        outcomes.setdefault(method, set()).add(
            c4.get_workspace("w").tasks[tid4].state)
    return permitted, update_targets, outcomes


# ------------------------------------------------------------------ the check

@pytest.fixture(scope="module")
def machines():
    return spec_machine(), implemented_machine()


def test_the_table_is_not_empty(machines):
    # Without this the comparisons below would pass by having nothing to compare.
    (spec_permitted, spec_updates, spec_outcomes), _ = machines
    assert len(spec_permitted) >= 8, "SPECIFICATION.md 8.1 parsed to almost nothing"
    assert spec_updates, "no task.update rows found in 8.1"
    assert len(spec_outcomes) >= 8, "the To column of 8.1 parsed to almost nothing"


@pytest.mark.parametrize("method", METHODS)
def test_the_table_matches_the_coordinator(method, machines):
    (spec_permitted, _, _), (real_permitted, _, _) = machines
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
    (_, spec_updates, _), (_, real_updates, _) = machines
    assert spec_updates.get(state, set()) == real_updates.get(state, set()), (
        f"SPECIFICATION.md 8.1 and task.update disagree about {state}.\n"
        f"  the table says : {sorted(spec_updates.get(state, set())) or 'nothing'}\n"
        f"  the map allows : {sorted(real_updates.get(state, set())) or 'nothing'}"
    )


@pytest.mark.parametrize("method", METHODS)
def test_the_to_column_matches_where_the_coordinator_leaves_the_task(method, machines):
    (_, _, documented), (_, _, actual) = machines
    named = documented.get(method, set())
    reached = actual.get(method, set())
    assert named == reached, (
        f"SPECIFICATION.md 8.1 and the coordinator disagree about where "
        f"{method} leaves the task.\n"
        f"  the To column names  : {sorted(named) or 'nothing'}\n"
        f"  the coordinator reaches: {sorted(reached) or 'nothing'}\n"
        f"  named but unreachable: {sorted(named - reached) or 'none'}\n"
        f"  reached but unnamed  : {sorted(reached - named) or 'none'}"
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
