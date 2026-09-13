"""
The projection must not lose or invent anything.

These check the tables against the chain they came from, rather than against
a copy of what the projection produced. A test that asserts the output equals
a recorded output only proves the code has not changed; these prove it agrees
with the coordinator.
"""
from __future__ import annotations

import pandas as pd
import pytest

from chap_analytics import BY_NAME, TABLES, frames
from chap_analytics.schema import UNTYPED


# ------------------------------------------------------------ the contract

def test_every_table_matches_its_declared_schema(f):
    for table in TABLES:
        df = f[table.name]
        assert list(df.columns) == table.names, (
            f"{table.name} columns drifted from schema.py")
        for col in table.columns:
            if col.dtype in UNTYPED:
                continue
            assert str(df[col.name].dtype) == col.dtype, (
                f"{table.name}.{col.name} is {df[col.name].dtype}, "
                f"schema declares {col.dtype}")


def test_a_missing_column_is_null_not_absent(envelopes_only):
    # Read without server state, deliberation outcomes cannot be known. The
    # column must still be there, so downstream code sees NA and not KeyError.
    g = frames(envelopes_only)
    assert "outcome" in g.deliberations.columns
    assert g.deliberations["outcome"].isna().all()


def test_tables_are_addressable_by_name(f):
    for name in BY_NAME:
        assert isinstance(f[name], pd.DataFrame)
    with pytest.raises(KeyError, match="not a CHAP table"):
        f["nonsense"]


# ------------------------------------------------------ nothing lost or invented

def test_events_reproduce_the_chain_exactly(f, chain):
    assert len(f.events) == len(chain.events)
    assert f.events["seq"].tolist() == sorted(e["seq"] for e in chain.events)


def test_every_decision_in_the_chain_reaches_the_decisions_table(f, chain):
    deciding = {"decide.approve", "decide.reject", "decide.override", "abstain.declare"}
    in_chain = sum(1 for e in chain.events
                   if (e["envelope"].get("method")) in deciding)
    assert len(f.decisions) == in_chain, "a decision was dropped or duplicated"


def test_every_override_reaches_both_override_tables(f, chain):
    in_chain = [e for e in chain.events
                if e["envelope"].get("method") == "decide.override"]
    assert len(f.overrides) == len(in_chain)
    expected_ops = sum(len(e["envelope"]["params"].get("diff", [])) for e in in_chain)
    assert len(f.patch_ops) == expected_ops, "patch operations lost in the explode"


def test_no_task_is_dropped_including_one_never_touched_again(f, chain):
    # task.create is not the only way a task comes into being: escalate.raise
    # and control.supersede mint a successor server-side, with no create
    # envelope of its own. A count that ignores them is short.
    minting = {"task.create", "escalate.raise", "control.supersede"}
    created = sum(1 for e in chain.events
                  if e["envelope"].get("method") in minting)
    assert len(f.tasks) == created, (
        "tasks must survive even when nothing else ever references them")
    assert (f.tasks["kind"] == "orphan").sum() == 1


def test_a_task_minted_by_an_escalation_is_present_and_linked(f):
    successors = f.tasks[f.tasks["supersedes"].notna()]
    assert len(successors) == 1, "the escalation successor should be a task in its own right"
    original = successors.iloc[0]["supersedes"]
    assert f.tasks.set_index("task_id").loc[original, "outcome"] == "escalated"


def test_every_participant_is_present(f):
    assert set(f.participants["participant"]) == {
        "human:ana", "human:bo", "human:cy", "agent:drafter", "agent:reviewer-bot"}
    bot = f.participants.set_index("participant").loc["agent:reviewer-bot"]
    assert bot["n_decisions"] == 0, "a member who did nothing still gets a row"


# ------------------------------------------------------------- derived values

