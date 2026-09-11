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


# ------------------------------------------------------------ the contract

def test_every_table_matches_its_declared_schema(f):
    for table in TABLES:
        df = f[table.name]
        assert list(df.columns) == table.names, (
            f"{table.name} columns drifted from schema.py")
        for col in table.columns:
            if col.dtype in ("object", "list"):
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


def test_the_override_base_artefact_is_reconstructed_from_envelopes(envelopes_only):
    # based_on is not in the override envelope: it arrives on the preceding
    # review.request. Recovering it is what makes the chain self-sufficient.
    g = frames(envelopes_only)
    ov = g.overrides.iloc[0]
    assert ov["based_on"] == {
        "comments": [{"path": "src/pay.ts", "severity": "warning", "body": "Cast."}]}


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
