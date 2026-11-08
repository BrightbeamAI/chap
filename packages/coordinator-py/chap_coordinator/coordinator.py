"""
chap_coordinator.coordinator

The Coordinator class. CHAP protocol logic, packaged as a library
rather than a CLI server. Applications instantiate one, call
``dispatch(envelope)``, subscribe to audit events, and persist via
the provided hooks.

Coverage:
  - Core (9 methods)
  - review/1.0 (6 methods)
  - whisper/1.0 (2 methods + lapse handling)
  - deliberation/1.0 (4 methods)
  - modes/1.0 (mode handling at task.create; trial mode forces review)
  - handoff/1.0 (3 methods with multi-task and group support)
  - control/1.0 (7 methods with task/participant/workspace scopes)
  - routing/1.0 (3 methods producing route_decision artefacts)
  - security-signed/1.0 (top-level `sig` field, key history with valid_from/until)
  - audit-scitt/1.0 (statement assembly + chain linkage)
  - identity-oidc/1.0 (cnf.jwk pinning, step-up checks)
  - identity-vc/1.0 (holder-binding extraction)
"""
from __future__ import annotations

import copy
import dataclasses
import datetime as _dt
import logging
import math
import re
import sys
import threading
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from .audit import (
    entry_call, entry_record, is_signed, link_hash, refusal_is_recorded, signed_digest,
)
from .canonical import ZERO_HASH, canonicalize, content_hash, sha256_hex
from .crypto import (
    CRYPTOGRAPHY_MISSING, is_ed25519_key, is_webauthn_key, jwk_thumbprint,
    parse_webauthn_assertion, strict_base64, webauthn_failure,
)
from .ids import IdFactory
from .storage.store import StoreChange, StoreConflictError, WorkspaceRecord
from .tracked import Scope, TrackedDict
from .catalogue import ALWAYS_AVAILABLE, OWNING_PROFILE, PROFILE_VERSIONS
from .jsonrpc import E, is_valid_envelope, json_text, make_response, rpc_error
from .params import lookup, params_error
from .patch import PatchError, apply_json_patch
from .types import (
    AuditEntry,
    Deliberation,
    Handoff,
    HandoffTask,
    Invitation,
    KeyRecord,
    Member,
    OverrideArtefact,
    ReviewState,
    RouteDecisionArtefact,
    SnapshotArtefact,
    Task,
    TaskHistoryEntry,
    WhisperPrompt,
    Workspace,
)

# ============================================================
#   Options
# ============================================================

AuditListener = Callable[[str, AuditEntry], None]
TokenVerifier = Callable[[str], dict | None]
CredentialVerifier = Callable[[dict], dict | None]
ScittSubmitter = Callable[[dict], dict | None]  # SCITT statement -> receipt or None
ScittReceiptVerifier = Callable[[dict], bool]  # receipt -> verified?


# Methods classified as privileged for step-up auth (identity-oidc/1.0 S4
# and control/1.0 S5). Compared against an OIDC auth_time freshness window.
PRIVILEGED_METHODS = frozenset({
    "control.pause", "control.resume", "control.cancel", "control.supersede",
    "control.snapshot", "control.rollback", "control.set_mode_ceiling",
    "workspace.set_profiles", "workspace.invite",
    "participant.rotate_key", "participant.revoke_key",
})


# Methods outside the control, deliberate, handoff and whisper namespaces that
# dispatch holds to the membership floor before the handler runs.
_MEMBER_ONLY_METHODS = frozenset({
    "task.route", "review.depth", "escalate.auto", "participant.leave",
})

# Read-only methods return workspace state but do not mutate it, so they are not
# recorded in the audit chain -- recording a read would grow and re-link it.
_READ_ONLY_METHODS = frozenset({
    "workspace.describe", "audit.read",
    "audit.verify_chain", "audit.verify_receipt",
    # Submitting the chain to a transparency service reads it and sends it
    # onward. Recording the submission would append to the very log being
    # submitted, so the receipt would attest a chain one entry shorter.
    "audit.submit_to_scitt",
})


# How deeply a request may nest, counting the envelope as level one. The
# reference HTTP servers refuse deeper bodies before dispatch; the same limit
# here means an in-process caller cannot send what the canonicaliser, the copy
# of a recorded call and the two references handle differently.
_MAX_NESTING = 64

#: The largest integer both coordinators read the same, 2**53 - 1.
_MAX_SAFE_INTEGER = 2**53 - 1


def _within_nesting(value: Any, limit: int) -> bool:
    stack = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > limit:
            return False
        if isinstance(node, list):
            stack.extend((v, depth + 1) for v in node)
        elif isinstance(node, dict):
            stack.extend((v, depth + 1) for v in node.values())
    return True


def _index_signed(index: dict, entry: AuditEntry) -> None:
    """Add one entry to a workspace's index of signed calls, if it is signed."""
    call = entry_call(entry)
    if not is_signed(call):
        return
    try:
        digest = signed_digest(call)
    except (ValueError, TypeError, RecursionError):
        return  # an altered entry; audit.verify_chain reports it
    if entry.outcome is not None:
        code = entry.outcome.get("code") if isinstance(entry.outcome, dict) else None
        index["refused"][digest] = (entry.seq, code)
    else:
        # A log written before signed copies were refused can hold one call
        # twice; the first is where it took effect.
        index["accepted"].setdefault(digest, entry.seq)


# Cap on a workspace's task.create idempotency map. Older keys are evicted, so
# the dedup window is bounded rather than growing unbounded in a long-lived
# workspace; a redelivery beyond this many intervening creates is not deduped.
_MAX_IDEMPOTENCY_KEYS = 10_000


# States in which a task has been deliberately stopped. review.request and
# task.complete both refuse these, so neither can revive a terminated task nor
# bypass a pause.
_REVIEW_REQUEST_STOPPED = ("cancelled", "superseded", "paused")


@dataclass
class CoordinatorOptions:
    """Options controlling Coordinator behaviour."""

    deterministic_ids: bool = False
    """ULIDs derived from a deterministic counter (tests / demos)."""

    deterministic_clock: bool = False
    """Internal clock advances by a fixed step per emission."""

    enable_chain: bool = False
    """Compute prev_hash on every audit entry (audit-scitt/1.0 supplement)."""

    require_signatures: bool = False
    """Require signatures on every workspace, as advertising
    security-signed/1.0 does on one."""

    signature_policy: str = "all"
    """The signature policy of a workspace whose workspace.create names none:
    "all" verifies every call, and "humans" takes an unsigned call from an
    agent member."""

    join_policy: str = "open"
    """The join policy of a workspace whose workspace.create names none:
    "open" admits anyone, and "invite" admits only a participant an admin
    invited, and the first member."""

    enforce_step_up: bool = False
    """Reject privileged methods when OIDC auth_time is stale."""

    require_read_membership: bool = False
    """Require audit.read / workspace.describe callers to be members. Off by
    default: reads are transport-delegated. Enable for multi-tenant or
    directly-exposed deployments."""

    max_envelope_bytes: int = 1_048_576
    """Largest envelope accepted, published in the workspace descriptor
    (SPEC S4.4). Envelopes whose canonical form exceeds this are rejected."""

    on_audit: AuditListener | None = None
    """Called after every successfully recorded audit entry."""

    on_auto_escalate: Callable[[Task, str], None] | None = None
    """Called when the routing policy auto-escalates a task."""

    verify_oidc_token: TokenVerifier | None = None
    """Hook for identity-oidc/1.0; called with a bearer token; returns claims."""

    verify_vc: CredentialVerifier | None = None
    """Hook for identity-vc/1.0; called with a VP; returns subject claims."""

    scitt_submitter: ScittSubmitter | None = None
    """Hook for audit-scitt/1.0; called with a SCITT signed statement;
    returns the receipt (opaque to CHAP) or None on failure."""

    verify_scitt_receipt: ScittReceiptVerifier | None = None
    """Hook for audit-scitt/1.0; called with a receipt; returns whether it
    verifies. When unset, audit.verify_receipt fails closed."""

    routing_policy: Callable[[Task, list[str]], dict] | None = None
    """Hook for routing/1.0 task.route; returns {selected, rationale...}."""

    review_depth_policy: Callable[[Task, dict], dict] | None = None
    """Hook for routing/1.0 review.depth; returns {depth, rationale...}."""

    escalation_policy: Callable[[Task, dict], dict] | None = None
    """Hook for routing/1.0 escalate.auto; returns {escalate, to, ...}."""

    default_profiles: list[str] = field(
        default_factory=lambda: ["core/1.0", "review/1.0"]
    )

    store: Any = None
    """Optional persistence store (see chap_coordinator.storage).
    Default is in-memory (no persistence). Pass a `SqliteStore` or any
    object satisfying the `Store` Protocol to persist workspaces. After each
    call that adds to a workspace's log the Coordinator calls `apply` with
    what changed, or `save` with the whole workspace when the store has no
    `apply`."""

    on_internal_error: Callable[[Exception, dict], None] | None = None
    """Called when a handler raises, with the exception and
    ``{"method": method, "workspace": workspace}``. The caller is answered
    with -32603 and no detail. When unset, the traceback is written to
    stderr."""

    on_store_error: Callable[[Exception, dict], None] | None = None
    """Called when a write to the store fails, with the error and
    ``{"id": workspace, "version": version}``. When unset, the failure is
    logged as an error on the ``chap_coordinator`` logger. What happens to
    the call is ``store_errors``'s to say."""

    store_errors: str = "raise"
    """What a failed write does to the call that made it. ``"raise"``: the
    call is answered with -32603 and ``data: {"reason": "store"}``, and the
    workspace goes back to what the store holds. ``"log"``: the call is
    answered as it was, and the next write carries its change again. A write
    another writer got to first is answered as ``"raise"`` says under either,
    and the call may be sent again."""


# ============================================================
#   Helpers
# ============================================================