def test_confidence_is_parsed_from_its_decimal_string_wire_form(f):
    # §7: fractional values travel as strings. A column of strings here would
    # break every downstream statistic silently, which is the whole reason
    # this is done once in the library.
    conf = f.tasks["confidence"].dropna()
    assert len(conf) >= 2
    assert conf.dtype.kind == "f"
    assert pytest.approx(0.93) == f.tasks.loc[
        f.tasks["confidence"].notna() & (f.tasks["kind"] == "draft_response"),
        "confidence"].max()


def test_outcomes_are_classified_from_what_happened(f):
    outcomes = f.tasks["outcome"].value_counts().to_dict()
    assert outcomes.get("overridden") == 1
    assert outcomes.get("approved") == 3      # simple, second-pass, and the quorum
    assert outcomes.get("abstained") == 1
    assert outcomes.get("escalated") == 1
    assert outcomes.get("cancelled") == 1


def test_only_the_last_approval_settles_an_all_approve_review(f):
    quorum = f.decisions[f.decisions["rule"] == "all_approve"].sort_values("seq")
    assert len(quorum) == 2, "expected two reviewers on the contract clause"
    assert quorum["is_final"].tolist() == [False, True], (
        "the first approval must not settle a review that needs both")


def test_a_rejection_sent_back_does_not_settle_the_review(f):
    sent_back = f.decisions[f.decisions["request_revision"] == True]  # noqa: E712
    assert len(sent_back) == 1
    assert not sent_back.iloc[0]["is_final"], (
        "request_revision returns the task to in_progress, so the review is not over")


def test_patch_operations_are_explodable_to_the_field_corrected(f):
    ops = f.patch_ops
    assert set(ops["op"]) == {"replace", "add"}
    assert set(ops["top_path"]) == {"comments"}
    assert ops["depth"].max() == 3           # /comments/0/severity
    assert ops["op_index"].tolist() == [0, 1]


def test_latency_is_measured_from_the_review_opening(f):
    lat = f.decisions["latency_s"].dropna()
    assert len(lat) == len(f.decisions), "every decision here follows a review"
    assert (lat >= 0).all(), "a decision cannot precede the review it answers"


def test_an_unanswered_whisper_is_distinguishable_from_an_answered_one(f):
    assert len(f.whispers) == 2
    answered = f.whispers[f.whispers["answered"]]
    assert len(answered) == 1
    assert answered.iloc[0]["answer"] == "yes"
    assert answered.iloc[0]["response_s"] >= 0
    unanswered = f.whispers[~f.whispers["answered"]]
    assert unanswered.iloc[0]["response_s"] is pd.NA or pd.isna(
        unanswered.iloc[0]["response_s"])


def test_deliberation_tally_matches_the_votes_cast(f):
    assert len(f.deliberations) == 1
    d = f.deliberations.iloc[0]
    assert d["n_participants"] == 3
    assert (d["n_yea"], d["n_nay"], d["n_abstain"]) == (2, 0, 1)
    assert d["n_votes"] == len(f.votes) == 3
    assert d["turnout"] == pytest.approx(1.0)
    assert d["closed_at"] is not pd.NaT


def test_open_work_is_censored_rather_than_counted_as_fast(f):
    # lifetime_s is null while a task is open. A mean over it would otherwise
    # report only the work that finished, which flatters every latency claim.
    open_tasks = f.tasks[~f.tasks["settled"]]
    assert len(open_tasks) > 0, "the fixture should leave something open"
    assert open_tasks["lifetime_s"].isna().all()


# ------------------------------------------------- the two sources agree

def test_envelopes_alone_recover_what_state_recovers(envelopes_only, f):
    """
    The point of replaying rather than reading state: an MCP client with only
    audit.read must get the same analysis as someone holding the database.
    """
    g = frames(envelopes_only)
    assert not g.chain.has_state and f.chain.has_state

    for table in ("tasks", "decisions", "overrides", "patch_ops", "votes"):
        assert len(g[table]) == len(f[table]), (
            f"{table} differs between an envelope-only read and a stateful one")

    assert g.decisions["kind"].value_counts().to_dict() == \
           f.decisions["kind"].value_counts().to_dict()
    assert g.tasks["outcome"].value_counts().to_dict() == \
           f.tasks["outcome"].value_counts().to_dict()


