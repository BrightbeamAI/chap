"""
The tables, their columns, and where each column comes from.

This module is the contract. Every projection in ``frames.py`` is checked
against it: a column reaches a table by being declared here, and a declared
column is present in every frame the projection produces.

Provenance matters as much as the dtype. A CHAP chain can be read two ways,
and they carry different information:

``envelopes``
    The audit log, which is what ``audit.read`` returns and therefore what an
    MCP client can obtain. Every request parameter is present, in order.

``state``
    A workspace snapshot, from a SqliteStore file or an in-process
    Coordinator. Adds what the server computed: deliberation outcomes, route
    decision outcomes, and the stored override artefacts.

``replay``
    Derived by replaying the envelope stream. The artefact under review
    arrives on ``review.request`` and the patch on ``decide.override``, so the
    before and the after are reconstructed from envelopes alone. Available
    from either source. Where both are present the stored value is used, and
    the differential suite requires the replayed one to agree with it.

``derived``
    Computed from the other columns: counts, durations, flags and labels.

A column whose source is unavailable is present and null. Code downstream can
reference any column and see missingness rather than a KeyError.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Provenance = Literal["envelopes", "state", "replay", "derived"]

#: dtypes pandas leaves to the projection. "list" is normalised to an empty
#: list rather than null, so `.explode()` and `len()` work on every row;
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
        "this one, so every count below reconciles with a count here."
    ),
    columns=(
        _c("seq", "Int64", "envelopes", "Position in the log. Gapless from zero within a workspace."),
        _c("workspace", "string", "envelopes", "Workspace id."),
        _c("ts", "datetime64[ns, UTC]", "envelopes", "Envelope timestamp, as the sender set it."),
        _c("arrived", "datetime64[ns, UTC]", "envelopes", "When the coordinator accepted it. Differs from ts under clock skew or replay."),
        _c("method", "string", "envelopes", "CHAP method, as named on the wire: task.create, decide.override."),
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
        "replaying state transitions, so they hold for a chain read from "
        "envelopes alone."
    ),
    columns=(
        _c("task_id", "string", "envelopes", "Task id."),
        _c("workspace", "string", "envelopes", "Workspace id."),
        _c("kind", "string", "envelopes", "Operator-defined task kind, uninterpreted by the coordinator."),
        _c("delegator", "string", "envelopes", "Who created the task."),
        _c("assignee", "string", "replay", "Current assignee, after any handoff. A task.route reassignment names its choice in the result rather than the envelope, so it is reflected where the source carried server state; see assignee_certain."),
        _c("assignee_certain", "boolean", "derived", "Whether assignee is known to be current. False where a task.route ran and the source could only say who held the task before it. Also false where the assignee was inherited from, or moved by, an offer whose own identity is inferred."),
        _c("id_certain", "boolean", "derived", "Whether this row's id is paired with its creation envelope beyond doubt. False where the pairing is inferred from the order of events. In that case everything the creation said may belong to a task created around the same moment: kind, delegator, mode, review_required, the routing hints, supersedes, and through review_required the lifecycle columns too. Conservative: also false where the candidates were indistinguishable and the choice therefore changed nothing. Filter on it before drawing a conclusion from a single row."),
        _c("original_assignee", "string", "envelopes", "Assignee at creation, before any reassignment."),
        _c("mode", "string", "envelopes", "shadow, trial or production."),
        _c("review_required", "boolean", "envelopes", "Whether completion depends on a reviewer decision."),
        _c("state", "string", "replay", "Terminal or current state."),
        _c("created_at", "datetime64[ns, UTC]", "envelopes", "When the task was created."),
        _c("settled_at", "datetime64[ns, UTC]", "replay", "When it reached a terminal state. Null while open."),
        _c("settled", "boolean", "derived", "Whether the task reached a terminal state. False means the row is censored: the work was still running when the chain was read."),
        _c("lifetime_s", "float64", "derived", "Seconds from creation to settlement. Null while open. A mean over this column covers the finished work alone; use settled to say so."),
        _c("outcome", "string", "derived", "How it ended, read from the decision that settled the last review pass. approved, overridden, rejected and abstained name a reviewer's final decision. completed_after_rejection is work that shipped after a reviewer said no; completed_bypassing_review shipped while a review was open and unsettled; completed_without_review had no review. declined is the assignee refusing the work through task.update. escalated, cancelled and superseded are what they say, and open is anything still running. An earlier pass judged an artefact that was sent back, so it is set aside."),
        _c("was_reviewed", "boolean", "derived", "Whether any review was opened on it."),
        _c("was_overridden", "boolean", "derived", "Whether a human corrected the artefact rather than accepting or refusing it."),
        _c("n_reviews", "Int64", "derived", "Review passes opened on it. Two or more means the work was sent back and reviewed again, and each pass judged its own artefact."),
        _c("n_decisions", "Int64", "derived", "Number of decisions recorded against it, across every pass."),
        _c("confidence", "float64", "derived", "Self-reported confidence, parsed from its decimal-string wire form. Null where the agent gave none."),
        _c("criticality", "string", "envelopes", "Routing hint, where given."),
        _c("risk_tier", "string", "envelopes", "Routing hint, where given."),
        _c("supersedes", "string", "envelopes", "Task this one replaced, for a supersession or an escalation."),
        _c("fulfils", "string", "envelopes", "On a wrapped tool call or exchange, the id of the decision the caller said it carries out. Asserted by the producer and not verified by the coordinator, so an incorrect id is a dangling reference; a link, not evidence. Null where none was given."),
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
        _c("requested_at", "datetime64[ns, UTC]", "replay", "When the review pass this decision belongs to was opened."),
        _c("latency_s", "float64", "derived", "Seconds from the opening of this decision's own review pass. Elapsed time: a reviewer answering after three days may have spent ninety seconds on it."),
        _c("rule", "string", "replay", "The review rule in force: any_one_approves, all_approve or quorum:N."),
        _c("review_index", "Int64", "derived", "Which review pass on this task, from zero. A task sent back for revision is reviewed afresh, and a rate computed within a pass counts decisions about one artefact."),
        _c("decision_index", "Int64", "derived", "Order of this decision within its review pass, from zero."),
        _c("is_final", "boolean", "derived", "Whether this decision settled the review."),
        _c("comment", "string", "envelopes", "Free-text note, where given."),
        _c("tags", "list", "envelopes", "Workspace-defined labels as a list. An empty list where none were given."),
        _c("n_tags", "Int64", "derived", "Number of tags, for filtering ahead of unpacking the list."),
        _c("request_revision", "boolean", "envelopes", "On a rejection, whether the task was sent back rather than declined."),
        _c("abstain_category", "string", "envelopes", "On an abstention, the stated category."),
        _c("digest_bound", "boolean", "derived", "Whether the decision carried an artefact digest, binding it to exact content."),
        _c("task_kind", "string", "derived", "Denormalised from tasks, so a reviewer analysis reads it here directly."),
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
        _c("rationale", "string", "envelopes", "Why, in the reviewer's words. The protocol requires it, so it is always present."),
        _c("tags", "list", "envelopes", "Workspace-defined labels as a list."),
        _c("policy_refs", "list", "envelopes", "Policies or guidelines the correction applies, as a list."),
        _c("intent_preserved", "boolean", "envelopes", "True where the edit refined the decision the draft was making, false where it substituted a different one. Null where the client left it unsaid."),
        _c("logical_id", "string", "envelopes", "Caller-chosen handle for the underlying item, stable across revisions."),
        _c("n_ops", "Int64", "derived", "Number of patch operations."),
        _c("op_kinds", "list", "derived", "Distinct RFC 6902 operations used, as a sorted list."),
        _c("paths", "list", "derived", "JSON Pointers touched, as a list."),
        _c("top_path", "string", "derived", "First path segment, the usual grouping key for which part of the output gets corrected."),
        _c("based_on", "object", "replay", "The artefact before correction, reconstructed from the review request."),
        _c("result", "object", "replay", "The artefact after correction: the patch applied to based_on, with the coordinator's own RFC 6902 semantics. Where state carries the artefact the coordinator stored, that is used, and the differential suite requires the two to agree."),
        _c("task_kind", "string", "derived", "Denormalised from tasks."),
        _c("assignee", "string", "derived", "Denormalised: whose work was corrected."),
        _c("confidence", "float64", "derived", "Denormalised: what the producer claimed. Pairs with the correction for calibration."),
    ),
)

PATCH_OPS = Table(
    name="patch_ops",
    grain="one row per RFC 6902 operation within an override",
    doc=(
        "Overrides exploded to the operation. This is the grain that answers "
        "which field of an output gets corrected most."
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
    doc="Who was involved, and what they did. Counts are denormalised so a reviewer league table is a single frame.",
    columns=(
        _c("participant", "string", "envelopes", "Participant URI."),
        _c("workspace", "string", "envelopes", "Workspace id."),
        _c("kind", "string", "envelopes", "human, agent, service, group or workspace."),
        _c("role", "string", "envelopes", "Operator-defined role, where given."),
        _c("joined_at", "datetime64[ns, UTC]", "envelopes", "When they joined."),
        _c("left_at", "datetime64[ns, UTC]", "replay", "When they left. Null for a current member."),
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
        _c("closed_at", "datetime64[ns, UTC]", "replay", "When closed. Null while open."),
        _c("n_participants", "Int64", "derived", "How many were entitled to vote."),
        _c("n_votes", "Int64", "derived", "How many voted."),
        _c("n_yea", "Int64", "derived", "Votes in favour."),
        _c("n_nay", "Int64", "derived", "Votes against."),
        _c("n_abstain", "Int64", "derived", "Abstentions. They count towards n_votes and turnout."),
        _c("turnout", "float64", "derived", "Votes over participants."),
        _c("outcome", "string", "state", "approved or rejected. Computed at close, so available with server state."),
        _c("id_certain", "boolean", "derived", "Whether this row's id is paired with the opening envelope beyond doubt. See tasks.id_certain."),
    ),
)

VOTES = Table(
    name="votes",
    grain="one row per vote in a deliberation",
    doc="The individual votes, for agreement analysis across a group.",
    columns=(
        _c("deliberation_id", "string", "envelopes", "Deliberation voted in."),
        _c("workspace", "string", "envelopes", "Workspace id."),
        _c("seq", "Int64", "envelopes", "Position of the voting envelope."),
        _c("voter", "string", "envelopes", "Who voted."),
        _c("vote", "string", "envelopes", "yea, nay or abstain."),
        _c("ts", "datetime64[ns, UTC]", "envelopes", "When."),
        _c("comment", "string", "envelopes", "Why, where given."),
        _c("veto_invoked", "boolean", "envelopes", "Whether a veto was claimed. Honoured under a veto rule."),
    ),
)

WHISPERS = Table(
    name="whispers",
    grain="one row per whisper",
    doc=(
        "Deadline-bound questions. The interesting column is lapsed: a high "
        "lapse rate means the agents needed a person who was elsewhere, and "
        "this is the table that shows it."
    ),
    columns=(
        _c("whisper_id", "string", "envelopes", "Whisper id."),
        _c("workspace", "string", "envelopes", "Workspace id."),
        _c("task_id", "string", "envelopes", "Task it concerns."),
        _c("asker", "string", "envelopes", "Who asked."),
        _c("question", "string", "envelopes", "What was asked."),
        _c("asked_at", "datetime64[ns, UTC]", "envelopes", "When."),
        _c("deadline_ms", "Int64", "envelopes", "How long it was given."),
        _c("answered_at", "datetime64[ns, UTC]", "replay", "When answered. Null while unanswered."),
        _c("answered_by", "string", "replay", "Who answered."),
        _c("answer", "string", "replay", "The answer, or the chosen option id. Free text is content, so a redactor removes it; a chosen option id is metadata and stays."),
        _c("answered", "boolean", "derived", "Whether an answer was recorded."),
        _c("state", "string", "replay", "pending, answered or lapsed, as the coordinator holds it."),
        _c("lapsed", "boolean", "derived", "Whether the deadline had passed by the time an answer arrived, or the coordinator announced the lapse and applied default_if_lapsed. A whisper still awaiting an answer is pending: the coordinator declares a lapse when its check runs, and until then the question is open. Can be true while state is 'answered', for an answer that arrived after the deadline and before any lapse check."),
        _c("response_s", "float64", "derived", "Seconds to answer. Null while unanswered."),
        _c("had_options", "boolean", "derived", "Whether it was multiple choice."),
        _c("id_certain", "boolean", "derived", "Whether this row's id is paired with the asking envelope beyond doubt. See tasks.id_certain."),
    ),
)

ROUTING = Table(
    name="routing",
    grain="one row per routing decision",
    doc=(
        "What the routing policy chose and why. Recording the alternatives it "
        "passed over is what makes a routing policy auditable."
    ),
    columns=(
        _c("workspace", "string", "envelopes", "Workspace id."),
        _c("seq", "Int64", "envelopes", "Position of the envelope."),
        _c("task_id", "string", "envelopes", "Task routed."),
        _c("method", "string", "envelopes", "task.route, review.depth or escalate.auto."),
        _c("ts", "datetime64[ns, UTC]", "envelopes", "When."),
        _c("candidates", "list", "envelopes", "Candidates offered to the policy, for task.route."),
        _c("n_candidates", "Int64", "derived", "How many candidates were offered."),
        _c("selected", "string", "state", "Assignee chosen, for task.route. The coordinator returns this in the result, so it is available with server state."),
        _c("depth", "string", "state", "skip, spot_check or full, for review.depth."),
        _c("escalated", "boolean", "state", "Whether escalate.auto decided to escalate."),
        _c("policy_id", "string", "state", "Which policy produced the answer."),
        _c("n_alternatives", "Int64", "state", "How many candidates were passed over."),
    ),
)

HANDOFFS = Table(
    name="handoffs",
    grain="one row per proposed handoff",
    doc=(
        "Work passed between participants. The column worth looking at is "
        "``resolution``: a high decline rate says the proposer is misreading "
        "who should take the work, and this is the table that shows it."
    ),
    columns=(
        _c("handoff_id", "string", "envelopes", "Handoff id."),
        _c("workspace", "string", "envelopes", "Workspace id."),
        _c("seq", "Int64", "envelopes", "Position of the proposing envelope."),
        _c("proposer", "string", "envelopes", "Who offered the work."),
        _c("recipient", "string", "envelopes", "Who it was offered to, a participant or a group URI."),
        _c("task_ids", "list", "envelopes", "Tasks in the proposal."),
        _c("n_tasks", "Int64", "derived", "How many tasks were offered."),
        _c("proposed_at", "datetime64[ns, UTC]", "envelopes", "When."),
        _c("resolved_at", "datetime64[ns, UTC]", "replay", "When accepted or declined. Null while outstanding."),
        _c("resolution", "string", "derived", "accepted, declined, or open. A handoff offered to a group stays open when one member declines, because the rest may still take it; the named recipient of a direct offer is the one who can decline it outright."),
        _c("resolved_by", "string", "replay", "Who accepted or declined it."),
        _c("n_accepted", "Int64", "derived", "Tasks actually taken on. Fewer than n_tasks where the recipient accepted a subset."),
        _c("reason", "string", "envelopes", "Why it was declined, where it was. Present on a declined offer that is still open to the rest of a group."),
        _c("response_s", "float64", "derived", "Seconds from proposal to resolution."),
        _c("id_certain", "boolean", "derived", "Whether this row's id is paired with the proposing envelope beyond doubt. See tasks.id_certain."),
    ),
)

TABLES: tuple[Table, ...] = (
    EVENTS, TASKS, DECISIONS, OVERRIDES, PATCH_OPS,
    PARTICIPANTS, DELIBERATIONS, VOTES, WHISPERS, HANDOFFS, ROUTING,
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