def _now_iso(clock_ms: int | None = None) -> str:
    if clock_ms is None:
        dt = _dt.datetime.now(_dt.timezone.utc)
    else:
        dt = _dt.datetime.fromtimestamp(clock_ms / 1000, tz=_dt.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


_log = logging.getLogger("chap_coordinator")

#: The Workspace collections kept object by object, idempotency keys included.
_TRACKED = ("members", "tasks", "overrides", "whispers", "deliberations",
            "handoffs", "snapshots", "route_decisions", "invitations",
            "idempotency_keys")

#: Workspace fields a store keeps as the workspace's own: all but these.
_NOT_META = frozenset((*_TRACKED, "audit"))


@dataclass
class _Persisted:
    """What the store holds of one workspace, as far as a Coordinator knows."""

    seq: int = 0
    """Audit entries the store holds."""
    replace: bool = False
    """Write the whole workspace next: the store's copy is unknown."""
    updated_at: str | None = None
    """The time to stamp on the next write."""


def _plain(value: Any) -> Any:
    """An object as a store keeps it: dataclasses as dicts, copied."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    return copy.deepcopy(value)


def _store_failed() -> dict:
    """The answer to a call whose workspace the store could not be brought in
    line with."""
    return rpc_error(E.INTERNAL, "Internal error", data={"reason": "store"})


def _target_of(envelope: Any) -> str | None:
    """The workspace a call names, where it names one."""
    params = envelope.get("params") if isinstance(envelope, dict) else None
    ws_id = params.get("workspace") if isinstance(params, dict) else None
    return ws_id if isinstance(ws_id, str) and ws_id else None


def _missing(params: dict, fields: list[str]) -> str | None:
    for f in fields:
        if f not in params:
            return f
    return None


def _rehydrate_workspace(data: dict) -> "Workspace":
    """Reconstruct a Workspace dataclass tree from a snapshot dict.

    Mirror of `_snapshot_workspace`; the two should round-trip cleanly.
    """
    from .types import (
        Workspace, Member, Task, TaskHistoryEntry, KeyRecord, ReviewState,
        OverrideArtefact, WhisperPrompt, Deliberation, Handoff, HandoffTask,
        SnapshotArtefact, RouteDecisionArtefact, AuditEntry, Invitation,
    )

    def _opt(cls, d):
        return cls(**d) if d is not None else None

    # The TypeScript coordinator leaves out a field it has no value for,
    # where this one writes None, so a field with no default that is missing
    # reads as None.
    def _build(cls, d: dict):
        absent = {f.name: None for f in dataclasses.fields(cls)
                  if f.default is dataclasses.MISSING
                  and f.default_factory is dataclasses.MISSING}
        return cls(**{**absent, **d})

    # The TypeScript coordinator names a history entry's sender ``from``, and
    # keeps a route decision's fields of its kind at the top beside its
    # ``kind``, where this one keeps them in ``extra``. A store either wrote
    # reads the same here.
    def _history_entry(h: dict) -> TaskHistoryEntry:
        h = dict(h)
        if "from" in h:
            h["from_"] = h.pop("from")
        return _build(TaskHistoryEntry, h)

    route_fields = {f.name for f in dataclasses.fields(RouteDecisionArtefact)}

    def _route_decision(v: dict) -> RouteDecisionArtefact:
        v = {k: x for k, x in v.items() if k != "kind"}
        extra = dict(v.pop("extra", None) or {})
        extra.update({k: v.pop(k) for k in [k for k in v if k not in route_fields]})
        return RouteDecisionArtefact(**v, extra=extra)

    members = {
        k: _build(Member, v) for k, v in (data.get("members") or {}).items()
    }
    for m in members.values():
        # A member written before its type was checked against its URI takes
        # the type the scheme names, and holds the admin role only where that
        # type may (SPECIFICATION 5.1, 6.3).
        scheme = m.uri.split(":", 1)[0] if isinstance(m.uri, str) else ""
        if scheme in _PARTICIPANT_TYPES:
            m.type = scheme
        if m.role == "admin" and m.type not in _ADMIN_TYPES:
            m.role = "participant"
        if isinstance(m.keys, list):
            m.keys = [_build(KeyRecord, k) if isinstance(k, dict) else k for k in m.keys]
        elif isinstance(m.keys, dict):
            m.keys = {k: _build(KeyRecord, v) if isinstance(v, dict) else v
                      for k, v in m.keys.items()}

    tasks = {}
    for k, v in (data.get("tasks") or {}).items():
        v = dict(v)
        v["history"] = [_history_entry(h) for h in v.get("history", [])]
        if isinstance(v.get("review"), dict):
            review = dict(v["review"])
            # A review written before authors were recorded takes them from
            # the task's history.
            if not isinstance(review.get("authors"), list):
                review["authors"] = _stored_authors(v.get("assignee"), v["history"],
                                                    v.get("prior_authors"))
            # and one written before acknowledgements were kept has none.
            if not isinstance(review.get("acknowledgements"), list):
                review["acknowledgements"] = []
            v["review"] = _build(ReviewState, review)
        tasks[k] = _build(Task, v)

    whispers = {
        k: _build(WhisperPrompt, v) for k, v in (data.get("whispers") or {}).items()
    }
    deliberations = {
        k: _build(Deliberation, v)
        for k, v in (data.get("deliberations") or {}).items()
    }
    handoffs = {}
    for k, v in (data.get("handoffs") or {}).items():
        v = dict(v)
        v["tasks"] = [_build(HandoffTask, t) for t in v.get("tasks", [])]
        handoffs[k] = _build(Handoff, v)

    snapshots = {
        k: SnapshotArtefact.from_dict(v) if isinstance(v, dict) else v
        for k, v in (data.get("snapshots") or {}).items()
    }

    # Every typed collection on Workspace is persisted by asdict, so every one
    # has to come back typed. An untyped collection reads as a plain dict and
    # diverges from the TypeScript restore, which rebuilds all of them.
    overrides = {
        k: _build(OverrideArtefact, v) if isinstance(v, dict) else v
        for k, v in (data.get("overrides") or {}).items()
    }
    route_decisions = {
        k: _route_decision(v) if isinstance(v, dict) else v
        for k, v in (data.get("route_decisions") or {}).items()
    }
    invitations = {
        k: _build(Invitation, v) for k, v in (data.get("invitations") or {}).items()
    }

    audit = [AuditEntry(**a) for a in (data.get("audit") or [])]

    ws_kwargs = {
        k: v for k, v in data.items()
        if k not in {"members", "tasks", "overrides", "whispers",
                     "deliberations", "handoffs", "snapshots",
                     "route_decisions", "invitations", "audit"}
    }
    ws = Workspace(**ws_kwargs)
    # A workspace written before the policy existed verified every call, and
    # one written before the join policy existed admitted anyone.
    if ws.signature_policy != "humans":
        ws.signature_policy = "all"
    if ws.join_policy != "invite":
        ws.join_policy = "open"
    if not isinstance(ws.review_rule, str) or not _review_rule_supported(ws.review_rule):
        ws.review_rule = None
    ws.members = members
    ws.tasks = tasks
    ws.overrides = overrides
    ws.whispers = whispers
    ws.deliberations = deliberations
    ws.handoffs = handoffs
    ws.snapshots = snapshots
    ws.route_decisions = route_decisions
    ws.invitations = invitations
    ws.audit = audit
    return ws


def _review_required(ws: Workspace, task: Task) -> bool:
    """Whether a task's work must be decided by a person: it carries
    review_required, or it runs in trial on a workspace advertising
    modes/1.0."""
    return task.review_required is True or (task.mode == "trial" and ws.has_profile("modes"))


def _raw_member(ws: Workspace, uri: Any) -> Member | None:
    """A member, read without recording it as changed."""
    return dict.get(ws.members, uri) if isinstance(uri, str) else None


#: Names a task.create idempotency key cannot take (SPECIFICATION 12.3).
_RESERVED_KEYS = ("__proto__", "constructor", "prototype")

#: The participant types SPECIFICATION 5.1 names, each the scheme of its URIs.
_PARTICIPANT_TYPES = ("human", "agent", "service", "group", "workspace")

#: The types that may hold the admin role (SPECIFICATION 6.3).
_ADMIN_TYPES = ("human", "service")

#: A profile id: ``<name>/<major>.<minor>`` in ASCII digits.
_PROFILE_ID = re.compile(r"([a-z][a-z0-9-]*)/(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")

#: Profiles that cannot be removed once advertised: each states a guarantee
#: about every later call.
_LASTING_PROFILES = ("security-signed", "identity-oidc", "identity-vc")


def _known_profile(profile_id: str) -> bool:
    """Whether a profile id names a profile the catalogue lists, at its major
    version (SPECIFICATION 6.5). A later minor version is the same profile
    (SPECIFICATION 15.4)."""
    m = _PROFILE_ID.fullmatch(profile_id)
    if m is None or m.group(1) not in PROFILE_VERSIONS:
        return False
    return PROFILE_VERSIONS[m.group(1)].split(".")[0] == m.group(2)


def _profiles_error(profiles: list[str]) -> dict | None:
    """The refusal of a profile list naming a profile the catalogue does not
    list, or None."""
    unknown = next((x for x in profiles if not _known_profile(x)), None)
    if unknown is None:
        return None
    return {"error": rpc_error(
        E.PARAMS, f"Unknown profile {json_text(unknown)}: a profile id is "
        "<name>/<major>.<minor> for a profile the catalogue lists")}


def _participant_error(kind: str, uri: str, field: str) -> dict | None:
    """Why ``uri`` cannot name a participant of this type, or None: the type
    is one of the five, and the URI's scheme names it (SPECIFICATION 5.1).
    ``field`` is the param that holds the URI."""
    if kind not in _PARTICIPANT_TYPES:
        return {"error": rpc_error(
            E.PARAMS, "type must be human, agent, service, group or workspace")}
    if not uri.startswith(f"{kind}:"):
        return {"error": rpc_error(E.PARAMS, f'{field} must start with "{kind}:" for type {kind}')}
    # A group: or workspace: URI addresses everyone it covers, as a review's
    # `to` does, so it names no one who could join and act.
    if kind in ("group", "workspace"):
        return {"error": rpc_error(
            E.PARAMS, "group: and workspace: URIs name address scopes, which cannot join")}
    return None


def _in_force(key: KeyRecord, now: str) -> bool:
    """Whether a key may sign now: neither revoked nor past its validity."""
    return (not (key.revoked_at is not None and now >= key.revoked_at)
            and not (key.valid_until is not None and now >= key.valid_until))


def _same_key(a: dict, b: dict) -> bool:
    """Whether two JWKs are the same key: the same kid and the same RFC 7638
    thumbprint, or, for a key with no thumbprint, the same JSON throughout."""
    def material(k: dict) -> str:
        thumbprint = jwk_thumbprint(k)
        return thumbprint if thumbprint is not None else canonicalize(k).decode("utf-8")
    return a.get("kid") == b.get("kid") and material(a) == material(b)


def _credential_keys(cnf: Any, nonce: Any, join_keys: list[dict]) -> list[dict] | None | bool:
    """The keys a credential binds for a join: its confirmation key, named by
    its kid or else by its RFC 7638 thumbprint, or, where it carries none, the
    join's keys whose thumbprint its nonce is (SPECIFICATION 5.4). Every key
    the join carries must be a bound one: None where one is not. False where
    the credential binds no key."""
    if isinstance(cnf, dict):
        kid = cnf.get("kid") if isinstance(cnf.get("kid"), str) and cnf["kid"] else jwk_thumbprint(cnf)
        if kid is None:
            return None
        key = {**cnf, "kid": kid}
        return [key] if all(_same_key(key, j) for j in join_keys) else None
    if isinstance(nonce, str):
        return join_keys if all(jwk_thumbprint(j) == nonce for j in join_keys) else None
    return False


def _raw_invitation(ws: Workspace, uri: str) -> "Invitation | None":
    """An invitation, read without recording it as changed."""
    return dict.get(ws.invitations, uri)


def _awaits_first_member(ws: Workspace) -> bool:
    """Whether nobody has joined the workspace yet: it holds no member and its
    log holds no accepted participant.join. A workspace whose members have all
    left has had its first member."""
    if ws.members:
        return False
    return not any(e.outcome is None and (entry_call(e) or {}).get("method") == "participant.join"
                   for e in ws.audit)


def _signing_keys(jwks: Any) -> list[dict]:
    """The keys of a join's ``jwks`` a signature can name: objects with a
    non-empty string kid."""
    keys = jwks.get("keys") if isinstance(jwks, dict) else None
    if not isinstance(keys, list):
        return []
    return [j for j in keys if isinstance(j, dict) and isinstance(j.get("kid"), str) and j["kid"]]


def _recorded_form(envelope: dict) -> dict:
    """A call as the log holds it.

    A participant.join keeps its credentials off the chain: an
    ``oidc_token`` is recorded as the SHA-256 of its UTF-8 bytes and a
    ``vc_presentation`` as the SHA-256 of its JCS form, so the log shows which
    credential a member joined with and never holds one a reader could
    present again.
    """
    params = envelope.get("params")
    if envelope.get("method") != "participant.join" or not isinstance(params, dict):
        return envelope
    params = dict(params)
    if isinstance(params.get("oidc_token"), str):
        params["oidc_token"] = sha256_hex(params["oidc_token"].encode("utf-8"))
    if isinstance(params.get("vc_presentation"), dict):
        params["vc_presentation"] = content_hash(params["vc_presentation"])
    return {**envelope, "params": params}


def _union_of(*lists: Any) -> list[str]:
    """The members of the lists in order, each once."""
    out: list[str] = []
    for items in lists:
        for uri in items:
            if uri not in out:
                out.append(uri)
    return out


def _authors_of(task: Task, completer: Any) -> list[str]:
    """Who made the work: the task's assignee, then whoever completed it,
    then the authors it carries from a task it replaces, once each."""
    authors = [task.assignee]
    if isinstance(completer, str) and completer != task.assignee:
        authors.append(completer)
    return _union_of(authors, task.prior_authors if isinstance(task.prior_authors, list) else [])


def _state_before(history: list, i: int) -> str | None:
    """The state a task was in before the history entry at ``i``, passing
    over pauses, which leave the work where it was."""
    for j in range(i - 1, -1, -1):
        if history[j].state != "paused":
            return history[j].state
    return None


def _work_completion(history: list, i: int) -> bool:
    """Whether the history entry at ``i`` records the work completed: the
    task entering ``completed`` from any state but review. A reviewer's
    decision completes a task out of review, and an entry a task already
    completed takes, such as a route or a handoff of it, completes
    nothing."""
    if history[i].state != "completed":
        return False
    return _state_before(history, i) not in ("review_requested", "completed")


def _last_completer(task: Task) -> str | None:
    """The sender of the task's latest completion of the work, if it has
    one."""
    for i in range(len(task.history) - 1, -1, -1):
        if _work_completion(task.history, i):
            return task.history[i].from_
    return None


#: The notes on the history entries of the reviews task.complete opened.
_OPENED_ON_COMPLETE = "review required; opened on task.complete"
_NEW_ROUND_ON_COMPLETE = "a new round of the review; opened on task.complete"


def _stored_authors(assignee: str, history: list, prior: Any = None) -> list[str]:
    """The authors of a review recorded before authors were kept, read from
    the task's history: the assignee, whoever completed the work, whether
    the completion opened the review or came before a review.request, and
    the member whose review.request opened the review, which supplied the
    artefact under review. A reviewer's decision that completed the task is
    not a completion of the work."""
    carried = prior if isinstance(prior, list) else []
    requester: Any = None

    def authors(completer: Any) -> list[str]:
        made = [assignee]
        if isinstance(completer, str) and completer != assignee:
            made.append(completer)
        return _union_of(made, carried, [requester] if isinstance(requester, str) else [])

    for i in range(len(history) - 1, -1, -1):
        entry = history[i]
        opens = entry.state == "review_requested" and _state_before(history, i) != "review_requested"
        if opens and entry.note in (_OPENED_ON_COMPLETE, _NEW_ROUND_ON_COMPLETE):
            return authors(entry.from_)
        if opens and requester is None:
            requester = entry.from_
        if _work_completion(history, i):
            return authors(entry.from_)
    return authors(None)


#: The states in which a task is over.
_ENDED = ("completed", "declined", "cancelled", "superseded", "escalated")


def _review_holds(ws: Workspace, task: Task, depth: int = 0) -> list[str]:
    """The authors a review the task requires holds, whatever state the task
    is in: those of its own review, until a reviewer rejects the round under
    review; or, for a task with no review yet, those of the task it
    replaces, since an escalation or a supersede carries that review on."""
    if depth > 64 or not _review_required(ws, task):
        return []
    if task.review is not None:
        if any(d.get("kind") == "reject" for d in task.review.decisions):
            return []
        return list(task.review.authors)
    replaced = ws.tasks.get(task.supersedes) if isinstance(task.supersedes, str) else None
    return _review_holds(ws, replaced, depth + 1) if replaced is not None else []


def _open_required_review_authors(ws: Workspace, task: Task) -> list[str]:
    """The authors a review the task requires holds to it: from the moment
    the review opens until the task is over, unless a reviewer has rejected
    the round under review, and through a successor an escalation or a
    supersede makes. They may take the work back to revise it, and hand it
    on with escalate.raise; they do not end the task, or its successor,
    around a decision. Once a reviewer rejects the work, its authors may
    give the task up. Empty where the task holds no such review."""
    return [] if task.state in _ENDED else _review_holds(ws, task)


def _held_by(task: Task, mover: Any) -> list[str]:
    """The task's assignee, as an author of its work, where it moves the
    task on itself; otherwise nobody. Whoever completed the work is an
    author by its completion. A member another member routed or escalated
    the task to made none of the work it was handed, so nobody can leave a
    person out of a review's reviewers by passing the task through them."""
    return [task.assignee] if mover == task.assignee else []


def _successor_authors(old: Task, mover: Any) -> list[str]:
    """The authors a successor carries from the task it replaces: that
    task's review authors where it held a review, its assignee where
    _held_by counts it, whoever completed the work, and the authors it
    carried itself."""
    completer = _last_completer(old)
    return _union_of(old.review.authors if old.review is not None else [],
                     _held_by(old, mover),
                     [completer] if completer is not None else [],
                     old.prior_authors if isinstance(old.prior_authors, list) else [])


def _is_scope(uri: Any) -> bool:
    """Whether a URI is an address scope, which every member it covers
    answers to."""
    return isinstance(uri, str) and (uri.startswith("workspace:") or uri.startswith("group:"))


def _review_rule_supported(rule: str) -> bool:
    """Rules review/1.0 can evaluate itself, with no external policy weights."""
    if rule in ("any_one_approves", "all_approve"):
        return True
    if rule.startswith("quorum:"):
        # ASCII digits only: str.isdigit() also takes other scripts' digits,
        # which TypeScript refuses and int() may not read.
        arg = rule.split(":", 1)[1]
        return re.fullmatch(r"[0-9]+", arg) is not None and int(arg) >= 1
    return False


def _review_satisfied(review) -> bool:
    """Whether the approvals recorded so far satisfy the review rule."""
    approvers = {d["reviewer"] for d in review.decisions
                 if d.get("kind") == "approve"}
    rule = review.rule or "any_one_approves"
    if rule == "all_approve":
        named = [r for r in review.requested_to
                 if not (r.startswith("workspace:") or r.startswith("group:"))]
        # A broadcast-only review has no bounded reviewer set to wait on, so it
        # degrades to first-approve.
        if not named:
            return len(approvers) >= 1
        return all(r in approvers for r in named)
    if rule.startswith("quorum:"):
        return len(approvers) >= int(rule.split(":", 1)[1])
    return len(approvers) >= 1  # any_one_approves (and the default)


_MODE_ORDER = {"shadow": 0, "trial": 1, "production": 2}


def mode_le(a: str, ceiling: str) -> bool:
    """True iff mode `a` is <= ceiling."""
    return _MODE_ORDER.get(a, 99) <= _MODE_ORDER.get(ceiling, 99)


# ============================================================
#   Coordinator
# ============================================================

class Coordinator:
    """The CHAP Coordinator.

    Instantiate one per process; call ``dispatch(envelope)`` for each
    incoming JSON-RPC 2.0 envelope and return its response to the
    transport layer.
    """

    def __init__(self, options: CoordinatorOptions | None = None,
                 **overrides: Any) -> None:
        # Two calling styles are supported:
        #   Coordinator(CoordinatorOptions(store=..., enable_chain=True))
        #   Coordinator(store=..., enable_chain=True)
        # The keyword form mirrors the TypeScript constructor's options
        # object and is the form the README quickstart uses. Any keyword
        # overrides are applied on top of the options object (or a fresh
        # default one).
        self.options = options or CoordinatorOptions()
        if overrides:
            import dataclasses
            self.options = dataclasses.replace(self.options, **overrides)
        self.ids = IdFactory(
            deterministic=self.options.deterministic_ids,
            start_ms=1_700_000_000_000,
        )
        self._clock_ms: int | None = (
            1_700_000_000_000 if self.options.deterministic_clock else None
        )
        self._frozen_now: str | None = None
        self.workspaces: dict[str, Workspace] = {}
        # Change tracking, for a store that implements `apply`: reads made
        # while a call is handled are recorded (see tracked.py), and each
        # call's changes are written once, when it ends.
        store = self.options.store
        self._tracking = store is not None and callable(getattr(store, "apply", None))
        self._scope = Scope()
        self._unflushed: dict[int, Workspace] = {}
        # One call at a time: a call reads and writes state across many steps.
        self._lock = threading.RLock()
        # Calls under way on the thread that holds the lock, and the entries
        # the store took that the audit listeners have yet to hear of. The
        # listeners hear of them once the outermost call has let the lock go,
        # so a listener may wait on a call from another thread.
        self._calls = 0
        self._heard: list[tuple[str, AuditEntry]] = []
        # Workspaces whose store copy may differ from memory after a failed
        # write: each is reloaded before the next call on it, which is
        # refused while that fails.
        self._held: set[str] = set()
        # How the writes of the call being handled failed, by workspace.
        self._write_failures: dict[str, str] = {}
        self._audit_listeners: list[AuditListener] = []
        if self.options.on_audit:
            self._audit_listeners.append(self.options.on_audit)

        # Method dispatch table
        self._handlers: dict[str, Callable[[dict], dict]] = {}
        self._register_core_handlers()
        self._register_profile_handlers()

        # Restore from persistent store if one is configured.
        # This must happen after handlers are registered so the
        # rehydrated state matches the same schema dispatch expects.
        self._restore_from_store()

    # -- public lifecycle ---------------------------------------------

    def _advance_clock(self) -> str:
        if self._clock_ms is not None:
            self._clock_ms += 1000
            return _now_iso(self._clock_ms)
        return _now_iso()

    def now_iso(self) -> str:
        if self._frozen_now is not None:
            return self._frozen_now
        return self._advance_clock()

    def add_audit_listener(self, fn: AuditListener) -> None:
        self._audit_listeners.append(fn)

    def get_workspace(self, workspace_id: str) -> Workspace | None:
        """Convenience: get a workspace by id, or ``None``."""
        return self.workspaces.get(workspace_id)

    # -- authorisation preconditions -----------------------------------

    @staticmethod
    def _require_member(ws: Workspace, sender: Any) -> dict | None:
        """Assert that ``sender`` is a joined member of ``ws``.

        Returns an error envelope fragment if not, else ``None``. Every
        actor-action method runs this on its ``from`` so a decision,
        completion, or review request can never be attributed to a
        participant who never joined. See SPECIFICATION.md S6.3 and the
        ``unknown_participant`` error condition in S13.3.

        (The published spec assigns this condition code -32403; the
        reference implementations use the internal NOT_AUTHORISED code
        -32011 because -32403 is already taken in their private range by
        OIDC_TOKEN_INVALID. The spec-vs-implementation error-table
        divergence is tracked separately.)
        """
        # A `from` that is not a string names nobody. It is answered in fixed
        # words, so both references refuse it alike whatever was sent, and a
        # list or object never reaches the membership lookup.
        if not isinstance(sender, str):
            return {"error": rpc_error(
                E.NOT_AUTHORISED,
                "Not a workspace member: from is not a participant URI",
            )}
        if sender not in ws.members:
            return {"error": rpc_error(
                E.NOT_AUTHORISED,
                f"Not a workspace member: {sender}",
            )}
        return None

    @staticmethod
    def _require_reviewer(ws: Workspace, task: Task, sender: str | None) -> dict | None:
        """Assert that ``sender`` may decide the task's open review.

        Applied to review-decision methods (decide.* and abstain.declare) and
        to review.acknowledge: to act on a review you must not be an author of
        the work under review, must be a human member where the task requires
        review, and must be one of the reviewers it was addressed to (the
        ``to`` set on review.request). Membership is the floor; this is the
        eligibility ceiling for decisions. Returns an error envelope fragment
        if not eligible, else ``None``.

        A broadcast-scoped reviewer (a ``workspace:`` or ``group:`` URI in
        the ``to`` set) is satisfied by any workspace member, so in that
        case the membership floor the caller already enforced is
        sufficient and no per-URI match is required. Note that the
        coordinator does not model group membership: a ``group:`` target is
        treated as "any member", not "any member of that group". Deployments
        needing true group restriction must enforce it externally. This
        mirrors the handoff profile's treatment of group recipients and
        keeps the documented ``to: workspace:<id>`` broadcast pattern
        working.
        """
        review = task.review
        if review is None:
            return None
        # A decision by whoever made the work is not a review of it, whatever
        # the reviewer set says, broadcast scopes included.
        if sender in review.authors:
            return {"error": rpc_error(
                E.NOT_AUTHORISED, "The author of the work under review cannot decide it")}
        member = _raw_member(ws, sender)
        if _review_required(ws, task) and (member is None or member.type != "human"):
            return {"error": rpc_error(
                E.NOT_AUTHORISED,
                f"This task requires a person's review: {sender} is not a human member")}
        if not review.requested_to:
            # No recorded reviewer set: fall back to the membership floor,
            # which the caller has already enforced.
            return None
        # If the review was broadcast to a workspace/group scope, any member
        # is eligible; the membership floor already passed.
        if any(isinstance(r, str) and (r.startswith("workspace:") or r.startswith("group:"))
               for r in review.requested_to):
            return None
        if sender not in review.requested_to:
            return {"error": rpc_error(
                E.NOT_AUTHORISED,
                f"Not an addressed reviewer for this task: {sender}",
            )}
        return None

    # -- public dispatch -----------------------------------------------

    def dispatch(self, envelope: dict) -> dict:
        """Process one JSON-RPC envelope; return the response."""
        return self._serially(lambda: self._dispatch_once(envelope))

    def _dispatch_once(self, envelope: Any) -> dict:
        response, failure = self._run(envelope)
        if failure is not None:
            # The store does not hold what the call did, whether a write
            # failed or another writer wrote the workspace first. The
            # workspace is what the store holds again, and the caller may send
            # the call again (SPECIFICATION 10.3).
            env_id = envelope.get("id") if isinstance(envelope, dict) else None
            return make_response(env_id, error=_store_failed())
        return response

    def _serially(self, run: Callable[[], Any]) -> Any:
        """Run under the lock, one call at a time, then tell the audit
        listeners of the entries the store took, with the lock released."""
        with self._lock:
            self._calls += 1
            try:
                result = run()
            finally:
                self._calls -= 1
                heard: list[tuple[str, AuditEntry]] = []
                if self._calls == 0:
                    heard, self._heard = self._heard, []
        for ws_id, entry in heard:
            for listener in self._audit_listeners:
                try:
                    listener(ws_id, entry)
                except Exception:
                    pass
        return result

    def _outside_call(self, ws_id: str, work: Callable[[], list]) -> list:
        """Work that changes a workspace outside any call, a whisper lapse
        check for one, kept to the rules a call keeps (SPECIFICATION 10.3):
        the workspace is first brought up to date with the store, and left
        alone as long as it is held; what the work changed is written once,
        when it ends; and where that write fails, the workspace goes back to what
        the store holds and the work's results are dropped, since none took
        effect."""
        def run() -> list:
            if self._scope.depth > 0:
                return work()
            if self._keep_up(ws_id) is not None:
                return []
            self._scope.depth += 1
            self._write_failures.clear()
            try:
                out = work()
            finally:
                self._scope.depth -= 1
                if self._scope.depth == 0 and self._unflushed:
                    self._flush_unflushed()
            if self._write_failures.pop(ws_id, None) is not None:
                self._reload(ws_id)
                return []
            return out
        return self._serially(run)

    def _run(self, envelope: Any) -> tuple[dict, str | None]:
        """One attempt at a call, its write included. The second value is how
        the call's write failed, "conflict" or "failed", or None."""
        self._frozen_now = self._advance_clock()
        self._scope.depth += 1
        if self._scope.depth == 1:
            self._write_failures.clear()
        target = _target_of(envelope)
        ran = False
        try:
            refused = self._keep_up(target) if target is not None else None
            if refused is not None:
                env_id = envelope.get("id") if isinstance(envelope, dict) else None
                response = make_response(env_id, error=refused)
            else:
                ran = True
                response = self._dispatch(envelope)
                error = response.get("error")
                if error:
                    self._record_refusal(envelope, error)
                elif target is None and envelope.get("method") == "workspace.create":
                    target = response["result"]["workspace"]
        finally:
            # One write per call, with everything the call changed, made
            # before dispatch returns.
            self._scope.depth -= 1
            if self._scope.depth == 0 and self._unflushed:
                self._flush_unflushed()
            self._frozen_now = None
        if not ran or target is None or self._scope.depth != 0:
            return response, None
        failure = self._write_failures.pop(target, None)
        if failure is not None:
            # The store does not hold what the call did: the workspace goes
            # back to what it does hold, or stays held while it cannot be read.
            self._reload(target)
        return response, failure

    def _keep_up(self, ws_id: str) -> dict | None:
        """Before a call on a workspace: where the store holds a different
        number of audit entries for it than this instance last wrote, another
        writer has written it, and it is reloaded. A workspace held after a
        failed write is reloaded as well, and the call refused while that
        fails."""
        if ws_id in self._held:
            return None if self._reload(ws_id) else _store_failed()
        version = getattr(self.options.store, "version", None)
        if not callable(version):
            return None
        try:
            stored = version(ws_id)
        except Exception:
            # Unread, the store may still take the call's write; if it does
            # not, the write fails and is answered as failed.
            return None
        ws = self.workspaces.get(ws_id)
        p = getattr(ws, "_persisted", None) if ws is not None else None
        # A workspace written whole on its next write replaces the store's copy.
        if ws is not None and (p is None or p.replace):
            return None
        if stored == (p.seq if p is not None else None):
            return None
        return None if self._reload(ws_id) else _store_failed()

    def _reload(self, ws_id: str) -> bool:
        """Replace a workspace with what the store holds of it. False when it
        cannot be read."""
        store = self.options.store
        if store is None:
            self._held.discard(ws_id)
            return True
        try:
            load_one = getattr(store, "load_one", None)
            record = (load_one(ws_id) if callable(load_one)
                      else next((r for r in store.load() if r.id == ws_id), None))
            ws = _rehydrate_workspace(record.data) if record is not None else None
        except Exception as err:
            current = self.workspaces.get(ws_id)
            self._report_store_error(err, ws_id, len(current.audit) if current else 0)
            return False
        old = self.workspaces.get(ws_id)
        if old is not None:
            self._unflushed.pop(id(old), None)
        if ws is None:
            self.workspaces.pop(ws_id, None)
        else:
            self.workspaces[ws_id] = ws
            if self._tracking:
                # What was just loaded is what the store holds.
                self._track(ws, _Persisted(seq=len(ws.audit)))
        self._held.discard(ws_id)
        return True

    def _dispatch(self, envelope: dict) -> dict:
        # SPECIFICATION 10.1 fixes the order of the checks below, because
        # which refusal a call receives decides whether it is recorded. First
        # the request itself, none of whose refusals is recorded.
        if not is_valid_envelope(envelope):
            return make_response(
                envelope.get("id") if isinstance(envelope, dict) else None,
                error=rpc_error(E.REQUEST, "Invalid JSON-RPC 2.0 request"),
            )
        method = envelope.get("method")
        env_id = envelope.get("id")
        if not isinstance(method, str) or not method:
            return make_response(env_id, error=rpc_error(
                E.REQUEST, "Invalid JSON-RPC 2.0 request"))
        if not _within_nesting(envelope, _MAX_NESTING):
            return make_response(env_id, error=rpc_error(
                E.REQUEST, f"Envelope nests deeper than {_MAX_NESTING} levels"))

        # Canonicalised once: the length is what the size limit measures, and
        # a request that cannot be canonicalised is refused below, once its
        # method is known to exist.
        canonical_error: Exception | None = None
        try:
            size = len(canonicalize(envelope))
        except (ValueError, TypeError, RecursionError) as exc:
            size = 0
            canonical_error = exc
        if size > self.options.max_envelope_bytes:
            return make_response(
                envelope.get("id"),
                error=rpc_error(
                    E.REQUEST,
                    f"Envelope exceeds max_envelope_bytes "
                    f"({size} > {self.options.max_envelope_bytes})"),
            )

        # JSON-RPC params, when present, must be a structured value (object).
        # CHAP methods use by-name params; reject non-object params cleanly as
        # Invalid params rather than letting a handler raise an internal error.
        raw_params = envelope.get("params")
        if raw_params is None:
            params: dict = {}
        elif isinstance(raw_params, dict):
            params = raw_params
        else:
            return make_response(env_id, error=rpc_error(
                E.PARAMS, "Invalid params: expected an object"))

        handler = self._handlers.get(method)
        if handler is None:
            return make_response(
                env_id, error=rpc_error(E.METHOD, f"Unknown method: {method}")
            )
        if canonical_error is not None:
            return make_response(env_id, error=rpc_error(E.PARAMS, str(canonical_error)))
        # A workspace is named by a non-empty string. A list or an object in
        # its place cannot be looked up in a dict, and an empty one made
        # participant.join create a workspace it then could not find, so both
        # references refuse either before any lookup.
        if "workspace" in params and (not isinstance(params["workspace"], str)
                                      or not params["workspace"]):
            return make_response(env_id, error=rpc_error(
                E.PARAMS, "workspace must be a non-empty string"))

        # SPECIFICATION 10.1: a signed request whose signed content matches a
        # recorded refusal is answered with that refusal and not evaluated
        # again, and one that matches an accepted call is refused with -32075.
        # The log publishes every call, signature and all, so without this
        # anyone who can read the log could send a refused request again once
        # whatever refused it had changed, or an accepted one again once it
        # applied anew, a reviewer's reject to a resubmitted artefact for
        # instance, and it would take effect in its signer's name.
        target_id = params.get("workspace")
        target = self.workspaces.get(target_id) if isinstance(target_id, str) else None
        if target is not None and is_signed(envelope):
            index = self._signed_index(target)
            digest = signed_digest(_recorded_form(envelope))
            prior = index["refused"].get(digest)
            if prior is not None:
                seq, code = prior
                return make_response(env_id, error=rpc_error(
                    code, f"Refused at seq {seq}; a refused request is not evaluated again",
                    data={"refused_at_seq": seq}))
            accepted = index["accepted"].get(digest)
            if accepted is not None:
                return make_response(env_id, error=rpc_error(
                    E.SIG_ALREADY_ACCEPTED,
                    f"Accepted at seq {accepted}; a signed request takes effect once",
                    data={"accepted_at_seq": accepted}))

        # security-signed/1.0: verify the top-level `sig` where the workspace
        # requires signatures, because it advertises the profile or this
        # coordinator is configured to require them. participant.join and
        # workspace.create are bootstrap operations that run before any
        # signing key is registered for the actor, so they cannot be
        # signature-verified. Under the "humans" policy a call that carries
        # no signature is taken from an agent member, such as one on the
        # server's own transport; people and services sign every call.
        sender = _raw_member(target, params.get("from")) if target is not None else None
        exempt = (target is not None and not is_signed(envelope)
                  and not self._must_sign(target, sender.type if sender is not None else None))
        if (self._requires_signatures(target)
                and method not in ("participant.join", "workspace.create")
                and not exempt):
            sig_err = self._verify_signature(envelope)
            if sig_err:
                return make_response(env_id, error=sig_err)
            if method == "participant.rotate_key":
                signing_kid = envelope["sig"].split(":", 2)[1]
                old_kid = params.get("old_kid")
                if old_kid is not None and signing_kid != old_kid:
                    return make_response(env_id, error=rpc_error(
                        E.SIG_ROTATION_KEY_MISMATCH,
                        "Rotation must be signed with the old key"))

        # identity-oidc/1.0: step-up freshness check on privileged methods.
        if self.options.enforce_step_up and method in PRIVILEGED_METHODS:
            stale = self._check_step_up(params)
            if stale:
                return make_response(env_id, error=stale)

        # SPECIFICATION 15.4: refuse a method whose owning profile the
        # workspace does not advertise. The refusal is -32601, the same answer
        # a coordinator that never implemented the method would give, so a
        # deployment cannot tell from outside which of the two it is talking
        # to. The detail rides in `data` for an operator reading the log.
        gate = self._profile_gate(method, params)
        if gate:
            return make_response(env_id, error=gate)

        # control/1.0 and deliberation/1.0: these operations are privileged
        # and MUST be performed by a workspace member. Control is the
        # governance "emergency brake"; deliberation open/close set the vote
        # parameters and finalize the tally. Without this floor a non-member
        # could resume a paused workspace, raise the mode ceiling, or open and
        # close a deliberation to finalize an outcome early. (The per-voter
        # eligibility check in deliberate.vote is separate and still applies.
        # Deployments needing a stricter role gate than membership layer it on
        # top via an identity-* profile or application check.) The routing
        # methods and participant.leave sit under the same floor
        # (SPECIFICATION 6.3.1): without it a non-member could reassign a task
        # with task.route, or write a leave to the log under any name.
        if (method.startswith("control.") or method.startswith("deliberate.")
                or method.startswith("handoff.")
                or method.startswith("whisper.")
                or method in _MEMBER_ONLY_METHODS):
            ws_id = params.get("workspace") if isinstance(params, dict) else None
            ws = self.workspaces.get(ws_id) if isinstance(ws_id, str) else None
            if ws is not None:
                not_member = self._require_member(ws, params.get("from"))
                if not_member:
                    return make_response(env_id, error=not_member["error"])

        # control/1.0 workspace-paused gate (S6 -32063).
        if method not in {"workspace.create", "workspace.describe",
                          "control.resume", "audit.read", "participant.join",
                          "participant.leave"}:
            ws_id = params.get("workspace") if isinstance(params, dict) else None
            if isinstance(ws_id, str):
                ws = self.workspaces.get(ws_id)
                if ws and ws.state == "paused":
                    return make_response(env_id, error=rpc_error(
                        E.CONTROL_WORKSPACE_PAUSED,
                        f"Workspace {ws_id} is paused"))

        # task.create idempotency: a repeat carrying a seen idempotency_key
        # returns the original task rather than creating (or recording) a
        # duplicate -- safe for at-least-once redelivery.
        if method == "task.create":
            key = params.get("idempotency_key")
            ws_id = params.get("workspace")
            ws = self.workspaces.get(ws_id) if isinstance(ws_id, str) else None
            if ws is not None and isinstance(key, str):
                existing_id = ws.idempotency_keys.get(key)
                task = ws.tasks.get(existing_id) if existing_id else None
                if task is not None:
                    return make_response(env_id, result={
                        "task_id": existing_id, "state": task.state})

        try:
            # The handler works on its own copy of what the caller sent, and
            # the caller gets a copy of the result, so no object is shared
            # between the workspace and the caller. A caller that reused or
            # changed one later would change the workspace outside any call,
            # where no change is recorded for the store.
            out = handler(copy.deepcopy(params))
        except Exception as exc:
            # The caller learns only that the call failed. An exception's text
            # can name a path, a query or a value from another workspace, so it
            # goes to the operator through on_internal_error and never onto
            # the wire.
            self._report_internal_error(exc, method, params.get("workspace"))
            return make_response(env_id, error=rpc_error(E.INTERNAL, "Internal error"))

        if "error" in out:
            return make_response(env_id, error=out["error"])

        # Record audit on successful operations that name a workspace. A
        # refusal is weighed in dispatch(), once this returns, against the
        # rule in SPECIFICATION 10.1. A workspace.create that names no
        # workspace is recorded in the one it made, which was otherwise left
        # with no entry and never written to the store.
        ws_id = (out["result"]["workspace"] if method == "workspace.create"
                 else params.get("workspace"))
        if isinstance(ws_id, str) and method not in _READ_ONLY_METHODS:
            ws = self.workspaces.get(ws_id)
            if ws is not None:
                self._record_audit(ws, _recorded_form(envelope))

        return make_response(env_id, result=copy.deepcopy(out.get("result")))

    # -- audit recording -----------------------------------------------

    def _record_audit(self, ws: Workspace, envelope: dict) -> None:
        self._append_entry(ws, envelope=copy.deepcopy(envelope))

    def _record_refusal(self, envelope: Any, error: dict) -> None:
        """SPECIFICATION 10.1: record a refused call when it is a governed attempt.

        The caller must be a member of an existing workspace, the call must
        be a JSON-RPC call for a method that is not a read, and the refusal
        must be one ``refusal_is_recorded`` names. The request is kept as it
        arrived, so a signature on it still verifies. Every call that
        reaches a recorded refusal has passed the canonical check, so it can
        be hashed into the chain.
        """
        if not is_valid_envelope(envelope) or not isinstance(envelope.get("method"), str):
            return
        method = envelope["method"]
        if method in _READ_ONLY_METHODS:
            return
        if not refusal_is_recorded(method, error, PRIVILEGED_METHODS):
            return
        params = envelope.get("params")
        if not isinstance(params, dict):
            return
        ws_id = params.get("workspace")
        ws = self.workspaces.get(ws_id) if isinstance(ws_id, str) else None
        sender = params.get("from")
        if ws is None or not isinstance(sender, str) or sender not in ws.members:
            return
        # A signed copy of a call already on the log is not a new attempt by
        # its signer. A copy of a refusal was answered with it, and a copy of
        # a call that took effect repeats a call its signer made once. Anyone
        # who can read the log can send either.
        if is_signed(envelope):
            index = self._signed_index(ws)
            digest = signed_digest(envelope)
            if digest in index["refused"] or digest in index["accepted"]:
                return
        self._append_entry(ws, request=copy.deepcopy(envelope),
                           outcome={"status": "refused", "code": int(error["code"])})

    def _signed_index(self, ws: Workspace) -> dict:
        """The signed calls on this workspace's log, by what their senders signed.

        ``refused`` maps a recorded refusal to its ``(seq, code)``, and
        ``accepted`` maps each call that took effect to its seq. Held beside the
        workspace rather than in it, so a snapshot never carries it, and
        rebuilt from the log when missing.
        """
        index = getattr(ws, "_signed_index", None)
        if index is None:
            index = {"refused": {}, "accepted": {}}
            for entry in ws.audit:
                _index_signed(index, entry)
            object.__setattr__(ws, "_signed_index", index)
        return index

    def _append_entry(self, ws: Workspace, *, envelope: dict | None = None,
                      request: dict | None = None, outcome: dict | None = None) -> None:
        entry = AuditEntry(seq=len(ws.audit), arrived=self.now_iso(),
                           envelope=envelope, request=request, outcome=outcome)
        if ws.chain_enabled or self.options.enable_chain:
            prev = ws.chain_head or ZERO_HASH
            entry.prev_hash = prev
            ws.chain_head = link_hash(entry_record(entry), prev)
        ws.audit.append(entry)
        index = getattr(ws, "_signed_index", None)
        if index is not None:
            _index_signed(index, entry)
        # The listeners hear of the entry once it is written, so they never
        # hear of one the store did not take.
        queued = getattr(ws, "_unannounced", None)
        if queued is None:
            object.__setattr__(ws, "_unannounced", [entry])
        else:
            queued.append(entry)
        if self.options.store is None:
            self._announce(ws, len(ws.audit))
            return
        if not self._tracking:
            self._persist(ws)
            return
        # One clock reading per entry, where earlier releases read it to stamp
        # the snapshot, so a deterministic clock gives the same times as before.
        self._persisted_of(ws).updated_at = self.now_iso()
        if self._scope.depth > 0:
            self._unflushed[id(ws)] = ws
        else:
            self._flush(ws)

    def _persist(self, ws: Workspace) -> None:
        """Save the whole workspace, for a store that implements only `save`."""
        if self.options.store is None:
            return
        try:
            record = self._snapshot_workspace(ws)
            self.options.store.save(record)
        except Exception as err:
            self._write_failed(ws, err, len(ws.audit))
            return
        self._announce(ws, len(ws.audit))

    def _announce(self, ws: Workspace, up_to: int) -> None:
        """Queue the entries now written, up to ``up_to``, for the audit
        listeners, who hear of them once the call lets the lock go."""
        queued = getattr(ws, "_unannounced", None) or []
        self._heard.extend((ws.id, e) for e in queued if e.seq < up_to)
        object.__setattr__(ws, "_unannounced", [e for e in queued if e.seq >= up_to])

    def _write_failed(self, ws: Workspace, err: Exception, version: int) -> None:
        """A write that did not reach the store: reported, and the call that
        made it answered as ``store_errors`` says. A write another writer got
        to first fails its call under either."""
        self._report_store_error(err, ws.id, version)
        if isinstance(err, StoreConflictError):
            self._write_failures[ws.id] = "conflict"
        elif self.options.store_errors == "log":
            return
        else:
            self._write_failures[ws.id] = "failed"
        self._held.add(ws.id)

    def _report_internal_error(self, exc: Exception, method: str, ws_id: Any) -> None:
        call: dict = {"method": method}
        if ws_id is not None:
            call["workspace"] = ws_id
        if self.options.on_internal_error is not None:
            try:
                self.options.on_internal_error(exc, call)
            except Exception:
                pass
            return
        print(f"chap coordinator: {method} failed with an internal error:", file=sys.stderr)
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)

    def _report_store_error(self, err: Exception, ws_id: str, version: int) -> None:
        if self.options.on_store_error is not None:
            try:
                self.options.on_store_error(err, {"id": ws_id, "version": version})
            except Exception:
                pass
            return
        # Another writer having written first is handled: the workspace is
        # reloaded, and the call may be sent again.
        level = logging.WARNING if isinstance(err, StoreConflictError) else logging.ERROR
        _log.log(level, "writing workspace %s to the store failed: %s", ws_id, err)

    # -- change tracking, for a store that implements `apply` -----------

    def _track(self, ws: Workspace, persisted: _Persisted) -> None:
        """Track this workspace's collections from here on."""
        for name in _TRACKED:
            d = getattr(ws, name)
            if not (isinstance(d, TrackedDict) and d.scope is self._scope):
                setattr(ws, name, TrackedDict.bound(dict.items(d), self._scope))
            else:
                d.dirty.clear()
        object.__setattr__(ws, "_persisted", persisted)

    def _persisted_of(self, ws: Workspace) -> _Persisted:
        """What the store holds of a workspace.

        A workspace this instance did not create or load, such as one an
        application put in ``workspaces`` itself, is tracked from here on and
        written whole once.
        """
        p = getattr(ws, "_persisted", None)
        if p is None or any(not (isinstance(getattr(ws, n), TrackedDict)
                                 and getattr(ws, n).scope is self._scope)
                            for n in _TRACKED):
            p = _Persisted(seq=0, replace=True)
            self._track(ws, p)
        return p

    def _flush_unflushed(self) -> None:
        pending = list(self._unflushed.values())
        self._unflushed.clear()
        for ws in pending:
            self._flush(ws)

    def _flush(self, ws: Workspace) -> None:
        """Write what changed in one workspace since its last write."""
        p = self._persisted_of(ws)
        try:
            change = self._take_change(ws, p)
        except Exception as err:
            # What was taken is unknown, so the next write carries the whole
            # workspace.
            p.replace = True
            self._write_failed(ws, err, len(ws.audit))
            return
        if change is None:
            return
        try:
            self.options.store.apply(change)
        except Exception as err:
            self._give_back(ws, p, change)
            self._write_failed(ws, err, change.version)
            return
        p.seq = change.version
        if change.replace:
            p.replace = False
        self._announce(ws, change.version)

    def _take_change(self, ws: Workspace, p: _Persisted):
        """The change since the last write, taking the recorded keys with it."""
        replace = p.replace
        objects: dict[str, dict] = {}
        for name in _TRACKED:
            d: TrackedDict = getattr(ws, name)
            if replace:
                objects[name] = {k: _plain(v) for k, v in dict.items(d)}
            elif d.dirty:
                objects[name] = {
                    k: (_plain(dict.__getitem__(d, k)) if dict.__contains__(d, k) else None)
                    for k in d.dirty}
            d.dirty.clear()
        entries = [e.to_dict() for e in ws.audit[0 if replace else p.seq:]]
        if not replace and not entries and not objects:
            return None
        meta = {f.name: copy.deepcopy(getattr(ws, f.name))
                for f in dataclasses.fields(ws) if f.name not in _NOT_META}
        updated_at = p.updated_at or (ws.audit[-1].arrived if ws.audit else ws.created)
        return StoreChange(id=ws.id, base=0 if replace else p.seq,
                           version=len(ws.audit), updated_at=updated_at,
                           meta=meta, entries=entries, objects=objects,
                           replace=replace)

    def _give_back(self, ws: Workspace, p: _Persisted, change) -> None:
        """A failed write's keys go back, so the next write carries them."""
        if change.replace:
            p.replace = True
            return
        for name, items in change.objects.items():
            d = getattr(ws, name, None)
            if isinstance(d, TrackedDict):
                d.dirty.update(items.keys())

    def _snapshot_workspace(self, ws: Workspace) -> "WorkspaceRecord":
        """JSON-safe snapshot of one workspace, plus a version counter."""
        data = dataclasses.asdict(ws)
        # Entries are stored in their wire shape, so an accepted entry carries
        # no null request or outcome, and a refusal no null envelope.
        data["audit"] = [copy.deepcopy(e.to_dict()) for e in ws.audit]
        version = len(ws.audit)
        return WorkspaceRecord(
            id=ws.id, data=data, version=version, updated_at=self.now_iso(),
        )

    def _profile_gate(self, method: str, params: Any) -> dict | None:
        """The SPECIFICATION 15.4 check, per method rather than per namespace.

        Core is always present, so a core method is never gated. The reads in
        ALWAYS_AVAILABLE are never gated either: a workspace must be able to
        ask what it is and to check its own chain, and `audit.verify_chain`
        belongs to `audit-scitt/1.0` while chaining also turns on through an
        option, so gating it would let a workspace write a hash-linked chain it
        is refused permission to verify.
        """
        owner = OWNING_PROFILE.get(method)
        if owner is None or owner == "core/1.0" or method in ALWAYS_AVAILABLE:
            return None
        ws_id = params.get("workspace") if isinstance(params, dict) else None
        ws = self.workspaces.get(ws_id) if isinstance(ws_id, str) else None
        if ws is None:
            return None
        # Match on the profile name, so a workspace advertising a later minor
        # version of the owning profile still carries its methods.
        if ws.has_profile(owner.split("/")[0]):
            return None
        return rpc_error(E.METHOD, f"Unknown method: {method}",
                         data={"profile": owner, "advertised": list(ws.profiles)})

    def _restore_from_store(self) -> None:
        if self.options.store is None:
            return
        try:
            records = self.options.store.load()
        except Exception:
            return
        from .types import Workspace as _Workspace, AuditEntry as _AuditEntry
        for r in records:
            try:
                ws = _rehydrate_workspace(r.data)
                self.workspaces[ws.id] = ws
            except Exception as err:
                # One workspace that cannot be rebuilt is skipped, with a
                # warning, so the others still load.
                _log.warning("workspace %s could not be restored: %s", r.id, err)
                continue
            if self._tracking:
                # What was just loaded is what the store holds.
                self._track(ws, _Persisted(seq=len(ws.audit)))

    # -- signature verification (security-signed/1.0) ----------------

    def _verify_signature(self, envelope: dict) -> dict | None:
        sig = envelope.get("sig")
        if not sig or not isinstance(sig, str):
            return rpc_error(E.SIG_VERIFY_FAILED, "Missing top-level `sig` field")
        # ed25519:<kid>:<base64>, or webauthn:<kid>:<assertion> for a key a
        # browser's authenticator holds (security-signed/1.0 3).
        parts = sig.split(":")
        scheme = parts[0]
        if len(parts) != 3 or scheme not in ("ed25519", "webauthn"):
            return rpc_error(E.SIG_VERIFY_FAILED, "Malformed signature tag")
        assertion = parse_webauthn_assertion(parts[2]) if scheme == "webauthn" else None
        if scheme == "webauthn" and assertion is None:
            return rpc_error(E.SIG_VERIFY_FAILED, "Malformed signature tag")
        kid, sig_b64 = parts[1], parts[2]

        params = envelope.get("params") or {}
        sender = params.get("from") if isinstance(params, dict) else None
        ws_id = params.get("workspace") if isinstance(params, dict) else None
        raw_ts = params.get("ts") if isinstance(params, dict) else None
        # Fail closed: a signature is present (checked above) and
        # require_signatures is on, so it must verify. If we cannot resolve
        # the context needed to verify it, reject rather than skip -- a
        # signature we cannot check must never be treated as valid. A `from`
        # or `workspace` that is not a string names nothing, so it counts as
        # missing, and never reaches a lookup.
        if (not isinstance(sender, str) or not sender
                or not isinstance(ws_id, str) or not ws_id):
            return rpc_error(E.SIG_VERIFY_FAILED,
                             "Cannot verify signature: missing from/workspace")
        # The key is chosen by the time the sender gives. A `ts` that is not a
        # string gives no time, so no key can be chosen for it.
        if raw_ts is not None and not isinstance(raw_ts, str):
            return rpc_error(E.SIG_VERIFY_FAILED,
                             "Cannot verify signature: ts must be a string")
        ts = raw_ts if raw_ts is not None else self.now_iso()

        ws = self.workspaces.get(ws_id)
        if not ws:
            return rpc_error(E.SIG_VERIFY_FAILED,
                             "Cannot verify signature: unknown workspace")
        member = ws.members.get(sender)
        if not member:
            return rpc_error(E.SIG_KEY_NOT_FOUND, f"No member: {sender}")
        # Revocation is present-tense: a revoked key must be rejected for any
        # live request regardless of the envelope's self-asserted `ts`.
        # Checking against `ts` alone lets a holder of a revoked key backdate
        # `ts` to before the revocation and still be accepted. Use the
        # coordinator's trusted clock for the revocation decision.
        now = self.now_iso()
        for k in member.keys:
            if k.kid == kid and k.revoked_at is not None and now >= k.revoked_at:
                return rpc_error(E.SIG_KEY_REVOKED, f"Key {kid} is revoked")
            # Expiry is present-tense for the same reason: a rotated-out key
            # (valid_until set to the rotation time, but never explicitly
            # revoked) must not sign live requests. Selecting it by the
            # self-asserted `ts` alone lets its holder backdate `ts` to before
            # valid_until and keep using it. Judge expiry by the trusted clock.
            if k.kid == kid and k.valid_until is not None and now >= k.valid_until:
                return rpc_error(E.SIG_KEY_NOT_FOUND, f"Key {kid} is no longer valid")
        key = member.key_for(kid, ts)
        if not key:
            for k in member.keys:
                if k.kid == kid and k.revoked_at is not None:
                    return rpc_error(E.SIG_KEY_REVOKED, f"Key {kid} is revoked")
            return rpc_error(E.SIG_KEY_NOT_FOUND,
                             f"No key {kid} valid at {ts} for {sender}")

        # Strip sig from a deep copy and re-canonicalise
        stripped = copy.deepcopy(envelope)
        stripped.pop("sig", None)
        canonical = canonicalize(stripped)

        if assertion is not None:
            if not is_webauthn_key(key.jwk):
                return rpc_error(E.SIG_VERIFY_FAILED, f"Key {kid} is not a WebAuthn key")
            failure = webauthn_failure(canonical, assertion, key.jwk)
            return None if failure is None else rpc_error(E.SIG_VERIFY_FAILED, failure)

        # An ed25519: tag names an Ed25519 key, and a webauthn: tag a
        # WebAuthn key, so neither key is read as the other kind.
        if not is_ed25519_key(key.jwk):
            return rpc_error(E.SIG_VERIFY_FAILED, f"Key {kid} is not an Ed25519 key")
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PublicKey,
            )
        except ImportError:
            return rpc_error(E.SIG_VERIFY_FAILED, CRYPTOGRAPHY_MISSING)
        signature = strict_base64(sig_b64)
        if signature is None:
            return rpc_error(E.SIG_VERIFY_FAILED, "Signature failed verification")
        try:
            import base64
            raw_x = key.jwk.get("x", "")
            pad = "=" * (-len(raw_x) % 4)
            pub_bytes = base64.urlsafe_b64decode(raw_x + pad)
            pub = Ed25519PublicKey.from_public_bytes(pub_bytes)
            pub.verify(signature, canonical)
            return None
        except Exception:
            return rpc_error(E.SIG_VERIFY_FAILED, "Signature failed verification")

    # -- step-up check (identity-oidc/1.0) ----------------------------

    def _requires_signatures(self, ws: Workspace | None) -> bool:
        """Whether a workspace requires signatures: it advertises
        security-signed/1.0, or this coordinator is configured to."""
        return bool(self.options.require_signatures) or (
            ws is not None and ws.has_profile("security-signed"))

    @staticmethod
    def _must_sign(ws: Workspace, kind: str | None) -> bool:
        """Whether a participant of this type signs its calls in a workspace
        that requires signatures: every participant under "all", and every
        one but an agent under "humans". A non-member signs."""
        return ws.signature_policy == "all" or kind != "agent"

    def _unverified_identity(self, profiles: list[str]) -> dict | None:
        """SPECIFICATION 6.5: an identity profile states that a verifier
        checks people's credentials, so advertising one this coordinator has
        no verifier for describes a guarantee it does not give."""
        def named(name: str) -> bool:
            return any(x == name or x.startswith(name + "/") for x in profiles)
        if named("identity-oidc") and self.options.verify_oidc_token is None:
            return rpc_error(
                E.PARAMS,
                "identity-oidc/1.0 is advertised but no token verifier is configured. "
                "A workspace that advertises it and does not enforce it "
                "describes a guarantee it does not give.")
        if named("identity-vc") and self.options.verify_vc is None:
            return rpc_error(
                E.PARAMS,
                "identity-vc/1.0 is advertised but no presentation verifier is configured. "
                "A workspace that advertises it and does not enforce it "
                "describes a guarantee it does not give.")
        return None

    def _check_step_up(self, params: dict) -> dict | None:
        # A `from` or `workspace` that is not a string names no member to hold
        # to step-up; the membership check answers such a call.
        ws_id = params.get("workspace")
        sender = params.get("from")
        if (not isinstance(ws_id, str) or not isinstance(sender, str)
                or not ws_id or not sender):
            return None
        ws = self.workspaces.get(ws_id)
        if not ws:
            return None
        member = ws.members.get(sender)
        if member is None:
            return None
        # Step-up applies to OIDC actors -- humans, or any member with an OIDC
        # binding. Agents and services authenticate out of band (SPIFFE/X.509),
        # so they are not held to an auth_time window here.
        if member.type != "human" and member.oidc_sub is None:
            return None
        if member.oidc_auth_time is None:
            return rpc_error(E.OIDC_STEP_UP_REQUIRED,
                             "Step-up authentication required",
                             {"window_sec": ws.step_up_window_sec})
        now_unix = int(_dt.datetime.now(_dt.timezone.utc).timestamp())
        age = now_unix - member.oidc_auth_time
        if age > ws.step_up_window_sec:
            return rpc_error(E.OIDC_STEP_UP_REQUIRED,
                             "Step-up authentication required",
                             {"window_sec": ws.step_up_window_sec,
                              "age_sec": age})
        if ws.min_acr is not None and member.oidc_acr != ws.min_acr:
            return rpc_error(E.OIDC_STEP_UP_REQUIRED,
                             "Step-up required: acr below the workspace minimum",
                             {"required_acr": ws.min_acr, "acr": member.oidc_acr})
        return None

    # ============================================================
    #   Handler registration
    # ============================================================

    def _register_core_handlers(self) -> None:
        # Core
        self._handlers.update({
            "workspace.create":       self._op_workspace_create,
            "workspace.describe":     self._op_workspace_describe,
            "workspace.set_profiles": self._op_workspace_set_profiles,
            "workspace.invite":       self._op_workspace_invite,
            "participant.join":       self._op_participant_join,
            "participant.leave":      self._op_participant_leave,
            "task.create":            self._op_task_create,
            "task.update":            self._op_task_update,
            "task.complete":          self._op_task_complete,
            "audit.read":             self._op_audit_read,
        })
        # review/1.0
        self._handlers.update({
            "review.request":  self._op_review_request,
            "review.acknowledge": self._op_review_acknowledge,
            "decide.approve":  lambda p: self._op_decide(p, "approve"),
            "decide.reject":   lambda p: self._op_decide(p, "reject"),
            "decide.override": self._op_decide_override,
            "abstain.declare": self._op_abstain_declare,
            "escalate.raise":  self._op_escalate_raise,
        })

    def _register_profile_handlers(self) -> None:
        from .profiles.whisper import register_whisper
        from .profiles.deliberation import register_deliberation
        from .profiles.handoff import register_handoff
        from .profiles.control import register_control
        from .profiles.routing import register_routing
        from .profiles.security_signed import register_security_signed
        from .profiles.audit_scitt import register_audit_scitt
        from .profiles.identity_oidc import register_identity_oidc
        from .profiles.identity_vc import register_identity_vc

        register_whisper(self)
        register_deliberation(self)
        register_handoff(self)
        register_control(self)
        register_routing(self)
        register_security_signed(self)
        register_audit_scitt(self)
        register_identity_oidc(self)
        register_identity_vc(self)

    # ============================================================
    #   Core method handlers
    # ============================================================

    def _op_workspace_create(self, p: dict) -> dict:
        ws, error = self._build_workspace(p)
        if error is not None:
            return error
        self._add_workspace(ws)
        return {"result": {"workspace": ws.id, "created": ws.created}}

    def _build_workspace(self, p: dict) -> tuple[Workspace | None, dict | None]:
        """The workspace a workspace.create with these params makes, or why it
        cannot be made.

        Nothing is registered, so participant.join can judge a join by the
        workspace it would make and make it only once the join passes.
        """
        bad = params_error(p, [
            ("profiles", "strings"), ("mode", "string"),
            ("mode_ceiling", "string"), ("routing_policy_uri", "string"),
            ("step_up_window_sec", "integer"), ("min_acr", "string"),
        ])
        if bad:
            return None, bad
        for name in ("mode", "mode_ceiling"):
            if p.get(name) is not None and p[name] not in _MODE_ORDER:
                return None, {"error": rpc_error(
                    E.PARAMS, f"{name} must be shadow, trial or production")}
        policy = p.get("signature_policy")
        if policy is None:
            policy = self.options.signature_policy
        if policy not in ("all", "humans"):
            return None, {"error": rpc_error(E.PARAMS, "signature_policy must be all or humans")}
        join_policy = p.get("join_policy")
        if join_policy is None:
            join_policy = self.options.join_policy
        if join_policy not in ("open", "invite"):
            return None, {"error": rpc_error(E.PARAMS, "join_policy must be open or invite")}
        review_rule = p.get("review_rule")
        if review_rule is not None and (not isinstance(review_rule, str) or not _review_rule_supported(review_rule)):
            return None, {"error": rpc_error(
                E.PARAMS, "review_rule must be any_one_approves, all_approve or quorum:<n>")}
        ws_id = p.get("workspace") or self.ids.workspace_id()
        if ws_id in self.workspaces:
            return None, {"error": rpc_error(E.PARAMS, f"workspace already exists: {ws_id}")}
        requested = p.get("profiles")
        if requested is not None:
            unknown = _profiles_error(requested)
            if unknown is not None:
                return None, unknown
        profiles = list(requested if requested is not None else self.options.default_profiles)

        # SPECIFICATION 6.5: the descriptor and the enforcement agree.
        # Signatures are enforced wherever security-signed/1.0 is advertised,
        # so enforcing them through the option adds the profile, and
        # advertising it needs nothing configured. A token verifier cannot
        # follow the profile, so identity-oidc/1.0 advertised with none
        # configured describes a guarantee the workspace does not give, and is
        # refused here, where the misconfiguration surfaces at once.
        signed = any(x == "security-signed" or x.startswith("security-signed/") for x in profiles)
        if self.options.require_signatures and not signed:
            profiles.append("security-signed/1.0")
        oidc = any(x == "identity-oidc" or x.startswith("identity-oidc/") for x in profiles)
        if self.options.verify_oidc_token is not None and not oidc:
            profiles.append("identity-oidc/1.0")
        vc = any(x == "identity-vc" or x.startswith("identity-vc/") for x in profiles)
        if self.options.verify_vc is not None and not vc:
            profiles.append("identity-vc/1.0")
        unverified = self._unverified_identity(profiles)
        if unverified is not None:
            return None, {"error": unverified}

        ws = Workspace(
            id=ws_id,
            created=self.now_iso(),
            state="active",
            profiles=profiles,
            mode=p.get("mode") or "trial",
            mode_ceiling=p.get("mode_ceiling") or "production",
            routing_policy_uri=p.get("routing_policy_uri"),
            step_up_window_sec=(300 if p.get("step_up_window_sec") is None
                                else int(p["step_up_window_sec"])),
            min_acr=p.get("min_acr"),
            signature_policy=policy,
            join_policy=join_policy,
            review_rule=review_rule,
        )
        if "audit-scitt/1.0" in profiles or self.options.enable_chain:
            ws.chain_enabled = True
            ws.chain_head = ZERO_HASH
        return ws, None

    def _add_workspace(self, ws: Workspace) -> None:
        self.workspaces[ws.id] = ws
        if self._tracking:
            # New here, so the store holds nothing of it yet.
            self._track(ws, _Persisted(seq=0))

    def _op_workspace_describe(self, p: dict) -> dict:
        miss = _missing(p, ["workspace"])
        if miss:
            return {"error": rpc_error(E.PARAMS, f"Missing field: {miss}")}
        ws = self.workspaces.get(p["workspace"])
        if not ws:
            return {"error": rpc_error(E.PARAMS, f"Unknown workspace: {p['workspace']}")}
        if self.options.require_read_membership:
            not_member = self._require_member(ws, p.get("from"))
            if not_member:
                return not_member
        out: dict[str, Any] = {
            "id": ws.id,
            "created": ws.created,
            "state": ws.state,
            "mode": ws.mode,
            "mode_ceiling": ws.mode_ceiling,
            "max_envelope_bytes": self.options.max_envelope_bytes,
            "step_up_window_sec": ws.step_up_window_sec,
            "signature_policy": ws.signature_policy,
            "join_policy": ws.join_policy,
            "review_rule": ws.review_rule or "any_one_approves",
            "profiles": ws.profiles,
            "members": [m.to_dict() for m in dict.values(ws.members)],
            "audit_count": len(ws.audit),
            "task_count": len(ws.tasks),
            "override_count": len(ws.overrides),
        }
        # A workspace without the chain has no head. Sending null where the
        # TypeScript reference omits the key made the same call answer two
        # ways on the wire, since JSON.stringify drops an undefined value.
        if ws.chain_head:
            out["evidence_head"] = ws.chain_head
        if ws.routing_policy_uri:
            out["routing_policy_uri"] = ws.routing_policy_uri
        return {"result": out}

    def _op_workspace_set_profiles(self, p: dict) -> dict:
        miss = _missing(p, ["workspace", "from", "profiles"])
        if miss:
            return {"error": rpc_error(E.PARAMS, f"Missing field: {miss}")}
        if not isinstance(p["profiles"], list) or not all(isinstance(x, str) for x in p["profiles"]):
            return {"error": rpc_error(E.PARAMS, "profiles must be a list of strings")}
        unknown = _profiles_error(p["profiles"])
        if unknown is not None:
            return unknown
        ws = self.workspaces.get(p["workspace"])
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        not_member = self._require_member(ws, p["from"])
        if not_member:
            return not_member
        if ws.members[p["from"]].role != "admin":
            return {"error": rpc_error(
                E.NOT_AUTHORISED,
                "workspace.set_profiles requires the admin role")}
        # A workspace that requires signatures keeps requiring them, and one
        # that says its people are verified keeps verifying them. Removing
        # either profile would turn its checks off for every call after this
        # one, and the calls the log holds from then on would carry no
        # assurance of who sent them.
        new_profiles = list(p["profiles"])
        for name in _LASTING_PROFILES:
            held = next((x for x in ws.profiles if x == name or x.startswith(name + "/")), None)
            if held is not None and not any(x.startswith(name + "/") for x in new_profiles):
                return {"error": rpc_error(
                    E.NOT_AUTHORISED, f"{held} cannot be removed once advertised")}
        unverified = self._unverified_identity(new_profiles)
        if unverified is not None:
            return {"error": unverified}
        if not any(prof.startswith("core/") for prof in new_profiles):
            new_profiles.append("core/1.0")
        ws.profiles = new_profiles
        # If audit-scitt/1.0 newly active, enable chain
        if "audit-scitt/1.0" in new_profiles and not ws.chain_enabled:
            ws.chain_enabled = True
            ws.chain_head = ZERO_HASH
        return {"result": {"profiles": ws.profiles}}

    def _op_participant_join(self, p: dict) -> dict:
        miss = _missing(p, ["workspace", "from", "type"])
        if miss:
            return {"error": rpc_error(E.PARAMS, f"Missing field: {miss}")}
        # The member is stored under its URI and read by its type, so neither
        # may be None.
        for f in ("from", "type"):
            if not isinstance(p[f], str):
                return {"error": rpc_error(E.PARAMS, f"{f} must be a string")}
        # SPECIFICATION 5.1: a participant is one of five types, and the
        # scheme of its URI names the type. Otherwise "human:mallory" could
        # join as an agent, which the "humans" signature policy lets call
        # unsigned, and still read as a person on the log.
        kind = p["type"]
        named = _participant_error(kind, p["from"], "from")
        if named:
            return named
        bad = params_error(p, [
            ("role", "string"), ("display_name", "string"), ("scopes", "strings"),
            ("capabilities", "object"), ("jwks", "object"), ("jwks.keys", "list"),
            ("oidc_token", "string"), ("vc_presentation", "object"),
        ])
        if bad:
            return bad
        if p.get("role") == "admin" and kind not in _ADMIN_TYPES:
            return {"error": rpc_error(E.PARAMS, "The admin role is held by a human or a service")}
        # A join may make its workspace. It is judged by the workspace it
        # would make, which is registered only once the join passes, so a
        # refused join leaves nothing behind.
        ws_id = p["workspace"]
        ws = self.workspaces.get(ws_id)
        made = ws is None
        if ws is None:
            ws, error = self._build_workspace({"workspace": ws_id})
            if error is not None:
                return error
        uri = p["from"]
        now = self.now_iso()
        existing = ws.members.get(uri)
        token_verified = False
        holder_verified = False
        credential_bound = False

        # SPECIFICATION 6.3: a newcomer joins in the role an admin's
        # invitation names, else in the role it asks for. The admin role is
        # granted, never claimed: by an invitation, or to the first member,
        # who is the admin of a workspace that admits by invitation. Taken by
        # anyone who asked, it would let an agent drop the workspace's
        # identity profile or revoke a person's key.
        invitation = None if existing is not None else _raw_invitation(ws, uri)
        asked = p.get("role") or None
        role = asked or "participant"
        if existing is None:
            first = _awaits_first_member(ws)
            if (ws.join_policy == "invite" and invitation is None
                    and not (first and kind in _ADMIN_TYPES)):
                return {"error": rpc_error(
                    E.NOT_AUTHORISED, f"{uri} has no invitation to this workspace")}
            if invitation is not None and invitation.role is not None:
                if asked == "admin" and invitation.role != "admin":
                    return {"error": rpc_error(
                        E.NOT_AUTHORISED, "The admin role is granted by an admin")}
                role = invitation.role
            elif first and ws.join_policy == "invite":
                if asked is not None and asked != "admin":
                    return {"error": rpc_error(
                        E.PARAMS,
                        "The first member of a workspace that admits by invitation is its admin")}
                role = "admin"
            elif asked == "admin" and not first:
                return {"error": rpc_error(
                    E.NOT_AUTHORISED, "The admin role is granted by an admin")}

        # identity-oidc/1.0 and identity-vc/1.0: where a verifier is
        # configured and the workspace advertises its profile, a person joins
        # with the credential it checks. Otherwise a workspace that says its
        # people are verified would admit one on nobody's word. Where both
        # apply either will do, since the two coexist (identity-vc/1.0 2).
        if kind == "human":
            oidc = self.options.verify_oidc_token is not None and ws.has_profile("identity-oidc")
            vc = self.options.verify_vc is not None and ws.has_profile("identity-vc")
            presented = ((oidc and isinstance(p.get("oidc_token"), str))
                         or (vc and isinstance(p.get("vc_presentation"), dict)))
            if (oidc or vc) and not presented:
                if oidc:
                    return {"error": rpc_error(E.OIDC_TOKEN_INVALID, (
                        "An oidc_token or a vc_presentation is required to join as a human "
                        "participant") if vc else (
                        "An oidc_token is required to join as a human participant"))}
                return {"error": rpc_error(
                    E.VC_VP_INVALID, "A vc_presentation is required to join as a human participant")}

        member = Member(
            uri=uri,
            type=kind,
            role=role,
            joined=now,
            display_name=p.get("display_name"),
            capabilities=p.get("capabilities"),
            scopes=p.get("scopes"),
        )

        # An invitation that names a token subject admits a join carrying a
        # token for that subject, which only a configured verifier can check.
        if invitation is not None and invitation.oidc_sub is not None and (
                self.options.verify_oidc_token is None
                or not isinstance(p.get("oidc_token"), str)):
            return {"error": rpc_error(
                E.OIDC_TOKEN_INVALID,
                "An oidc_token is required: the invitation names a token subject")}

        # identity-oidc/1.0 binding
        if self.options.verify_oidc_token and isinstance(p.get("oidc_token"), str):
            claims = self.options.verify_oidc_token(p["oidc_token"])
            if claims is None:
                return {"error": rpc_error(E.OIDC_TOKEN_INVALID,
                                           "OIDC token invalid")}
            # A token binds only to the participant it belongs to. One naming
            # another participant is refused, and a join under an existing
            # member's name must carry a token for that member: the same
            # subject, or, for a member with no verified subject yet, a token
            # naming the member.
            bound_uri = claims.get("chap_participant_uri")
            sub = claims.get("sub")
            sub = sub if isinstance(sub, str) and sub else None
            if bound_uri is not None and bound_uri != uri:
                return {"error": rpc_error(E.OIDC_CNF_MISMATCH,
                                           "OIDC token is bound to another participant")}
            if invitation is not None and invitation.oidc_sub is not None \
                    and sub != invitation.oidc_sub:
                return {"error": rpc_error(E.OIDC_CNF_MISMATCH,
                                           "OIDC token subject does not match the invitation")}
            if existing is not None:
                if existing.oidc_sub is not None:
                    if sub != existing.oidc_sub:
                        return {"error": rpc_error(
                            E.OIDC_CNF_MISMATCH,
                            "OIDC token subject does not match the member")}
                elif bound_uri != uri:
                    return {"error": rpc_error(E.OIDC_CNF_MISMATCH,
                                               "OIDC token does not name this member")}
            elif kind == "human" and bound_uri != uri and (
                    invitation is None or invitation.oidc_sub is None):
                # A provider vouches for its user, and only the token, or an
                # admin's invitation naming the token's subject, says which
                # participant that user is. Otherwise any user of the provider
                # takes any free name.
                return {"error": rpc_error(E.OIDC_CNF_MISMATCH,
                                           "OIDC token does not name this participant")}
            member.oidc_sub = sub
            token_verified = True
            # A NumericDate may carry a fraction; both coordinators keep whole
            # seconds, and nothing that is not a finite number.
            at = claims.get("auth_time")
            if (isinstance(at, (int, float)) and not isinstance(at, bool)
                    and -_MAX_SAFE_INTEGER <= at <= _MAX_SAFE_INTEGER
                    and (isinstance(at, int) or math.isfinite(at))):
                member.oidc_auth_time = math.floor(at)
            acr = claims.get("acr")
            member.oidc_acr = acr if isinstance(acr, str) else None
            # The token binds the key in its cnf.jwk or, with none, the key
            # whose thumbprint is its nonce: a client that cannot have the key
            # put in the token puts its thumbprint in the nonce it asks the
            # identity provider for. A join that brings another key is
            # refused, so a token taken from its holder admits no key of the
            # taker's.
            cnf = claims.get("cnf")
            cnf_jwk = cnf.get("jwk") if isinstance(cnf, dict) else None
            bound = _credential_keys(cnf_jwk, claims.get("nonce") if cnf_jwk is None else None,
                                     _signing_keys(p.get("jwks")))
            if bound is None:
                return {"error": rpc_error(E.OIDC_CNF_MISMATCH,
                                           "The join's keys are not the key the token binds")}
            if bound is not False:
                credential_bound = True
                member.keys.extend(KeyRecord(jwk=j, kid=j["kid"], valid_from=now) for j in bound)

        # identity-vc/1.0 binding
        if self.options.verify_vc and isinstance(p.get("vc_presentation"), dict):
            subject = self.options.verify_vc(p["vc_presentation"])
            if subject is None:
                return {"error": rpc_error(E.VC_VP_INVALID,
                                           "VC presentation invalid")}
            holder = next((h for h in (subject.get("holder"), subject.get("id"))
                           if isinstance(h, str) and h), None)
            # A join under an existing member's name binds a presentation only
            # from the member's own holder.
            if existing is not None and (existing.vc_holder is None
                                         or holder != existing.vc_holder):
                return {"error": rpc_error(E.VC_HOLDER_BINDING_INVALID,
                                           "Presentation holder does not match the member")}
            member.vc_holder = holder
            holder_verified = True
            bound = _credential_keys(subject.get("cnf_jwk"), None, _signing_keys(p.get("jwks")))
            if bound is None:
                return {"error": rpc_error(E.VC_HOLDER_BINDING_INVALID,
                                           "The join's keys are not the key the presentation binds")}
            if bound is not False:
                credential_bound = True
                member.keys.extend(KeyRecord(jwk=j, kid=j["kid"], valid_from=now) for j in bound)

        # A person a credential verifies signs with a key the credential
        # binds, or one an invitation pins: keys the join asserts on its own
        # are not the person's on anyone's word.
        if (existing is None and kind == "human" and (token_verified or holder_verified)
                and not credential_bound and not (invitation is not None and invitation.jwks)
                and _signing_keys(p.get("jwks"))):
            if token_verified:
                return {"error": rpc_error(
                    E.OIDC_CNF_MISMATCH,
                    "The token binds no key: a person it verifies signs with a key it binds")}
            return {"error": rpc_error(
                E.VC_HOLDER_BINDING_INVALID,
                "The presentation binds no key: a person it verifies signs with a key it binds")}

        # An invitation that pins keys names the keys the member signs with:
        # the join's own keys, and a key an identity profile binds, are among
        # them, and the member holds the pinned keys.
        pinned = invitation.jwks["keys"] if invitation is not None and invitation.jwks else None
        if pinned is not None:
            strays = [j for j in [*_signing_keys(p.get("jwks")), *(k.jwk for k in member.keys)]
                      if not any(_same_key(k, j) for k in pinned)]
            if strays:
                return {"error": rpc_error(
                    E.NOT_AUTHORISED, f"{uri} joins with keys its invitation does not name",
                    data={"kids": list(dict.fromkeys(j.get("kid") for j in strays))})}
            member.keys = [KeyRecord(jwk=j, kid=j["kid"], valid_from=now) for j in pinned]

        attested = list(member.keys)

        # Self-asserted JWKs are ignored for an identity-bound participant: the
        # verifier-pinned key is the signing key (proof of possession).
        if not attested:
            for j in _signing_keys(p.get("jwks")):
                if not any(k.kid == j["kid"] for k in member.keys):
                    member.keys.append(KeyRecord(jwk=j, kid=j["kid"], valid_from=now))

        if existing is not None:
            # A member's keys change through participant.rotate_key, which the
            # old key signs. A join under the member's name that brings only
            # keys the member does not hold, and no identity binding one, is
            # refused: the name is held by other keys, and answering it as
            # joined would leave its sender holding a key nothing here
            # accepts.
            if not attested:
                keys = _signing_keys(p.get("jwks"))
                held = any(_same_key(k.jwk, j) for j in keys for k in existing.keys)
                if keys and not held:
                    return {"error": rpc_error(
                        E.NOT_AUTHORISED,
                        f"{uri} is already a member, with other keys; a new key arrives "
                        "through participant.rotate_key",
                        data={"kids": list(dict.fromkeys(j["kid"] for j in keys))})}
            for f in ("oidc_sub", "oidc_auth_time", "oidc_acr", "vc_holder"):
                v = getattr(member, f)
                if v is not None:
                    setattr(existing, f, v)
            if token_verified:
                existing.oidc_acr = member.oidc_acr
            for k in attested:
                if not any(x.kid == k.kid for x in existing.keys):
                    existing.keys.append(k)
            return {"result": {"joined": True, "as": uri}}
        # A participant that signs its calls joins with a key: without one it
        # would hold a name it could never sign for. The key may be its own,
        # or one an identity profile or an invitation binds.
        if self._requires_signatures(ws) and self._must_sign(ws, kind) and not member.keys:
            return {"error": rpc_error(
                E.PARAMS, f"{uri} must sign in this workspace, and joins with no key")}
        if made:
            self._add_workspace(ws)
        ws.members[uri] = member
        if invitation is not None:
            del ws.invitations[uri]
        return {"result": {"joined": True, "as": uri}}

    def _op_workspace_invite(self, p: dict) -> dict:
        """SPECIFICATION 6.3.3: an admin invites a participant. For a URI that
        is not a member it records an invitation, which the invitee's join
        takes up. For a member that holds no key in force it registers the
        invitation's keys: the way back for a member whose keys were all
        revoked, or who joined a workspace that requires signatures with
        none."""
        miss = _missing(p, ["workspace", "from", "uri", "type"])
        if miss:
            return {"error": rpc_error(E.PARAMS, f"Missing field: {miss}")}
        for f in ("uri", "type"):
            if not isinstance(p[f], str):
                return {"error": rpc_error(E.PARAMS, f"{f} must be a string")}
        bad = params_error(p, [
            ("role", "string"), ("jwks", "object"), ("jwks.keys", "list"), ("oidc_sub", "string"),
        ])
        if bad:
            return bad
        ws = self.workspaces.get(p["workspace"])
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        not_member = self._require_member(ws, p["from"])
        if not_member:
            return not_member
        if _raw_member(ws, p["from"]).role != "admin":
            return {"error": rpc_error(E.NOT_AUTHORISED, "workspace.invite requires the admin role")}
        uri = p["uri"]
        kind = p["type"]
        named = _participant_error(kind, uri, "uri")
        if named:
            return named
        role = p.get("role") or None
        if role == "admin" and kind not in _ADMIN_TYPES:
            return {"error": rpc_error(E.PARAMS, "The admin role is held by a human or a service")}
        sub = p.get("oidc_sub")
        if sub is not None and self.options.verify_oidc_token is None:
            return {"error": rpc_error(
                E.PARAMS, "oidc_sub names a token subject, and this Coordinator has no token verifier")}
        pinned = None
        if p.get("jwks") is not None:
            pinned = _signing_keys(p["jwks"])
            if not pinned:
                return {"error": rpc_error(E.PARAMS, "jwks names no key: each key needs a kid")}
        now = self.now_iso()
        member = ws.members.get(uri)
        if member is not None:
            # A member's keys otherwise change through participant.rotate_key,
            # which a key it holds signs.
            if any(_in_force(k, now) for k in member.keys):
                return {"error": rpc_error(
                    E.PARAMS, f"{uri} is a member and holds a key in force; its keys change "
                    "through participant.rotate_key")}
            if pinned is None:
                return {"error": rpc_error(
                    E.PARAMS, f"{uri} is a member; an invitation for a member registers the keys "
                    "its jwks names")}
            # Where a token verifier is configured, a person's key is the one
            # their token binds, and they register a new one by joining again
            # with it. Registered here, it would be a key the admin chose, and
            # the admin could revoke a person's keys and sign as them.
            if ((self.options.verify_oidc_token is not None
                    and (member.type == "human" or member.oidc_sub is not None))
                    or (self.options.verify_vc is not None
                        and (member.type == "human" or member.vc_holder is not None))):
                return {"error": rpc_error(
                    E.PARAMS, f"{uri} registers a new key by joining again with a token that "
                    "binds it; an invitation does not register a person's keys here")}
            if ((role is not None and role != member.role)
                    or (sub is not None and sub != member.oidc_sub)):
                return {"error": rpc_error(
                    E.PARAMS, f"{uri} is a member; an invitation for a member registers keys "
                    "and changes nothing else")}
            # A kid names one key for good: a signature naming a revoked kid is
            # refused whatever key holds it now.
            reused = next((j for j in pinned if any(k.kid == j["kid"] for k in member.keys)), None)
            if reused is not None:
                return {"error": rpc_error(
                    E.PARAMS, f"Key {reused['kid']} was {uri}'s; a new key takes a new kid")}
            for j in pinned:
                member.keys.append(KeyRecord(jwk=j, kid=j["kid"], valid_from=now))
            return {"result": {"invited": True, "uri": uri, "registered": [j["kid"] for j in pinned]}}
        # Where a token verifier is configured, a person's keys arrive with the
        # token or presentation they join with. Pinned here, they would be keys
        # the admin chose, which a join whose token binds no key would take
        # up, and the admin could sign as the person.
        if (pinned is not None and (kind == "human" or sub is not None)
                and (self.options.verify_oidc_token is not None or self.options.verify_vc is not None)):
            return {"error": rpc_error(
                E.PARAMS, f"{uri} joins with a token that binds its key; an invitation does not "
                "register a person's keys here")}
        ws.invitations[uri] = Invitation(
            uri=uri, type=kind, invited_by=p["from"], invited_at=now, role=role,
            jwks={"keys": pinned} if pinned is not None else None, oidc_sub=sub)
        return {"result": {"invited": True, "uri": uri}}

    def _op_participant_leave(self, p: dict) -> dict:
        # Self-removal: pops the caller's own `from`. Under require_signatures
        # the signature binds `from`, so a member can only remove itself;
        # evicting another member is an admin operation, not this. Unsigned, a
        # spoofed `from` is the transport's responsibility.
        ws = self.workspaces.get(p.get("workspace", ""))
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        # A member that left and joined again would come back unpaused, so a
        # paused member stays until another member lifts its pause.
        member = _raw_member(ws, p.get("from"))
        if member is not None and member.paused:
            return {"error": rpc_error(
                E.NOT_AUTHORISED, "A paused participant does not leave until another member lifts its pause")}
        ws.members.pop(p.get("from", ""), None)
        return {"result": {"left": True}}

    def _op_task_create(self, p: dict) -> dict:
        miss = _missing(p, ["workspace", "from", "kind", "input"])
        if miss:
            return {"error": rpc_error(E.PARAMS, f"Missing field: {miss}")}
        # A param of the wrong type is refused, so both references read the
        # same task from the same call.
        bad = params_error(p, [
            ("assignee", "string"), ("to", "string"), ("mode", "string"),
            ("review_required", "boolean"), ("routing_hints", "object"),
            ("idempotency_key", "string"),
        ])
        if bad:
            return bad
        # The TypeScript coordinator keeps the keys in a plain object, where
        # these names belong to the object itself, so both refuse them.
        if p.get("idempotency_key") in _RESERVED_KEYS:
            return {"error": rpc_error(
                E.PARAMS, "idempotency_key must not be __proto__, constructor or prototype")}
        ws = self.workspaces.get(p["workspace"])
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        not_member = self._require_member(ws, p.get("from"))
        if not_member:
            return not_member
        assignee = p.get("assignee") or p.get("to")
        if not isinstance(assignee, str) or assignee not in ws.members:
            return {"error": rpc_error(E.PARAMS, "Assignee not in workspace")}

        # control/1.0 participant-paused check
        if ws.members[assignee].paused:
            return {"error": rpc_error(E.CONTROL_WORKSPACE_PAUSED,
                                       f"Assignee {assignee} is paused")}

        # modes/1.0 ceiling check.
        requested_mode = p.get("mode") or ws.mode
        if not mode_le(requested_mode, ws.mode_ceiling):
            return {"error": rpc_error(
                E.MODE_CEILING_EXCEEDED,
                f"Requested mode {requested_mode} exceeds ceiling {ws.mode_ceiling}",
            )}

        task_id = self.ids.task_id()
        now = self.now_iso()
        task = Task(
            id=task_id,
            kind=p["kind"],
            state="created",
            assignee=assignee,
            delegator=p["from"],
            input=p["input"],
            created_at=now,
            updated_at=now,
            deadline=p.get("deadline"),
            mode=requested_mode,
            routing_hints=p.get("routing_hints"),
            history=[TaskHistoryEntry(ts=now, from_=p["from"], state="created")],
        )

        # modes/1.0: trial mode forces review.required -- but only when the
        # workspace has opted into modes/1.0. mode is inert without the profile,
        # so a plain core/review workspace does not inherit forced review.
        if ws.has_profile("modes") and task.mode == "trial":
            task.review_required = True
        elif "review_required" in p:
            task.review_required = bool(p["review_required"])

        ws.tasks[task_id] = task
        key = p.get("idempotency_key")
        if isinstance(key, str):
            ws.idempotency_keys[key] = task_id
            if len(ws.idempotency_keys) > _MAX_IDEMPOTENCY_KEYS:
                del ws.idempotency_keys[next(iter(ws.idempotency_keys))]
        return {"result": {"task_id": task_id, "state": "created"}}

    def _op_task_update(self, p: dict) -> dict:
        ws = self.workspaces.get(p.get("workspace", ""))
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        not_member = self._require_member(ws, p.get("from"))
        if not_member:
            return not_member
        task = lookup(ws.tasks, p.get("task_id"))
        if not task:
            return {"error": rpc_error(E.PARAMS, "Unknown task")}
        # The state is required, and is written into the refusal below.
        if not isinstance(p.get("state"), str):
            return {"error": rpc_error(E.PARAMS, "state must be a string")}
        new_state = p.get("state")
        # Pausing and resuming both belong to control/1.0. A Core-only
        # workspace that could pause through task.update would hold a task
        # nothing it advertises can lift. A review opens with review.request,
        # or with task.complete on a task that requires one: task.update into
        # review_requested would leave the task under review with no review
        # for a decision to act on.
        legal = {
            "created":          ["in_progress", "declined"],
            "in_progress":      ["in_progress", "completed", "declined"],
            "review_requested": ["in_progress"],
            "paused":           ["cancelled"],
        }
        if new_state not in legal.get(task.state, []):
            return {"error": rpc_error(
                E.PARAMS, f"Illegal transition {task.state} -> {new_state}"
            )}
        # task.complete opens a review, and does not complete, when one is
        # required or when the task holds a review already. task.update
        # reaches the same state by another route and carries no output, so
        # completing here would record a finished task with nothing reviewed
        # and no decision on the chain.
        if new_state == "completed" and _review_required(ws, task):
            return {"error": rpc_error(
                E.PARAMS,
                "This task requires review. Submit the output with task.complete, "
                "which opens the review; a reviewer decision completes it.")}
        if new_state == "completed" and task.review is not None:
            return {"error": rpc_error(
                E.PARAMS,
                "This task holds a review. Submit the output with task.complete, "
                "which opens a new round; a reviewer decision completes it.")}
        # A review once opened is decided. Its authors may take their work
        # back to revise it, and nobody else; and a task whose review no
        # decision has closed is not declined around it.
        if task.state == "review_requested" and p.get("from") not in task.review.authors:
            return {"error": rpc_error(
                E.NOT_AUTHORISED,
                "Only the authors of the work under review take it back from review")}
        if new_state == "cancelled" and p.get("from") in _open_required_review_authors(ws, task):
            return {"error": rpc_error(
                E.NOT_AUTHORISED,
                "The authors of the work under review cannot end its review without a decision")}
        if new_state == "declined" and (
                not any(d.get("kind") == "reject" for d in task.review.decisions)
                if task.review is not None else bool(_open_required_review_authors(ws, task))):
            return {"error": rpc_error(
                E.PARAMS,
                "This task holds a review no decision has closed; a reviewer's decision ends it")}
        task.state = new_state
        task.updated_at = self.now_iso()
        task.history.append(TaskHistoryEntry(
            ts=task.updated_at, from_=p.get("from", ""),
            state=new_state, note=p.get("progress_note"),
        ))
        return {"result": {"state": new_state}}

    def _op_task_complete(self, p: dict) -> dict:
        ws = self.workspaces.get(p.get("workspace", ""))
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        not_member = self._require_member(ws, p.get("from"))
        if not_member:
            return not_member
        task = lookup(ws.tasks, p.get("task_id"))
        if not task:
            return {"error": rpc_error(E.PARAMS, "Unknown task")}
        # Completion is only legal from an active, non-terminal, non-paused
        # state. An allowlist (rather than a denylist of terminal states) is
        # safer: any state not explicitly listed -- cancelled, superseded,
        # paused, already-completed/declined, abstained, escalated -- is
        # rejected, so completion can neither revive a terminated task nor
        # bypass a pause.
        if task.state not in ("created", "in_progress"):
            return {"error": rpc_error(
                E.PARAMS, f"Cannot complete task in state: {task.state}")}
        confidence = p.get("confidence")
        if confidence is not None and (isinstance(confidence, bool)
                                       or not isinstance(confidence, (int, float, str))):
            return {"error": rpc_error(E.PARAMS, "confidence must be a number or a string")}
        # The assignee does the work a person reviews. A completion from
        # anyone else would make them an author, and an author cannot decide:
        # routing a task to a reviewer and completing it would take that
        # reviewer off the review.
        required = _review_required(ws, task)
        if required and p.get("from") != task.assignee:
            return {"error": rpc_error(
                E.NOT_AUTHORISED, "Only the assignee completes a task that requires review")}
        # review/1.0 S3.1: task.complete on a task whose review is required
        # opens a review implicitly and does not complete, and so does
        # task.complete on a task that holds a review from an earlier round.
        # The submitted output becomes the artefact under review; only a
        # reviewer decision (decide.*) then takes the task to completed.
        # Without this a review_required task would reach completed with
        # unreviewed output and no decision, and a task taken back from review
        # with task.update would complete with none.
        if required or task.review is not None:
            now = self.now_iso()
            # The assignee and the completer made this artefact, and neither
            # may decide it.
            authors = _authors_of(task, p.get("from"))
            note = _OPENED_ON_COMPLETE
            if task.review is None:
                # review_required means a person has to see this. The producer
                # must not satisfy its own review, and neither must another
                # agent: an agent approving an agent leaves a decide.approve on
                # the chain that reads as human oversight and is not. So the
                # implicit review is addressed to the human members other than
                # the authors, and where there are none the completion is
                # refused: a review no person can decide would never close.
                eligible = [uri for uri, member in dict.items(ws.members)
                            if uri not in authors and member.type == "human"]
                if not eligible:
                    return {"error": rpc_error(
                        E.NOT_AUTHORISED,
                        "Task requires review but has no eligible human reviewer "
                        "(needs a human member other than its assignee and completer)")}
                task.review = ReviewState(requested_at=now, requested_to=eligible,
                                          rule=ws.review_rule or "any_one_approves",
                                          authors=authors)
            else:
                # The task held a review already: it went back to in_progress
                # through task.update or a reject with request_revision, and
                # this completion submits a new artefact. A decision counts
                # only for the artefact it was made on, so the new round starts
                # with no decisions; kept, an approval of the earlier artefact
                # would count towards the rule on this one. The reviewers and
                # the rule stay, so a revision cannot weaken the review. The
                # authors of the new artefact are left out of the reviewers,
                # and so is any member who is not a person where the review is
                # required; where nobody remains, the completion is refused.
                # The authors of the earlier rounds stay authors of this one:
                # the work is theirs as well, whoever resubmits it.
                round_authors = _union_of(task.review.authors, authors)

                def keeps(r: str) -> bool:
                    member = _raw_member(ws, r)
                    return r not in round_authors and not (
                        required and member is not None and member.type != "human")
                reviewers = [r for r in task.review.requested_to if keeps(r)]
                if not reviewers:
                    return {"error": rpc_error(
                        E.NOT_AUTHORISED,
                        "No reviewer of this task remains once the authors of the work "
                        "are left out")}
                task.review = dataclasses.replace(
                    task.review, requested_at=now, requested_to=reviewers, decisions=[],
                    authors=round_authors, acknowledgements=[])
                note = _NEW_ROUND_ON_COMPLETE
            task.pending_artefact = p.get("output")
            task.state = "review_requested"
            task.updated_at = now
            task.history.append(TaskHistoryEntry(
                ts=now, from_=p.get("from", ""), state="review_requested", note=note,
            ))
            return {"result": {"state": "review_requested",
                               "review_id": task.id}}
        task.output = p.get("output")
        if "confidence" in p:
            # Stored as received (number or string) so it hashes deterministically;
            # the routing engine coerces to a number for its thresholds.
            task.confidence = p["confidence"]
        task.state = "completed"
        task.updated_at = self.now_iso()
        task.history.append(TaskHistoryEntry(
            ts=task.updated_at, from_=p.get("from", ""), state="completed",
        ))
        return {"result": {"state": "completed"}}

    def _op_audit_read(self, p: dict) -> dict:
        ws = self.workspaces.get(p.get("workspace", ""))
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        if self.options.require_read_membership:
            not_member = self._require_member(ws, p.get("from"))
            if not_member:
                return not_member
        # A range or filter that is not an object reads as none. Within one, a
        # field the read compares or slices by must be of its type.
        bad = params_error(p, [
            ("range.from_seq", "integer"), ("range.to_seq", "integer"),
            ("filter.method", "string"), ("filter.from", "string"), ("filter.task_id", "string"),
        ])
        if bad:
            return bad
        rng = p.get("range")
        rng = rng if isinstance(rng, dict) else {}
        flt = p.get("filter")
        flt = flt if isinstance(flt, dict) else {}
        outcome_filter = flt.get("outcome")
        if outcome_filter is not None and outcome_filter not in ("accepted", "refused"):
            return {"error": rpc_error(
                E.PARAMS, "filter.outcome must be 'accepted' or 'refused'")}
        from_seq = 0 if rng.get("from_seq") is None else int(rng["from_seq"])
        to_seq = len(ws.audit) if rng.get("to_seq") is None else int(rng["to_seq"])

        out: list[dict] = []
        for entry in ws.audit[from_seq:to_seq]:
            # Filters read the call an entry records, accepted or refused, and
            # `outcome` narrows to one kind.
            refused = entry.outcome is not None
            if outcome_filter == "accepted" and refused:
                continue
            if outcome_filter == "refused" and not refused:
                continue
            env = entry_call(entry) or {}
            ep = env.get("params") or {}
            if flt.get("method") and env.get("method") != flt["method"]:
                continue
            if flt.get("from") and ep.get("from") != flt["from"]:
                continue
            if flt.get("task_id"):
                task_id = ep.get("task_id")
                if env.get("method") == "whisper.answer":
                    whisper_id = ep.get("whisper_id")
                    prompt = ws.whispers.get(whisper_id) if isinstance(whisper_id, str) else None
                    task_id = prompt.task_id if prompt else None
                if task_id != flt["task_id"]:
                    continue
            out.append(entry.to_dict())
        # The next entry to read: the end of the range, or the end of the log
        # where the range runs past it, so a reader paging forward meets the
        # entries written after this read.
        return {"result": {"entries": out, "next_seq": min(to_seq, len(ws.audit))}}

    # ============================================================
    #   review/1.0 handlers
    # ============================================================

    def _op_review_request(self, p: dict) -> dict:
        miss = _missing(p, ["workspace", "from", "task_id", "artefact"])
        if miss:
            return {"error": rpc_error(E.PARAMS, f"Missing field: {miss}")}
        ws = self.workspaces.get(p["workspace"])
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        not_member = self._require_member(ws, p.get("from"))
        if not_member:
            return not_member
        task = lookup(ws.tasks, p["task_id"])
        if not task:
            return {"error": rpc_error(E.PARAMS, "Unknown task")}
        # task.complete refuses a task that has been stopped, so that a
        # completion "can neither revive a terminated task nor bypass a pause".
        # review.request creates the same kind of state and had no such check,
        # so it revived cancelled and superseded tasks and pulled paused ones
        # back into play. The same three states are refused here.
        #
        # `completed` is deliberately still allowed. Requesting review on a
        # completed task is how every framework bridge submits a draft, and
        # 0.2.12's `review_required` is the route away from that, not this.
        if task.state in _REVIEW_REQUEST_STOPPED:
            return {"error": rpc_error(
                E.NOT_REVIEWABLE,
                f"Cannot request review for task in state: {task.state}")}
        reviewers = p.get("to")
        if reviewers is not None and not isinstance(reviewers, str) and not (
                isinstance(reviewers, list) and all(isinstance(r, str) for r in reviewers)):
            return {"error": rpc_error(E.PARAMS, "to must be a string or a list of strings")}
        if isinstance(reviewers, str):
            reviewers = [reviewers]
        if not reviewers:
            return {"error": rpc_error(E.PARAMS, "review.request needs 'to'")}
        bad = params_error(p, [("rule", "string")])
        if bad:
            return bad
        now = self.now_iso()
        declared_rule = p.get("rule")
        rule = declared_rule or "any_one_approves"
        if not _review_rule_supported(rule):
            return {"error": rpc_error(
                E.PARAMS,
                f"Unsupported review rule {json_text(rule)}: "
                "review/1.0 supports "
                "any_one_approves, all_approve, quorum:<n>")}

        # A review already open on this task. Replacing the artefact underneath
        # it would let a reviewer decide on content they never saw, and would
        # discard decisions already cast while their envelopes remain in the
        # audit log, so a quorum could appear to have been assembled across two
        # different artefacts. Re-requesting the *same* artefact is a different
        # thing: it widens the reviewer set on an open review, which is
        # legitimate and preserves what has been decided so far.
        open_review = task.review if task.state == "review_requested" else None
        if open_review is not None:
            if content_hash(task.pending_artefact) != content_hash(p["artefact"]):
                return {"error": rpc_error(
                    E.REVIEW_ALREADY_OPEN,
                    "A review is already open on this task with different content. "
                    "Decide, abstain, escalate or cancel it before requesting "
                    "review of a new artefact.",
                )}
            # Only a rule the caller actually supplied can be a change.
            # Comparing the resolved default instead refused the documented
            # widen-the-reviewer-set request whenever `rule` was omitted.
            if declared_rule is not None and declared_rule != open_review.rule:
                return {"error": rpc_error(
                    E.REVIEW_ALREADY_OPEN,
                    f"Cannot change the decision rule of an open review "
                    f"(currently {open_review.rule})",
                )}

        # Where a person's review is required, the work's authors do not
        # choose who reviews it, and the reviewers a request names, opening
        # the review or widening it, are people other than the authors, each
        # named. Without this the agent whose work a required review holds
        # could add itself, or a person it joined, or any member at all
        # through a broadcast scope, and approve its own work. An explicit
        # review of a task that does not require one keeps its own `to`, so a
        # deliberate agent-reviews-agent flow is still available.
        authors = (open_review.authors if open_review is not None
                   else _union_of(task.review.authors if task.review is not None else [],
                                  _authors_of(task, _last_completer(task))))
        if _review_required(ws, task):
            # The review a task requires opens on task.complete, from the
            # assignee, under the workspace's rule. Opened here, by anyone, it
            # would take an artefact, a rule and reviewers its requester
            # chose, and a requester who is not an author could approve work
            # it supplied itself, or reopen a task a review already completed.
            if open_review is None:
                return {"error": rpc_error(
                    E.NOT_AUTHORISED,
                    "This task requires a person's review, which opens on task.complete; "
                    "review.request only adds reviewers to it once it is open")}
            if p.get("from") in authors:
                return {"error": rpc_error(
                    E.NOT_AUTHORISED, "The author of the work under review cannot request its review")}
            scope = next((r for r in reviewers if _is_scope(r)), None)
            if scope is not None:
                return {"error": rpc_error(
                    E.PARAMS,
                    f"A review that requires a person names its reviewers, and {scope} is an "
                    "address scope")}
            for r in reviewers:
                if r in authors:
                    return {"error": rpc_error(
                        E.NOT_AUTHORISED,
                        f"The author of the work under review cannot review it: {r}")}
                member = _raw_member(ws, r)
                if member is not None and member.type != "human":
                    return {"error": rpc_error(
                        E.NOT_AUTHORISED,
                        f"This task requires a person's review: {r} is not a human member")}

        if open_review is not None:
            for r in reviewers:
                if r not in open_review.requested_to:
                    open_review.requested_to.append(r)
            if p.get("deadline") is not None:
                open_review.deadline = p["deadline"]
            task.updated_at = now
            return {"result": {
                "state": "review_requested",
                "review_id": task.id,
                "amended": True,
                "requested_to": list(open_review.requested_to),
            }}

        task.state = "review_requested"
        task.updated_at = now
        task.review = ReviewState(
            requested_at=now,
            requested_to=list(reviewers),
            rule=rule,
            deadline=p.get("deadline"),
            authors=authors,
        )
        task.history.append(TaskHistoryEntry(
            ts=now, from_=p["from"], state="review_requested",
        ))
        task.pending_artefact = p["artefact"]
        return {"result": {"state": "review_requested", "review_id": task.id}}

    def _op_review_acknowledge(self, p: dict) -> dict:
        """review/1.0 S3.6: a reviewer says they have begun the open review.

        Only someone who may decide it can, so the log's record of who took a
        review up, and when, names reviewers alone. It changes nothing else:
        the review stays open and the acknowledgement counts towards no rule.
        """
        miss = _missing(p, ["workspace", "from", "task_id"])
        if miss:
            return {"error": rpc_error(E.PARAMS, f"Missing field: {miss}")}
        ws = self.workspaces.get(p["workspace"])
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        not_member = self._require_member(ws, p.get("from"))
        if not_member:
            return not_member
        task = lookup(ws.tasks, p["task_id"])
        if not task:
            return {"error": rpc_error(E.PARAMS, "Unknown task")}
        if task.state != "review_requested":
            return {"error": rpc_error(
                E.NOT_REVIEWABLE, f"Task not awaiting review: {task.state}"
            )}
        not_reviewer = self._require_reviewer(ws, task, p.get("from"))
        if not_reviewer:
            return not_reviewer
        review = task.review
        assert review is not None
        if any(a.get("reviewer") == p["from"] for a in review.acknowledgements):
            return {"error": rpc_error(E.PARAMS, "Already acknowledged in this round")}
        review.acknowledgements.append({"reviewer": p["from"], "ts": self.now_iso()})
        return {"result": {"acknowledged": True, "requested_at": review.requested_at}}

    def _check_artefact_digest(self, p: dict, task: "Task") -> "dict | None":
        """Verify an optional ``approved_artefact_digest`` (review/1.0 S3.2).

        The digest sits in params, so it is inside whatever the envelope
        signature covers: the decision then attests the content the reviewer
        saw rather than a task reference, and a relying party can check it
        without trusting the Coordinator that produced it. Absent, behaviour is
        exactly as before.

        On mismatch nothing is recorded and no state changes, so a client that
        computes the wrong digest refuses only its own decision.
        """
        declared = p.get("approved_artefact_digest")
        if declared is None:
            return None
        if not isinstance(declared, str):
            return {"error": rpc_error(
                E.PARAMS, "approved_artefact_digest must be a string")}
        if task.pending_artefact is None:
            return {"error": rpc_error(
                E.PARAMS, "No artefact under review to bind a digest to")}
        actual = content_hash(task.pending_artefact)
        if declared != actual:
            return {"error": rpc_error(
                E.SIG_ARTEFACT_DIGEST_MISMATCH,
                "approved_artefact_digest does not match the artefact under review",
                {"declared": declared, "actual": actual},
            )}
        return None

    def _op_decide(self, p: dict, kind: str) -> dict:
        miss = _missing(p, ["workspace", "from", "task_id"])
        if miss:
            return {"error": rpc_error(E.PARAMS, f"Missing field: {miss}")}
        bad = params_error(p, [("tags", "strings"), ("request_revision", "boolean")])
        if bad:
            return bad
        ws = self.workspaces.get(p["workspace"])
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        not_member = self._require_member(ws, p.get("from"))
        if not_member:
            return not_member
        task = lookup(ws.tasks, p["task_id"])
        if not task:
            return {"error": rpc_error(E.PARAMS, "Unknown task")}
        if task.state != "review_requested":
            return {"error": rpc_error(
                E.NOT_REVIEWABLE, f"Task not awaiting review: {task.state}"
            )}
        not_reviewer = self._require_reviewer(ws, task, p.get("from"))
        if not_reviewer:
            return not_reviewer
        digest_error = self._check_artefact_digest(p, task)
        if digest_error:
            return digest_error
        now = self.now_iso()
        assert task.review is not None
        task.review.decisions.append({
            "reviewer": p["from"],
            "kind": kind,
            "ts": now,
            "comment": p.get("comment"),
            "tags": p.get("tags") or [],
        })
        if kind == "approve":
            # Complete only when the review rule's approval predicate is met;
            # otherwise the approval is recorded and the task stays
            # review_requested awaiting the rest.
            if _review_satisfied(task.review):
                task.output = task.pending_artefact
                task.state = "completed"
        else:
            task.state = "in_progress" if p.get("request_revision") else "declined"
        task.updated_at = now
        task.history.append(TaskHistoryEntry(
            ts=now, from_=p["from"], state=task.state,
        ))
        return {"result": {"state": task.state}}

    def _op_decide_override(self, p: dict) -> dict:
        miss = _missing(p, ["workspace", "from", "task_id", "diff", "rationale"])
        if miss:
            return {"error": rpc_error(E.PARAMS, f"Missing field: {miss}")}
        bad = params_error(p, [("tags", "strings"), ("policy_refs", "strings")])
        if bad:
            return bad
        ws = self.workspaces.get(p["workspace"])
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        not_member = self._require_member(ws, p.get("from"))
        if not_member:
            return not_member
        task = lookup(ws.tasks, p["task_id"])
        if not task:
            return {"error": rpc_error(E.PARAMS, "Unknown task")}
        if task.state != "review_requested":
            return {"error": rpc_error(
                E.NOT_REVIEWABLE, f"Task not awaiting review: {task.state}"
            )}
        not_reviewer = self._require_reviewer(ws, task, p.get("from"))
        if not_reviewer:
            return not_reviewer
        digest_error = self._check_artefact_digest(p, task)
        if digest_error:
            return digest_error
        # The override is of the artefact under review; use the coordinator's
        # record, not a caller-supplied "before" that could be fabricated.
        base = task.pending_artefact
        if base is None:
            return {"error": rpc_error(E.PARAMS, "No base artefact for override")}
        try:
            applied = apply_json_patch(
                base, p["diff"], max_string_bytes=self.options.max_envelope_bytes)
        except PatchError as exc:
            return {"error": rpc_error(E.PATCH_FAILED, str(exc))}

        now = self.now_iso()
        artefact_id = self.ids.artefact_id()
        override = OverrideArtefact(
            id=artefact_id,
            task_id=task.id,
            reviewer=p["from"],
            based_on_artefact=base,
            diff=p["diff"],
            result=applied,
            rationale=p["rationale"],
            tags=list(p.get("tags") or []),
            policy_refs=list(p.get("policy_refs") or []),
            ts=now,
            logical_id=p.get("logical_id"),
            instance_id=p.get("instance_id"),
            intent_preserved=p.get("intent_preserved"),
        )
        ws.overrides[artefact_id] = override

        assert task.review is not None
        task.review.decisions.append({
            "reviewer": p["from"],
            "kind": "override",
            "ts": now,
            "tags": override.tags,
            "override_artefact_id": artefact_id,
        })
        task.output = applied
        task.state = "completed"
        task.updated_at = now
        task.history.append(TaskHistoryEntry(
            ts=now, from_=p["from"], state="completed", note="override applied",
        ))
        return {"result": {
            "state": "completed",
            "override_artefact_id": artefact_id,
            "applied": applied,
        }}

    def _op_abstain_declare(self, p: dict) -> dict:
        miss = _missing(p, ["workspace", "from", "task_id", "reason"])
        if miss:
            return {"error": rpc_error(E.PARAMS, f"Missing field: {miss}")}
        bad = params_error(p, [("category", "string")])
        if bad:
            return bad
        ws = self.workspaces.get(p["workspace"])
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        not_member = self._require_member(ws, p.get("from"))
        if not_member:
            return not_member
        task = lookup(ws.tasks, p["task_id"])
        if not task:
            return {"error": rpc_error(E.PARAMS, "Unknown task")}
        if task.state != "review_requested":
            return {"error": rpc_error(
                E.NOT_REVIEWABLE, f"Task not awaiting review: {task.state}"
            )}
        not_reviewer = self._require_reviewer(ws, task, p.get("from"))
        if not_reviewer:
            return not_reviewer
        now = self.now_iso()
        assert task.review is not None
        task.review.decisions.append({
            "reviewer": p["from"],
            "kind": "abstain",
            "ts": now,
            "comment": p["reason"],
            "abstain_category": p.get("category") or "other",
        })
        task.state = "abstained"
        task.updated_at = now
        task.history.append(TaskHistoryEntry(
            ts=now, from_=p["from"], state="abstained", note=p["reason"],
        ))
        return {"result": {"state": "abstained"}}

    def _op_escalate_raise(self, p: dict) -> dict:
        miss = _missing(p, ["workspace", "from", "original_task_id", "new_task"])
        if miss:
            return {"error": rpc_error(E.PARAMS, f"Missing field: {miss}")}
        ws = self.workspaces.get(p["workspace"])
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        not_member = self._require_member(ws, p.get("from"))
        if not_member:
            return not_member
        orig_id = p["original_task_id"]
        orig = ws.tasks.get(orig_id) if isinstance(orig_id, str) else None
        if not orig:
            return {"error": rpc_error(E.PARAMS, "Unknown original task")}
        if orig.state in ("completed", "cancelled", "superseded"):
            return {"error": rpc_error(
                E.PARAMS, f"Cannot escalate a terminal task: {orig.state}")}
        nt = p["new_task"]
        if not isinstance(nt, dict):
            return {"error": rpc_error(E.PARAMS, "new_task must be an object")}
        bad = params_error(p, [("new_task.kind", "string")])
        if bad:
            return bad
        assignee = nt.get("assignee")
        if not isinstance(assignee, str) or assignee not in ws.members:
            return {"error": rpc_error(E.PARAMS,
                                       "Escalation assignee not in workspace")}
        named = nt.get("mode")
        if named is not None and not isinstance(named, str):
            return {"error": rpc_error(E.PARAMS, "new_task.mode must be a string")}
        # The successor is a created task, so it meets the checks task.create
        # and control.supersede apply: its assignee must not be paused, and its
        # mode, the one new_task names or else the original's, must be within
        # the ceiling in force now. Naming a lower mode is how work is
        # escalated after the ceiling has been lowered.
        if ws.members[assignee].paused:
            return {"error": rpc_error(E.CONTROL_WORKSPACE_PAUSED,
                                       f"Assignee {assignee} is paused")}
        mode = named or orig.mode
        if not mode_le(mode, ws.mode_ceiling):
            return {"error": rpc_error(
                E.MODE_CEILING_EXCEEDED,
                f"Requested mode {mode} exceeds ceiling {ws.mode_ceiling}",
            )}

        now = self.now_iso()
        new_id = self.ids.task_id()
        new_task = Task(
            id=new_id,
            kind=nt.get("kind") or orig.kind,
            state="created",
            assignee=assignee,
            delegator=p["from"],
            input=nt["input"] if nt.get("input") is not None else {},
            created_at=now,
            updated_at=now,
            supersedes=orig.id,
            mode=mode,
            history=[TaskHistoryEntry(
                ts=now, from_=p["from"], state="created",
                note=f"escalated from {orig.id}",
            )],
        )
        # Escalating hands the work on and keeps its review. The successor
        # requires review when the original did or held a review, since a
        # review once opened is decided, and a trial task forces review under
        # modes/1.0 as it does at task.create.
        if (orig.review_required or orig.review is not None
                or (ws.has_profile("modes") and new_task.mode == "trial")):
            new_task.review_required = True
        # Whoever made the work it carries on is an author of its review too,
        # so handing the work on does not make its author a reviewer of it.
        new_task.prior_authors = _successor_authors(orig, p.get("from"))
        ws.tasks[new_id] = new_task
        orig.state = "escalated"
        orig.updated_at = now
        orig.superseded_by = new_id
        orig.history.append(TaskHistoryEntry(
            ts=now, from_=p["from"], state="escalated", note=f"-> {new_id}",
        ))
        return {"result": {"new_task_id": new_id, "escalated_from": orig.id}}
