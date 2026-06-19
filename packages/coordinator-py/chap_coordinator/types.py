"""
chap_coordinator.types

Dataclass types for the Coordinator's in-memory state. These mirror
the wire shapes defined in the schemas under ``schemas/`` and the
profile specs under ``profiles/``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ParticipantType = Literal["human", "agent", "service", "group", "workspace"]
TaskState = Literal[
    "created",
    "in_progress",
    "review_requested",
    "completed",
    "declined",
    "abstained",
    "escalated",
    "superseded",
    "paused",
    "cancelled",
]
Mode = Literal["shadow", "trial", "production"]
WorkspaceState = Literal["active", "paused", "cancelled"]
PauseScope = Literal["task", "participant", "workspace"]


# ============================================================
#   Key history (security-signed/1.0)
# ============================================================

@dataclass
class KeyRecord:
    """A participant's JWK with its validity window.

    Per ``security-signed/1.0``, key lookup is by (uri, kid, ts) so
    historical envelopes verify against the key that was valid then.
    """

    jwk: dict
    kid: str
    valid_from: str  # ISO timestamp; inclusive
    valid_until: str | None = None  # ISO timestamp; exclusive; None = unbounded
    revoked_at: str | None = None
    revoked_reason: str | None = None

    def covers(self, ts: str) -> bool:
        if self.revoked_at is not None and ts >= self.revoked_at:
            return False
        if ts < self.valid_from:
            return False
        if self.valid_until is not None and ts >= self.valid_until:
            return False
        return True

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "jwk": self.jwk,
            "kid": self.kid,
            "valid_from": self.valid_from,
        }
        if self.valid_until is not None:
            out["valid_until"] = self.valid_until
        if self.revoked_at is not None:
            out["revoked_at"] = self.revoked_at
            out["revoked_reason"] = self.revoked_reason
        return out


# ============================================================
#   Participants
# ============================================================

@dataclass
class Member:
    """A participant inside a workspace."""

    uri: str
    type: ParticipantType
    role: str
    joined: str
    display_name: str | None = None
    capabilities: dict[str, Any] | None = None
    scopes: list[str] | None = None
    keys: list[KeyRecord] = field(default_factory=list)
    paused: bool = False  # control.pause scope=participant
    # OIDC binding (set when identity-oidc/1.0 is in use)
    oidc_sub: str | None = None
    oidc_auth_time: int | None = None
    # VC binding (set when identity-vc/1.0 is in use)
    vc_holder: str | None = None  # e.g. did:example:alice

    def key_for(self, kid: str, ts: str) -> KeyRecord | None:
        for k in self.keys:
            if k.kid == kid and k.covers(ts):
                return k
        return None

    def active_key(self, ts: str) -> KeyRecord | None:
        candidates = [k for k in self.keys if k.covers(ts)]
        if not candidates:
            return None
        candidates.sort(key=lambda k: k.valid_from, reverse=True)
        return candidates[0]

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "uri": self.uri,
            "type": self.type,
            "role": self.role,
            "joined": self.joined,
        }
        if self.display_name:
            out["display_name"] = self.display_name
        if self.capabilities:
            out["capabilities"] = self.capabilities
        if self.scopes:
            out["scopes"] = self.scopes
        if self.keys:
            out["jwks"] = {"keys": [k.jwk for k in self.keys if k.revoked_at is None
                                    and k.valid_until is None]}
            out["key_history"] = [k.to_dict() for k in self.keys]
        if self.paused:
            out["paused"] = True
        if self.oidc_sub:
            out["oidc_sub"] = self.oidc_sub
        if self.vc_holder:
            out["vc_holder"] = self.vc_holder
        return out


# ============================================================
#   Tasks and history
# ============================================================

@dataclass
class TaskHistoryEntry:
    ts: str
    from_: str
    state: str
    note: str | None = None

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"ts": self.ts, "from": self.from_, "state": self.state}
        if self.note:
            out["note"] = self.note
        return out


@dataclass
class ReviewState:
    """Per-task review state once review.request has been issued."""

    requested_at: str
    requested_to: list[str]
    rule: str = "any_one_approves"
    deadline: str | None = None
    decisions: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "requested_at": self.requested_at,
            "requested_to": self.requested_to,
            "rule": self.rule,
            "decisions": self.decisions,
        }
        if self.deadline:
            out["deadline"] = self.deadline
        return out


@dataclass
class Task:
    id: str
    kind: str
    state: TaskState
    assignee: str
    delegator: str
    input: dict[str, Any]
    created_at: str
    updated_at: str
    deadline: str | None = None
    mode: Mode = "trial"
    routing_hints: dict[str, Any] | None = None
    review: ReviewState | None = None
    review_required: bool | None = None  # set by modes/1.0 (trial forces True)
    output: Any = None
    confidence: float | None = None
    supersedes: str | None = None
    superseded_by: str | None = None
    parent: str | None = None
    paused: bool = False
    history: list[TaskHistoryEntry] = field(default_factory=list)
    pending_artefact: Any = None  # transient: stored for override base

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "state": self.state,
            "assignee": self.assignee,
            "delegator": self.delegator,
            "input": self.input,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "mode": self.mode,
            "history": [h.to_dict() for h in self.history],
        }
        if self.deadline:
            out["deadline"] = self.deadline
        if self.routing_hints:
            out["routing_hints"] = self.routing_hints
        if self.review:
            out["review"] = self.review.to_dict()
        if self.review_required is not None:
            out["review_required"] = self.review_required
        if self.output is not None:
            out["output"] = self.output
        if self.confidence is not None:
            out["confidence"] = self.confidence
        if self.supersedes:
            out["supersedes"] = self.supersedes
        if self.superseded_by:
            out["superseded_by"] = self.superseded_by
        if self.parent:
            out["parent"] = self.parent
        return out


# ============================================================
#   Artefacts
# ============================================================

@dataclass
class OverrideArtefact:
    """The artefact produced by ``decide.override`` (review/1.0)."""

    id: str
    task_id: str
    reviewer: str
    based_on_artefact: Any
    diff: list[dict]
    result: Any
    rationale: str
    tags: list[str]
    policy_refs: list[str]
    ts: str
    logical_id: str | None = None
    instance_id: str | None = None
    intent_preserved: bool | None = None

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "id": self.id,
            "kind": "override",
            "task_id": self.task_id,
            "reviewer": self.reviewer,
            "based_on_artefact": self.based_on_artefact,
            "diff": self.diff,
            "result": self.result,
            "rationale": self.rationale,
            "tags": self.tags,
            "policy_refs": self.policy_refs,
            "ts": self.ts,
        }
        if self.logical_id is not None:
            out["logical_id"] = self.logical_id
        if self.instance_id is not None:
            out["instance_id"] = self.instance_id
        if self.intent_preserved is not None:
            out["intent_preserved"] = self.intent_preserved
        return out


@dataclass
class RouteDecisionArtefact:
    """The artefact recorded by each routing/1.0 decision.

    Per profiles/routing.md S6, this is the standard artefact kind
    captured by task.route, review.depth, and escalate.auto.
    """

    id: str
    decision_type: Literal["task.route", "review.depth", "escalate.auto"]
    outcome: Any
    produced_by: str
    produced_at: str
    task: str | None = None
    policy_id: str | None = None
    hints_observed: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        content: dict[str, Any] = {
            "decision_type": self.decision_type,
            "outcome": self.outcome,
            "hints_observed": self.hints_observed,
            "rationale": self.rationale,
        }
        if self.policy_id:
            content["policy_id"] = self.policy_id
        content.update(self.extra)
        out: dict[str, Any] = {
            "id": self.id,
            "kind": "route_decision",
            "produced_by": self.produced_by,
            "produced_at": self.produced_at,
            "content": content,
        }
        if self.task:
            out["task"] = self.task
        return out


@dataclass
class SnapshotArtefact:
    """A control/1.0 snapshot, represented as an artefact (kind=snapshot)."""

    id: str  # art_... per profile spec
    ts: str
    by: str
    workspace: str
    audit_seq: int  # snapshot covers entries [0, audit_seq)
    label: str | None = None
    include: list[str] = field(default_factory=list)
    # The serialised slice of state covered by this snapshot
    state: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "id": self.id,
            "kind": "snapshot",
            "produced_by": self.by,
            "produced_at": self.ts,
            "content": {
                "workspace": self.workspace,
                "audit_seq": self.audit_seq,
                "include": self.include,
                "state": self.state,
            },
        }
        if self.label:
            out["content"]["label"] = self.label
        return out


# ============================================================
#   Whisper (whisper/1.0)
# ============================================================

@dataclass
class WhisperPrompt:
    """A typed clarifying question (whisper/1.0)."""

    id: str
    task_id: str
    asker: str
    askee: list[str]
    question: str
    options: list[dict] | None
    asked_at: str
    deadline_ms: int
    default_if_lapsed: Any
    urgency: str = "low"
    # Outcome fields
    state: Literal["pending", "answered", "lapsed"] = "pending"
    answered_at: str | None = None
    answered_by: str | None = None
    answer_option: str | None = None
    answer_text: str | None = None
    comment: str | None = None
    default_applied: Any = None  # populated when state == "lapsed"

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "id": self.id,
            "task_id": self.task_id,
            "asker": self.asker,
            "askee": self.askee,
            "question": self.question,
            "asked_at": self.asked_at,
            "deadline_ms": self.deadline_ms,
            "default_if_lapsed": self.default_if_lapsed,
            "urgency": self.urgency,
            "state": self.state,
        }
        if self.options is not None:
            out["options"] = self.options
        if self.state == "answered":
            out["answered_at"] = self.answered_at
            out["answered_by"] = self.answered_by
            if self.answer_option is not None:
                out["answer_option"] = self.answer_option
            if self.answer_text is not None:
                out["answer_text"] = self.answer_text
            if self.comment:
                out["comment"] = self.comment
        if self.state == "lapsed":
            out["default_applied"] = self.default_applied
        return out


# ============================================================
#   Deliberation (deliberation/1.0)
# ============================================================

@dataclass
class Deliberation:
    """A multi-party deliberation (deliberation/1.0)."""

    id: str
    task_id: str | None
    opener: str
    participants: list[str]  # the 'to' list at open time
    rule: str  # raw rule string, e.g. "weighted_vote_with_veto:2.0"
    question: str | None = None
    weights: dict[str, float] | None = None
    veto: dict[str, bool] | None = None
    deadline: str | None = None
    state: Literal["open", "closed", "lapsed"] = "open"
    comments: list[dict] = field(default_factory=list)
    votes: list[dict] = field(default_factory=list)
    outcome: dict | None = None
    opened_at: str = ""

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "id": self.id,
            "opener": self.opener,
            "participants": self.participants,
            "rule": self.rule,
            "state": self.state,
            "comments": self.comments,
            "votes": self.votes,
        }
        if self.task_id:
            out["task_id"] = self.task_id
        if self.question:
            out["question"] = self.question
        if self.weights:
            out["weights"] = self.weights
        if self.veto:
            out["veto"] = self.veto
        if self.deadline:
            out["deadline"] = self.deadline
        if self.outcome is not None:
            out["outcome"] = self.outcome
        if self.opened_at:
            out["opened_at"] = self.opened_at
        return out


# ============================================================
#   Handoff (handoff/1.0)
# ============================================================

@dataclass
class HandoffTask:
    task_id: str
    title: str | None = None
    status_summary: str | None = None
    next_action: str | None = None
    blockers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"task_id": self.task_id}
        if self.title:
            out["title"] = self.title
        if self.status_summary:
            out["status_summary"] = self.status_summary
        if self.next_action:
            out["next_action"] = self.next_action
        if self.blockers:
            out["blockers"] = self.blockers
        return out


@dataclass
class Handoff:
    """A handoff (handoff/1.0). May carry multiple tasks; the
    recipient may be a single participant or a group."""

    id: str
    proposer: str
    recipient: str  # single URI or group:...
    proposed_at: str
    tasks: list[HandoffTask] = field(default_factory=list)
    summary: str | None = None
    context_links: list[str] = field(default_factory=list)
    state: Literal["proposed", "accepted", "declined"] = "proposed"
    resolved_at: str | None = None
    accepted_by: str | None = None
    accepted_task_ids: list[str] = field(default_factory=list)
    accept_comment: str | None = None
    decline_reason: str | None = None
    decline_suggested_target: str | None = None

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "id": self.id,
            "proposer": self.proposer,
            "recipient": self.recipient,
            "proposed_at": self.proposed_at,
            "tasks": [t.to_dict() for t in self.tasks],
            "state": self.state,
        }
        if self.summary:
            out["summary"] = self.summary
        if self.context_links:
            out["context_links"] = self.context_links
        if self.resolved_at:
            out["resolved_at"] = self.resolved_at
        if self.accepted_by:
            out["accepted_by"] = self.accepted_by
            out["accepted_task_ids"] = self.accepted_task_ids
            if self.accept_comment:
                out["accept_comment"] = self.accept_comment
        if self.decline_reason:
            out["decline_reason"] = self.decline_reason
            if self.decline_suggested_target:
                out["decline_suggested_target"] = self.decline_suggested_target
        return out


# ============================================================
#   Audit
# ============================================================

@dataclass
class AuditEntry:
    """One row in the audit log."""

    seq: int
    arrived: str
    envelope: dict
    prev_hash: str | None = None  # set when chain linkage is enabled

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "seq": self.seq,
            "arrived": self.arrived,
            "envelope": self.envelope,
        }
        if self.prev_hash is not None:
            out["prev_hash"] = self.prev_hash
        return out


# ============================================================
#   Workspace
# ============================================================

@dataclass
class Workspace:
    """The Coordinator's in-memory record of a workspace."""

    id: str
    created: str
    state: WorkspaceState
    profiles: list[str]
    mode: Mode = "trial"
    mode_ceiling: Mode = "production"
    routing_policy_uri: str | None = None
    step_up_window_sec: int = 300  # identity-oidc/1.0 step-up window
    members: dict[str, Member] = field(default_factory=dict)
    tasks: dict[str, Task] = field(default_factory=dict)
    overrides: dict[str, OverrideArtefact] = field(default_factory=dict)
    whispers: dict[str, WhisperPrompt] = field(default_factory=dict)
    deliberations: dict[str, Deliberation] = field(default_factory=dict)
    handoffs: dict[str, Handoff] = field(default_factory=dict)
    snapshots: dict[str, SnapshotArtefact] = field(default_factory=dict)
    route_decisions: dict[str, RouteDecisionArtefact] = field(default_factory=dict)
    audit: list[AuditEntry] = field(default_factory=list)
    # Chain state (audit-scitt/1.0 supplementary chain linkage)
    chain_head: str | None = None
    chain_enabled: bool = False

    def has_profile(self, profile_id: str) -> bool:
        # Match either exact or by prefix (e.g. "review/" matches "review/1.0")
        return any(p == profile_id or p.startswith(profile_id + "/")
                   for p in self.profiles)
