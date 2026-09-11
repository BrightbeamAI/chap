"""
The tables, their columns, and where each column comes from.

This module is the contract. Every projection in ``frames.py`` is checked
against it, so a column cannot appear in a table without being declared here
first, and a declared column cannot silently vanish.

Provenance matters as much as the dtype. A CHAP chain can be read two ways and
they do not carry the same information:

``envelopes``
    The audit log alone, which is all ``audit.read`` returns and therefore all
    an MCP client can obtain. Every request parameter is present, in order.

``state``
    A workspace snapshot, from a SqliteStore file or an in-process
    Coordinator. Adds what the server computed: deliberation outcomes, route
    decision outcomes, and the stored override artefacts.

``replay``
    Derived by replaying the envelope stream. The artefact under review
    arrives on ``review.request`` and the patch on ``decide.override``, so the
    before and after can be reconstructed from envelopes alone. Available from
    either source, and checked against ``state`` when both are present.

A column whose source is unavailable is present and null, never absent. Code
downstream should be able to reference a column without asking where the data
came from, and see missingness rather than a KeyError.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Provenance = Literal["envelopes", "state", "replay", "derived"]

#: dtypes that pandas cannot enforce. "list" is normalised to an empty list
#: rather than null, so `.explode()` and `len()` work without a guard;
#: "object" holds an arbitrary artefact and is left exactly as it arrived.
UNTYPED = ("object", "list")


@dataclass(frozen=True)
class Column:
    name: str
    dtype: str
    provenance: Provenance
    doc: str


@dataclass(frozen=True)
class Table:
    name: str
    grain: str
    doc: str
    columns: tuple[Column, ...]

    @property
    def names(self) -> list[str]:
        return [c.name for c in self.columns]

    def dtypes(self) -> dict[str, str]:
        return {c.name: c.dtype for c in self.columns}


def _c(name: str, dtype: str, provenance: Provenance, doc: str) -> Column:
    return Column(name, dtype, provenance, doc)


EVENTS = Table(
    name="events",
    grain="one row per audit log entry",
    doc=(
        "The chain itself, flattened. Every other table is a projection of "
        "this one, so a count here that does not reconcile with the tables "
        "below is a bug in the projection rather than in the data."
    ),
    columns=(
        _c("seq", "Int64", "envelopes", "Position in the log. Gapless from zero within a workspace."),
        _c("workspace", "string", "envelopes", "Workspace id."),
        _c("ts", "datetime64[ns, UTC]", "envelopes", "Envelope timestamp, as the sender set it."),
        _c("arrived", "datetime64[ns, UTC]", "envelopes", "When the coordinator accepted it. Differs from ts under clock skew or replay."),
        _c("method", "string", "envelopes", "CHAP method, without the MCP 'chap.' prefix."),
        _c("actor", "string", "envelopes", "The 'from' participant URI: who made the call."),
        _c("actor_kind", "string", "derived", "URI scheme of the actor: human, agent, service, group or workspace."),
        _c("task_id", "string", "envelopes", "Task the envelope concerns, where it names one."),
        _c("prev_hash", "string", "envelopes", "Hash link to the previous entry, when chaining is on."),
        _c("chained", "boolean", "derived", "Whether this entry carries a chain link."),
    ),
)

TASKS = Table(
    name="tasks",
    grain="one row per task",
    doc=(
        "A task and how it ended. The lifecycle columns are derived by "
        "replaying state transitions, so they hold for a chain read without "
        "server state."
    ),
    columns=(
        _c("task_id", "string", "envelopes", "Task id."),
        _c("workspace", "string", "envelopes", "Workspace id."),
        _c("kind", "string", "envelopes", "Operator-defined task kind, uninterpreted by the coordinator."),
        _c("delegator", "string", "envelopes", "Who created the task."),
        _c("assignee", "string", "replay", "Current assignee, after any routing or handoff reassignment."),
        _c("original_assignee", "string", "envelopes", "Assignee at creation, before any reassignment."),
        _c("mode", "string", "envelopes", "shadow, trial or production."),
        _c("review_required", "boolean", "envelopes", "Whether completion depends on a reviewer decision."),
        _c("state", "string", "replay", "Terminal or current state."),
        _c("created_at", "datetime64[ns, UTC]", "envelopes", "When the task was created."),
        _c("settled_at", "datetime64[ns, UTC]", "replay", "When it reached a terminal state. Null while open."),
        _c("settled", "boolean", "derived", "Whether the task reached a terminal state. False means censored, not failed."),
        _c("lifetime_s", "float64", "derived", "Seconds from creation to settlement. Null while open, so a mean over this column silently drops open work: use the censoring flag."),
        _c("outcome", "string", "derived", "How it ended for analysis: approved, overridden, rejected, abstained, escalated, cancelled, superseded, completed_without_review, or open."),
        _c("was_reviewed", "boolean", "derived", "Whether any review was opened on it."),
        _c("was_overridden", "boolean", "derived", "Whether a human corrected the artefact rather than accepting or refusing it."),
        _c("n_decisions", "Int64", "derived", "Number of decisions recorded against it."),
        _c("confidence", "float64", "derived", "Self-reported confidence, parsed from its decimal-string wire form. Null when not supplied."),
        _c("criticality", "string", "envelopes", "Routing hint, where given."),
        _c("risk_tier", "string", "envelopes", "Routing hint, where given."),
        _c("supersedes", "string", "envelopes", "Task this one replaced, for a supersede or an escalation."),
    ),
)

DECISIONS = Table(
    name="decisions",
    grain="one row per review decision",
    doc=(
        "Every approve, reject, override and abstain. The unit of analysis "
        "for anything about reviewers. Several rows share a task where the "
        "review rule needed more than one decision."
    ),
    columns=(
        _c("task_id", "string", "envelopes", "Task decided on."),
        _c("workspace", "string", "envelopes", "Workspace id."),
        _c("seq", "Int64", "envelopes", "Position of the deciding envelope in the log."),
        _c("reviewer", "string", "envelopes", "Who decided."),
        _c("kind", "string", "envelopes", "approve, reject, override or abstain."),
        _c("ts", "datetime64[ns, UTC]", "envelopes", "When the decision was made."),
        _c("requested_at", "datetime64[ns, UTC]", "replay", "When the review this decision belongs to was opened."),
        _c("latency_s", "float64", "derived", "Seconds from the review opening to this decision. Elapsed time, not effort: a reviewer answering after three days may have spent ninety seconds."),
        _c("rule", "string", "replay", "The review rule in force: any_one_approves, all_approve or quorum:N."),
        _c("decision_index", "Int64", "derived", "Order of this decision within its review, from zero."),
        _c("is_final", "boolean", "derived", "Whether this decision settled the review."),
        _c("comment", "string", "envelopes", "Free-text note, where given."),
        _c("tags", "list", "envelopes", "Workspace-defined labels as a list. Empty list, never null."),
        _c("n_tags", "Int64", "derived", "Number of tags, for filtering without unpacking the list."),
        _c("request_revision", "boolean", "envelopes", "On a rejection, whether the task was sent back rather than declined."),
        _c("abstain_category", "string", "envelopes", "On an abstention, the stated category."),
        _c("digest_bound", "boolean", "derived", "Whether the decision carried an artefact digest, binding it to exact content."),
        _c("task_kind", "string", "derived", "Denormalised from tasks, so reviewer analyses need no join."),
        _c("assignee", "string", "derived", "Denormalised: who produced the work being judged."),
    ),
)

OVERRIDES = Table(
    name="overrides",
    grain="one row per override",
    doc=(
        "The corrections. This is the supervision signal: what a human "
        "changed, why, and whether they were refining the agent's decision or "
        "reversing it. One row per override; the individual patch operations "
        "are in ``patch_ops``."
    ),
    columns=(
        _c("task_id", "string", "envelopes", "Task corrected."),
        _c("workspace", "string", "envelopes", "Workspace id."),
        _c("seq", "Int64", "envelopes", "Position of the override envelope in the log."),
        _c("reviewer", "string", "envelopes", "Who made the correction."),
        _c("ts", "datetime64[ns, UTC]", "envelopes", "When."),
        _c("rationale", "string", "envelopes", "Why, in the reviewer's words. Required by the protocol, so never null."),
        _c("tags", "list", "envelopes", "Workspace-defined labels as a list."),
        _c("policy_refs", "list", "envelopes", "Policies or guidelines the correction applies, as a list."),
        _c("intent_preserved", "boolean", "envelopes", "True where the edit refined the decision the draft was making, false where it substituted a different one. Null where the client did not say."),
        _c("logical_id", "string", "envelopes", "Caller-chosen handle for the underlying item, stable across revisions."),
        _c("n_ops", "Int64", "derived", "Number of patch operations."),
        _c("op_kinds", "list", "derived", "Distinct RFC 6902 operations used, as a sorted list."),
        _c("paths", "list", "derived", "JSON Pointers touched, as a list."),
        _c("top_path", "string", "derived", "First path segment, the usual grouping key for 'which part of the output gets corrected'."),
        _c("based_on", "object", "replay", "The artefact before correction, reconstructed from the review request."),
        _c("result", "object", "replay", "The artefact after the patch was applied."),
        _c("task_kind", "string", "derived", "Denormalised from tasks."),
        _c("assignee", "string", "derived", "Denormalised: whose work was corrected."),
        _c("confidence", "float64", "derived", "Denormalised: what the producer claimed. Pairs with the correction for calibration."),
    ),
)

PATCH_OPS = Table(
    name="patch_ops",
    grain="one row per RFC 6902 operation within an override",
    doc=(
        "Overrides exploded to the operation. The grain that answers which "
        "field of an output gets corrected most, which a per-override table "
        "cannot without unnesting."
    ),
    columns=(
        _c("task_id", "string", "envelopes", "Task corrected."),
        _c("workspace", "string", "envelopes", "Workspace id."),
        _c("seq", "Int64", "envelopes", "Position of the parent override envelope."),
        _c("reviewer", "string", "envelopes", "Who made the correction."),
        _c("op_index", "Int64", "derived", "Position within the patch, from zero."),
        _c("op", "string", "envelopes", "add, remove, replace, move, copy or test."),
        _c("path", "string", "envelopes", "JSON Pointer into the artefact."),
        _c("top_path", "string", "derived", "First path segment."),
        _c("depth", "Int64", "derived", "Pointer depth, so field-level and nested corrections can be told apart."),
    ),
)

PARTICIPANTS = Table(
    name="participants",
    grain="one row per participant per workspace",
    doc="Who was involved, and what they did. Counts are denormalised so a reviewer league table needs no joins.",
    columns=(
        _c("participant", "string", "envelopes", "Participant URI."),
        _c("workspace", "string", "envelopes", "Workspace id."),
        _c("kind", "string", "envelopes", "human, agent, service, group or workspace."),
        _c("role", "string", "envelopes", "Operator-defined role, where given."),
        _c("joined_at", "datetime64[ns, UTC]", "envelopes", "When they joined."),
        _c("left_at", "datetime64[ns, UTC]", "replay", "When they left, null if still a member."),
        _c("n_tasks_assigned", "Int64", "derived", "Tasks assigned to them."),
        _c("n_decisions", "Int64", "derived", "Decisions they made."),
        _c("n_overrides", "Int64", "derived", "Corrections they made."),
        _c("n_abstentions", "Int64", "derived", "Reviews they stood aside from."),
    ),
)

DELIBERATIONS = Table(
    name="deliberations",
    grain="one row per deliberation",
    doc="Group decisions. The outcome is computed at close, so it is null while a deliberation is open.",
    columns=(
        _c("deliberation_id", "string", "envelopes", "Deliberation id."),
        _c("workspace", "string", "envelopes", "Workspace id."),
        _c("task_id", "string", "envelopes", "Task it concerns, where it names one."),
        _c("opener", "string", "envelopes", "Who opened it."),
        _c("rule", "string", "envelopes", "Voting rule in force."),
        _c("question", "string", "envelopes", "What was being decided."),
        _c("opened_at", "datetime64[ns, UTC]", "envelopes", "When."),
        _c("closed_at", "datetime64[ns, UTC]", "replay", "When closed, null while open."),
        _c("n_participants", "Int64", "derived", "How many were entitled to vote."),
        _c("n_votes", "Int64", "derived", "How many voted."),
        _c("n_yea", "Int64", "derived", "Votes in favour."),
        _c("n_nay", "Int64", "derived", "Votes against."),
        _c("n_abstain", "Int64", "derived", "Abstentions, which count as neither side."),
        _c("turnout", "float64", "derived", "Votes over participants."),
        _c("outcome", "string", "state", "approved or rejected. Computed at close, so unavailable from envelopes alone."),
    ),
)

VOTES = Table(
    name="votes",
    grain="one row per vote in a deliberation",
    doc="The individual votes, for agreement analysis across a group rather than a review.",
    columns=(
        _c("deliberation_id", "string", "envelopes", "Deliberation voted in."),
        _c("workspace", "string", "envelopes", "Workspace id."),
        _c("seq", "Int64", "envelopes", "Position of the voting envelope."),
        _c("voter", "string", "envelopes", "Who voted."),
        _c("vote", "string", "envelopes", "yea, nay or abstain."),
        _c("ts", "datetime64[ns, UTC]", "envelopes", "When."),
        _c("comment", "string", "envelopes", "Why, where given."),
        _c("veto_invoked", "boolean", "envelopes", "Whether a veto was claimed. Only honoured under a veto rule."),
    ),
)

WHISPERS = Table(
    name="whispers",
    grain="one row per whisper",
    doc=(
        "Deadline-bound questions. The interesting column is whether it "
        "lapsed: a high lapse rate means the humans are not there when the "
        "agents need them, which no other table shows."
    ),
    columns=(
        _c("whisper_id", "string", "envelopes", "Whisper id."),
        _c("workspace", "string", "envelopes", "Workspace id."),
        _c("task_id", "string", "envelopes", "Task it concerns."),
        _c("asker", "string", "envelopes", "Who asked."),
        _c("question", "string", "envelopes", "What was asked."),
        _c("asked_at", "datetime64[ns, UTC]", "envelopes", "When."),
        _c("deadline_ms", "Int64", "envelopes", "How long it was given."),
        _c("answered_at", "datetime64[ns, UTC]", "replay", "When answered, null if never."),
        _c("answered_by", "string", "replay", "Who answered."),
        _c("answer", "string", "replay", "The answer, or the chosen option id."),
        _c("answered", "boolean", "derived", "Whether a human answered before the deadline passed."),
        _c("response_s", "float64", "derived", "Seconds to answer. Null where unanswered."),
        _c("had_options", "boolean", "derived", "Whether it was multiple choice."),
    ),
)

ROUTING = Table(
    name="routing",
    grain="one row per routing decision",
    doc=(
        "What the routing policy chose and why. Recording the alternatives it "
        "passed over is what makes a routing policy auditable rather than "
        "merely observable."
    ),
    columns=(
        _c("workspace", "string", "envelopes", "Workspace id."),
        _c("seq", "Int64", "envelopes", "Position of the envelope."),
        _c("task_id", "string", "envelopes", "Task routed."),
        _c("method", "string", "envelopes", "task.route, review.depth or escalate.auto."),
        _c("ts", "datetime64[ns, UTC]", "envelopes", "When."),
        _c("selected", "string", "state", "Assignee chosen, for task.route."),
        _c("depth", "string", "state", "skip, spot_check or full, for review.depth."),
        _c("escalated", "boolean", "state", "Whether escalate.auto decided to escalate."),
        _c("policy_id", "string", "state", "Which policy produced the answer."),
        _c("n_alternatives", "Int64", "state", "How many candidates were passed over."),
    ),
)

TABLES: tuple[Table, ...] = (
    EVENTS, TASKS, DECISIONS, OVERRIDES, PATCH_OPS,
    PARTICIPANTS, DELIBERATIONS, VOTES, WHISPERS, ROUTING,
)

BY_NAME: dict[str, Table] = {t.name: t for t in TABLES}


def describe() -> str:
    """A readable rendering of the whole contract, for a notebook or a README."""
    out: list[str] = []
    for t in TABLES:
        out.append(f"{t.name}  ({t.grain})")
        out.append(f"  {t.doc}")
        width = max(len(c.name) for c in t.columns)
        for c in t.columns:
            out.append(f"    {c.name:<{width}}  {c.dtype:<24} [{c.provenance}]  {c.doc}")
        out.append("")
    return "\n".join(out)