#: Columns whose value must be identical whichever way the chain was read.
#: Everything left out is either state-only by declaration or a flag about the
#: read itself. assignee is excluded because task.route names its choice in the
#: result; assignee_certain is how a row says so.
AGREE_ON = [
    "kind", "delegator", "original_assignee", "mode", "review_required",
    "state", "created_at", "settled", "settled_at", "lifetime_s", "outcome",
    "was_reviewed", "was_overridden", "n_reviews", "n_decisions",
    "confidence", "criticality", "risk_tier", "supersedes",
]

def _same(x, y) -> bool:
    if pd.isna(x) and pd.isna(y):
        return True
    if pd.isna(x) or pd.isna(y):
        return False
    return x == y


def test_the_two_reads_agree_cell_by_cell_on_the_rows_they_can_both_identify(envelopes_only, f):
    """
    Comparing counts and value_counts is how eight disagreements hid in this
    fixture: two tasks had their criticality swapped, a third carried another
    task's assignee and a provenance link it never had, and the totals came out
    the same either way. A row-by-row comparison is the only one that catches
    a permutation.

    Rows an envelope-only read cannot identify are excluded, because it says so
    in id_certain rather than pretending otherwise; that they are excluded is
    itself asserted, so the exemption cannot quietly grow.
    """
    g = frames(envelopes_only)
    a = g.tasks.set_index("task_id")
    b = f.tasks.set_index("task_id")

    shared = [i for i in b.index if i in a.index and bool(a.loc[i, "id_certain"])]
    assert shared, "the fixture should identify at least some tasks from envelopes alone"

    for tid in shared:
        for col in AGREE_ON:
            x, y = a.loc[tid, col], b.loc[tid, col]
            assert _same(x, y), (
                f"{tid} {col}: envelope-only read says {x!r}, stateful says {y!r}")

    unsure = [i for i in a.index if not bool(a.loc[i, "id_certain"])]
    assert len(shared) + len(unsure) == len(a), (
        "every row is either compared or declared uncertain")


def test_the_override_base_artefact_is_reconstructed_from_envelopes(envelopes_only):
    # based_on is not in the override envelope: it arrives on the preceding
    # review.request. Recovering it is what makes the chain self-sufficient.
    g = frames(envelopes_only)
    ov = g.overrides.iloc[0]
    assert ov["based_on"] == {
        "comments": [{"path": "src/pay.ts", "severity": "warning", "body": "Cast."}]}


# ------------------------------------------- what the coordinator would say

def _fresh():
    from chap_coordinator import Coordinator, CoordinatorOptions
    from conftest import PROFILES
    c = Coordinator(CoordinatorOptions(default_profiles=PROFILES))

    def ok(m, p=None, a="human:ana"):
        r = c.dispatch({"jsonrpc": "2.0", "id": m, "method": m,
                        "params": {"workspace": "w", "from": a, **(p or {})}})
        assert "error" not in r, f"{m}: {r.get('error')}"
        return r.get("result", {})

    ok("workspace.create", {"profiles": PROFILES})
    for uri, kind in [("human:ana", "human"), ("human:bo", "human"), ("agent:x", "agent")]:
        ok("participant.join", {"type": kind, "role": "original"}, uri)
    return c, ok


def _both(c):
    from chap_analytics import from_coordinator
    from chap_analytics.load import Chain
    entries = c.dispatch({"jsonrpc": "2.0", "id": "r", "method": "audit.read",
                          "params": {"workspace": "w", "from": "human:ana"}})["result"]["entries"]
    return (frames(Chain(workspace="w", events=entries, state=None, source="audit.read")),
            frames(from_coordinator(c, workspace="w")))


