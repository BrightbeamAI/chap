"""
The redactor's promise, and what a malformed chain is allowed to cost.

A redactor that leaves content reachable is worse than no redactor, because
someone relied on it. These tests plant a marker in every place a chain can
carry caller content and then search every cell of every table, the envelope
stream and the snapshot for it, rather than checking the columns anyone
happened to think of.

The second half is about damage limits. A chain assembled by hand, exported
twice and concatenated, or written by a client that sent a number as a string,
should cost the analysis the cell it is wrong about and nothing else.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("pandas")
pytest.importorskip("chap_coordinator")

import pandas as pd  # noqa: E402
from chap_coordinator import Coordinator, CoordinatorOptions  # noqa: E402

from chap_analytics import (TABLES, frames, from_coordinator, from_json,  # noqa: E402
                            redact_artefacts)
from chap_analytics.load import Chain  # noqa: E402

PROFILES = ["core/1.0", "review/1.0", "whisper/1.0", "control/1.0"]

#: One marker per place a chain can carry what the agent was working on.
MARKERS = ["SECRET-INPUT", "SECRET-OUTPUT", "SECRET-ARTEFACT", "SECRET-PATCH",
           "SECRET-ANSWER", "SECRET-DEFAULT", "SECRET-SUCCESSOR"]


def planted():
    """A workspace with a marker in every content-bearing field."""
    c = Coordinator(CoordinatorOptions(default_profiles=PROFILES))

    def ok(m, p=None, a="human:ana"):
        r = c.dispatch({"jsonrpc": "2.0", "id": m, "method": m,
                        "params": {"workspace": "w", "from": a, **(p or {})}})
        assert "error" not in r, f"{m}: {r.get('error')}"
        return r.get("result", {})

    ok("workspace.create", {"profiles": PROFILES})
    for uri, kind in [("human:ana", "human"), ("human:bo", "human"),
                      ("human:cy", "human"), ("agent:x", "agent")]:
        ok("participant.join", {"type": kind}, uri)

    t = ok("task.create", {"kind": "k", "assignee": "agent:x",
                           "input": {"body": "SECRET-INPUT"}})["task_id"]
    ok("whisper.ask", {"task_id": t, "to": ["human:bo"], "question": "which one?",
                       "deadline_ms": 600_000,
                       "default_if_lapsed": "SECRET-DEFAULT"}, "agent:x")
    wid = next(iter(c.get_workspace("w").whispers))
    ok("whisper.answer", {"whisper_id": wid, "answer": "SECRET-ANSWER"}, "human:bo")
    # A snapshot copies the open tasks, their inputs included.
    ok("control.snapshot", {}, "human:ana")
    ok("task.complete", {"task_id": t, "output": {"body": "SECRET-OUTPUT"}}, "agent:x")
    ok("review.request", {"task_id": t, "artefact": {"body": "SECRET-ARTEFACT"},
                          "to": ["human:ana"]}, "agent:x")
    ok("decide.override", {"task_id": t, "rationale": "House style.",
                           "diff": [{"op": "replace", "path": "/body",
                                     "value": "SECRET-PATCH"}]}, "human:ana")
    other = ok("task.create", {"kind": "k2", "input": {}, "assignee": "agent:x"})["task_id"]
    ok("escalate.raise", {"original_task_id": other,
                          "new_task": {"kind": "esc", "assignee": "human:cy",
                                       "input": {"body": "SECRET-SUCCESSOR"}}})
    return c


def leaks(blob) -> list[str]:
    text = json.dumps(blob, default=str)
    return [m for m in MARKERS if m in text]


def redacted_reads(c, tmp_path):
    """Both sources, through the public loaders, with the redactor applied."""
    entries = c.dispatch({"jsonrpc": "2.0", "id": "r", "method": "audit.read",
                          "params": {"workspace": "w", "from": "human:ana"}})["result"]["entries"]
    export = tmp_path / "export.json"
    export.write_text(json.dumps({"workspace": "w", "entries": entries}), encoding="utf-8")
    return {
        "envelopes": frames(from_json(str(export), redact=redact_artefacts)),
        "state": frames(from_coordinator(c, workspace="w", redact=redact_artefacts)),
    }


# ------------------------------------------------------------------ privacy

@pytest.mark.parametrize("source", ["envelopes", "state"])
def test_no_table_cell_survives_redaction(source, tmp_path):
    f = redacted_reads(planted(), tmp_path)[source]
    for table in TABLES:
        df = f[table.name]
        for col in df.columns:
            assert not leaks(df[col].tolist()), f"{table.name}.{col} leaked"


@pytest.mark.parametrize("source", ["envelopes", "state"])
def test_nothing_reachable_from_the_frames_object_survives_either(source, tmp_path):
    # The tables were redacted and the chain hanging off them was not, so the
    # artefacts were one attribute away. A snapshot also contains the envelope
    # stream, so redacting the copy and keeping the original leaks everything.
    f = redacted_reads(planted(), tmp_path)[source]
    assert not leaks(f.chain.events), "the envelope stream kept its content"
    assert not leaks(f.chain.state), "the snapshot kept what the envelopes gave up"


def test_redaction_keeps_the_analysis_and_only_removes_the_content(tmp_path):
    c = planted()
    red = redacted_reads(c, tmp_path)["state"]
    intact = frames(from_coordinator(c, workspace="w"))

    assert len(red.decisions) == len(intact.decisions)
    assert len(red.patch_ops) == len(intact.patch_ops)
    assert red.tasks["outcome"].value_counts().to_dict() == \
           intact.tasks["outcome"].value_counts().to_dict()
    # Which field reviewers keep correcting is the question patch_ops exists to
    # answer, and it survives losing what they wrote into it.
    assert red.patch_ops["path"].tolist() == ["/body"]
    assert red.patch_ops["op"].tolist() == ["replace"]
    assert red.overrides.iloc[0]["rationale"] == "House style."
    assert red.overrides.iloc[0]["based_on"] is None
    assert intact.overrides.iloc[0]["based_on"] is not None


@pytest.mark.parametrize("field", ["answer", "answer_text"])
def test_a_free_text_answer_is_redacted_under_either_of_its_names(field, tmp_path):
    # The coordinator reads the free text from `answer` or `answer_text`. A
    # redactor that knew one name left the other one attribute away.
    c = Coordinator(CoordinatorOptions(default_profiles=PROFILES))

    def ok(m, p=None, a="human:ana"):
        r = c.dispatch({"jsonrpc": "2.0", "id": m, "method": m,
                        "params": {"workspace": "w", "from": a, **(p or {})}})
        assert "error" not in r, f"{m}: {r.get('error')}"
        return r.get("result", {})

    ok("workspace.create", {"profiles": PROFILES})
    for uri, kind in [("human:ana", "human"), ("human:bo", "human"), ("agent:x", "agent")]:
        ok("participant.join", {"type": kind}, uri)
    t = ok("task.create", {"kind": "k", "input": {}, "assignee": "agent:x"})["task_id"]
    ok("whisper.ask", {"task_id": t, "to": ["human:bo"], "question": "q",
                       "deadline_ms": 600_000, "default_if_lapsed": "no"}, "agent:x")
    wid = next(iter(c.get_workspace("w").whispers))
    ok("whisper.answer", {"whisper_id": wid, field: "SECRET-ANSWER"}, "human:bo")

    for label, f in redacted_reads(c, tmp_path).items():
        assert not leaks(f.whispers["answer"].tolist()), f"{label} table"
        assert not leaks(f.chain.events), f"{label} events"
        assert not leaks(f.chain.state), f"{label} state"
    intact = frames(from_coordinator(c, workspace="w"))
    assert intact.whispers.iloc[0]["answer"] == "SECRET-ANSWER", "and unredacted, it is read"


def test_a_redacted_override_has_no_result_to_show(tmp_path):
    # result is the patch applied to based_on. With based_on gone there is
    # nothing to apply it to, and inventing a result from a null base would put
    # the patch's own content into a column the redactor was meant to clear.
    c = planted()
    for label, f in redacted_reads(c, tmp_path).items():
        assert f.overrides.iloc[0]["result"] is None, f"{label} read"
        assert f.overrides.iloc[0]["based_on"] is None, f"{label} read"


def test_a_chosen_option_survives_redaction_but_free_text_does_not():
    # An option id is one of the asker's own choices, so it is metadata and is
    # what makes a redacted whisper still worth counting.
    c = Coordinator(CoordinatorOptions(default_profiles=PROFILES))

    def ok(m, p=None, a="human:ana"):
        r = c.dispatch({"jsonrpc": "2.0", "id": m, "method": m,
                        "params": {"workspace": "w", "from": a, **(p or {})}})
        assert "error" not in r, f"{m}: {r.get('error')}"
        return r.get("result", {})

    ok("workspace.create", {"profiles": PROFILES})
    for uri, kind in [("human:ana", "human"), ("human:bo", "human"), ("agent:x", "agent")]:
        ok("participant.join", {"type": kind}, uri)
    t = ok("task.create", {"kind": "k", "input": {}, "assignee": "agent:x"})["task_id"]
    ok("whisper.ask", {"task_id": t, "to": ["human:bo"], "question": "refund?",
                       "options": [{"id": "yes", "label": "Refund"},
                                   {"id": "no", "label": "Decline"}],
                       "deadline_ms": 600_000, "default_if_lapsed": "no"}, "agent:x")
    wid = next(iter(c.get_workspace("w").whispers))
    ok("whisper.answer", {"whisper_id": wid, "answer_option": "yes"}, "human:bo")

    f = frames(from_coordinator(c, workspace="w", redact=redact_artefacts))
    assert f.whispers.iloc[0]["answer"] == "yes"
    assert bool(f.whispers.iloc[0]["had_options"])


# ------------------------------------------------------- damage limitation

def ev(seq, method, params, ts="2026-01-01T00:00:00.000Z"):
    return {"seq": seq, "arrived": ts,
            "envelope": {"method": method, "ts": ts, "params": params}}


@pytest.mark.parametrize("name,events", [
    ("params is a list", [ev(0, "task.create", ["not", "a", "dict"])]),
    ("params is a string", [ev(0, "task.create", "nope")]),
    ("an entry that is not a dict", [ev(0, "task.create", {"kind": "k"}), "junk", 42, None]),
    ("no seq at all", [{"envelope": {"method": "task.create", "params": {"kind": "k"}}}]),
    ("a null envelope", [{"seq": 0, "envelope": None}]),
    ("a method nobody has heard of", [ev(0, "quantum.entangle", {"x": 1})]),
    ("a deliberation addressed to an int", [ev(0, "deliberate.open", {"to": 7})]),
    ("a review addressed to a dict",
     [ev(0, "review.request", {"task_id": "T", "to": {"a": 1}, "artefact": {}})]),
    ("a handoff whose tasks are a string",
     [ev(0, "handoff.propose", {"to": "human:bo", "tasks": "T"})]),
    ("seq out of order", [ev(5, "task.create", {"kind": "k"}), ev(1, "task.create", {"kind": "j"})]),
])
def test_a_malformed_chain_still_projects(name, events):
    f = frames(Chain(workspace="w", events=events, state=None, source="test"))
    for table in TABLES:
        assert list(f[table.name].columns) == table.names, name


def test_two_exports_concatenated_do_not_lose_a_task():
    # Placeholder keys were the seq number, so a repeated seq overwrote the
    # earlier creation and the task vanished with no sign that it had.
    events = [ev(0, "task.create", {"kind": "one"}), ev(0, "task.create", {"kind": "two"})]
    f = frames(Chain(workspace="w", events=events, state=None, source="test"))
    assert len(f.events) == 2
    assert sorted(f.tasks["kind"].tolist()) == ["one", "two"]


@pytest.mark.parametrize("diff", [{"op": "add"}, "add /a", ["a", "b"], 7])
def test_a_malformed_patch_counts_no_operations_it_cannot_show(diff):
    # n_ops was the length of whatever arrived, so a string diff reported ten
    # operations against zero rows in patch_ops.
    events = [ev(0, "decide.override", {"task_id": "T", "diff": diff, "rationale": "r"})]
    f = frames(Chain(workspace="w", events=events, state=None, source="test"))
    assert f.overrides["n_ops"].tolist() == [len(f.patch_ops)] == [0]


def test_one_uncastable_value_costs_its_own_cell_and_no_others():
    # astype fails a whole column on one bad value, so a single client sending
    # a deadline as a string emptied deadline_ms for every whisper in the
    # workspace, and an empty column looks exactly like a source that could not
    # carry it.
    events = [
        ev(0, "whisper.ask", {"task_id": "T", "question": "a", "deadline_ms": 600_000}),
        ev(1, "whisper.answer", {"whisper_id": "wsp_A", "answer": "x"}),
        ev(2, "whisper.ask", {"task_id": "T", "question": "b", "deadline_ms": "not a number"}),
        ev(3, "whisper.answer", {"whisper_id": "wsp_B", "answer": "y"}),
    ]
    f = frames(Chain(workspace="w", events=events, state=None, source="test"))
    deadlines = f.whispers.set_index("question")["deadline_ms"]
    assert deadlines.loc["a"] == 600_000, "the well-formed row lost its value to its neighbour"
    assert pd.isna(deadlines.loc["b"])


def test_a_number_sent_as_a_string_is_read_rather_than_discarded():
    events = [
        ev(0, "whisper.ask", {"task_id": "T", "question": "a", "deadline_ms": "60000"}),
        ev(1, "whisper.answer", {"whisper_id": "wsp_A", "answer": "x"}),
    ]
    f = frames(Chain(workspace="w", events=events, state=None, source="test"))
    assert f.whispers["deadline_ms"].tolist() == [60_000]


def test_a_container_where_a_string_was_declared_is_nulled_not_rendered():
    # pandas will render a dict as its repr in a string column, which looks
    # like a value and is not one. A scalar is rendered as pandas would.
    events = [
        ev(0, "task.create", {"kind": {"nested": "object"}, "assignee": "agent:x"}),
        ev(1, "task.create", {"kind": 7, "assignee": "agent:x"}),
        ev(2, "task.update", {"task_id": "T1", "state": "in_progress"}),
        ev(3, "task.update", {"task_id": "T2", "state": "in_progress"}),
    ]
    f = frames(Chain(workspace="w", events=events, state=None, source="test"))
    kinds = f.tasks.set_index("task_id")["kind"]
    assert pd.isna(kinds.loc["T1"])
    assert kinds.loc["T2"] == "7"