def test_the_corrected_artefact_is_reconstructed_from_envelopes_alone():
    # based_on arrives on review.request and the patch on decide.override, so
    # the result is their combination and needs no server state.
    c, ok = _fresh()
    t = ok("task.create", {"kind": "k", "input": {}, "assignee": "agent:x"})["task_id"]
    ok("task.complete", {"task_id": t, "output": {"body": "Cast.", "tags": ["a"]}}, "agent:x")
    ok("review.request", {"task_id": t, "artefact": {"body": "Cast.", "tags": ["a"]},
                          "to": ["human:ana"]}, "agent:x")
    ok("decide.override", {"task_id": t, "rationale": "House style.",
                           "diff": [{"op": "replace", "path": "/body", "value": "Cast, please."},
                                    {"op": "add", "path": "/tags/-", "value": "b"}]}, "human:ana")

    stored = next(iter(c.get_workspace("w").overrides.values())).result
    env, state = _both(c)
    assert env.overrides.iloc[0]["result"] == stored == {"body": "Cast, please.", "tags": ["a", "b"]}
    assert state.overrides.iloc[0]["result"] == stored


def test_a_member_joining_again_keeps_their_original_record():
    # The coordinator merges identity bindings on a repeat join and changes
    # nothing else, so joined_at and role are those of the first join.
    c, ok = _fresh()
    ok("participant.join", {"type": "human", "role": "impostor"}, "human:ana")

    for label, f in zip(("envelopes", "state"), _both(c)):
        row = f.participants.set_index("participant").loc["human:ana"]
        assert row["role"] == "original", f"{label} read"
        first_join = f.events[(f.events["method"] == "participant.join")
                              & (f.events["actor"] == "human:ana")].iloc[0]["ts"]
        assert row["joined_at"] == first_join, f"{label} read"


def test_the_first_close_of_a_deliberation_is_the_one_that_closed_it():
    c, ok = _fresh()
    did = ok("deliberate.open", {"to": ["human:ana", "human:bo"], "rule": "any_one_approves",
                                 "question": "Ship?"}, "human:ana")["deliberation_id"]
    ok("deliberate.vote", {"deliberation_id": did, "vote": "yea"}, "human:ana")
    ok("deliberate.close", {"deliberation_id": did}, "human:ana")
    ok("deliberate.close", {"deliberation_id": did}, "human:bo")

    for label, f in zip(("envelopes", "state"), _both(c)):
        closes = f.events[f.events["method"] == "deliberate.close"].sort_values("seq")
        assert f.deliberations.iloc[0]["closed_at"] == closes.iloc[0]["ts"], f"{label} read"


def test_the_assignee_may_be_named_as_to_on_creation():
    c, ok = _fresh()
    ok("task.create", {"kind": "k", "input": {}, "to": "agent:x"})
    for label, f in zip(("envelopes", "state"), _both(c)):
        row = f.tasks.iloc[0]
        assert row["assignee"] == "agent:x", f"{label} read"
        assert row["original_assignee"] == "agent:x", f"{label} read"


# ------------------------------------------------------------------ privacy

def test_a_redactor_removes_artefact_bodies_and_keeps_the_analysis(driver):
    from chap_analytics import from_coordinator, redact_artefacts
    redacted = frames(from_coordinator(
        driver.coord, workspace=driver.workspace, redact=redact_artefacts))
    intact = frames(from_coordinator(driver.coord, workspace=driver.workspace))

    # Shape survives: the counts an analyst works from are all metadata.
    assert len(redacted.decisions) == len(intact.decisions)
    assert len(redacted.patch_ops) == len(intact.patch_ops)
    assert redacted.tasks["outcome"].value_counts().to_dict() == \
           intact.tasks["outcome"].value_counts().to_dict()

    # Content does not: the artefact the reviewer saw is gone.
    assert redacted.overrides.iloc[0]["based_on"] is None
    assert intact.overrides.iloc[0]["based_on"] is not None
