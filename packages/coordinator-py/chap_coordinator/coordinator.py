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
import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Callable

from .canonical import ZERO_HASH, canonicalize, content_hash, sha256_hex
from .ids import IdFactory
from .jsonrpc import E, is_valid_envelope, make_response, rpc_error
from .patch import PatchError, apply_json_patch
from .types import (
    AuditEntry,
    Deliberation,
    Handoff,
    HandoffTask,
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
    "workspace.set_profiles",
    "participant.rotate_key", "participant.revoke_key",
})


# Read-only methods return workspace state but do not mutate it, so they are not
# recorded in the audit chain -- recording a read would grow and re-link it.
_READ_ONLY_METHODS = frozenset({
    "workspace.describe", "audit.read",
    "audit.verify_chain", "audit.verify_receipt",
})


# Cap on a workspace's task.create idempotency map. Older keys are evicted, so
# the dedup window is bounded rather than growing unbounded in a long-lived
# workspace; a redelivery beyond this many intervening creates is not deduped.
_MAX_IDEMPOTENCY_KEYS = 10_000


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
    """Reject envelopes lacking a verifiable signature (security-signed/1.0)."""

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
    object satisfying the `Store` Protocol to persist workspaces."""


# ============================================================
#   Helpers
# ============================================================

def _now_iso(clock_ms: int | None = None) -> str:
    if clock_ms is None:
        dt = _dt.datetime.now(_dt.timezone.utc)
    else:
        dt = _dt.datetime.fromtimestamp(clock_ms / 1000, tz=_dt.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _missing(params: dict, fields: list[str]) -> str | None:
    for f in fields:
        if f not in params:
            return f
    return None


def _tags_error(params: dict) -> dict | None:
    tags = params.get("tags")
    if tags is not None and (not isinstance(tags, list)
                             or not all(isinstance(t, str) for t in tags)):
        return {"error": rpc_error(E.PARAMS, "tags must be a list of strings")}
    return None


def _link_hash(envelope: dict, prev: str) -> str:
    """Chain link: sha256( JCS(envelope) || prev_hash )."""
    return sha256_hex(canonicalize(envelope) + prev.encode("utf-8"))


def _rehydrate_workspace(data: dict) -> "Workspace":
    """Reconstruct a Workspace dataclass tree from a snapshot dict.

    Mirror of `_snapshot_workspace`; the two should round-trip cleanly.
    """
    from .types import (
        Workspace, Member, Task, TaskHistoryEntry, KeyRecord, ReviewState,
        WhisperPrompt, Deliberation, Handoff, HandoffTask, AuditEntry,
    )

    def _opt(cls, d):
        return cls(**d) if d is not None else None

    members = {
        k: Member(**v) for k, v in (data.get("members") or {}).items()
    }
    for m in members.values():
        if isinstance(m.keys, list):
            m.keys = [KeyRecord(**k) if isinstance(k, dict) else k for k in m.keys]
        elif isinstance(m.keys, dict):
            m.keys = {k: KeyRecord(**v) if isinstance(v, dict) else v
                      for k, v in m.keys.items()}

    tasks = {}
    for k, v in (data.get("tasks") or {}).items():
        v = dict(v)
        v["history"] = [TaskHistoryEntry(**h) for h in v.get("history", [])]
        if isinstance(v.get("review"), dict):
            v["review"] = ReviewState(**v["review"])
        tasks[k] = Task(**v)

    whispers = {
        k: WhisperPrompt(**v) for k, v in (data.get("whispers") or {}).items()
    }
    deliberations = {
        k: Deliberation(**v)
        for k, v in (data.get("deliberations") or {}).items()
    }
    handoffs = {}
    for k, v in (data.get("handoffs") or {}).items():
        v = dict(v)
        v["tasks"] = [HandoffTask(**t) for t in v.get("tasks", [])]
        handoffs[k] = Handoff(**v)

    audit = [AuditEntry(**a) for a in (data.get("audit") or [])]

    ws_kwargs = {
        k: v for k, v in data.items()
        if k not in {"members", "tasks", "whispers", "deliberations",
                     "handoffs", "audit"}
    }
    ws = Workspace(**ws_kwargs)
    ws.members = members
    ws.tasks = tasks
    ws.whispers = whispers
    ws.deliberations = deliberations
    ws.handoffs = handoffs
    ws.audit = audit
    return ws


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
        self.workspaces: dict[str, Workspace] = {}
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

    def now_iso(self) -> str:
        if self._clock_ms is not None:
            self._clock_ms += 1000
            return _now_iso(self._clock_ms)
        return _now_iso()

    def add_audit_listener(self, fn: AuditListener) -> None:
        self._audit_listeners.append(fn)

    def get_workspace(self, workspace_id: str) -> Workspace | None:
        """Convenience: get a workspace by id, or ``None``."""
        return self.workspaces.get(workspace_id)

    # -- authorisation preconditions -----------------------------------

    @staticmethod
    def _require_member(ws: Workspace, sender: str | None) -> dict | None:
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
        if not sender or sender not in ws.members:
            return {"error": rpc_error(
                E.NOT_AUTHORISED,
                f"Not a workspace member: {sender}",
            )}
        return None

    @staticmethod
    def _require_reviewer(task: Task, sender: str | None) -> dict | None:
        """Assert that ``sender`` was addressed in the task's review.

        Applied to review-decision methods (decide.* and abstain.declare):
        to act on a review you must be one of the reviewers it was
        addressed to (the ``to`` set on review.request). Membership is the
        floor; this is the eligibility ceiling for decisions. Returns an
        error envelope fragment if not eligible, else ``None``.

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
        if review is None or not review.requested_to:
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
        if not is_valid_envelope(envelope):
            return make_response(
                envelope.get("id") if isinstance(envelope, dict) else None,
                error=rpc_error(E.REQUEST, "Invalid JSON-RPC 2.0 request"),
            )

        try:
            size = len(canonicalize(envelope))
        except (ValueError, TypeError):
            size = 0  # not canonicalisable; the ingress check below rejects it
        if size > self.options.max_envelope_bytes:
            return make_response(
                envelope.get("id"),
                error=rpc_error(
                    E.REQUEST,
                    f"Envelope exceeds max_envelope_bytes "
                    f"({size} > {self.options.max_envelope_bytes})"),
            )

        method = envelope.get("method")
        env_id = envelope.get("id")

        if not isinstance(method, str):
            return make_response(env_id, error=rpc_error(E.REQUEST, "Missing method"))

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

        # security-signed/1.0: verify top-level `sig` field if required.
        # participant.join and workspace.create are bootstrap operations that
        # run before any signing key is registered for the actor, so they
        # cannot be signature-verified; every other method must verify.
        if (self.options.require_signatures
                and method not in ("participant.join", "workspace.create")):
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

        # control/1.0 and deliberation/1.0: these operations are privileged
        # and MUST be performed by a workspace member. Control is the
        # governance "emergency brake"; deliberation open/close set the vote
        # parameters and finalize the tally. Without this floor a non-member
        # could resume a paused workspace, raise the mode ceiling, or open and
        # close a deliberation to finalize an outcome early. (The per-voter
        # eligibility check in deliberate.vote is separate and still applies.
        # Deployments needing a stricter role gate than membership layer it on
        # top via an identity-* profile or application check.)
        if (method.startswith("control.") or method.startswith("deliberate.")
                or method.startswith("handoff.")
                or method.startswith("whisper.")):
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

        handler = self._handlers.get(method)
        if handler is None:
            return make_response(
                env_id, error=rpc_error(E.METHOD, f"Unknown method: {method}")
            )

        try:
            canonicalize(envelope)
        except (ValueError, TypeError) as exc:
            return make_response(env_id, error=rpc_error(E.PARAMS, str(exc)))

        try:
            out = handler(params)
        except Exception as exc:
            # Keep the wire message generic; a handler exception may carry
            # internal detail that should not be disclosed to callers. The
            # specifics remain available to operators via the data field.
            return make_response(
                env_id,
                error=rpc_error(E.INTERNAL, "Internal error", data={"detail": str(exc)}),
            )

        if "error" in out:
            return make_response(env_id, error=out["error"])

        # Record audit on successful operations that name a workspace
        ws_id = params.get("workspace")
        if isinstance(ws_id, str) and method not in _READ_ONLY_METHODS:
            ws = self.workspaces.get(ws_id)
            if ws is not None:
                self._record_audit(ws, envelope)

        return make_response(env_id, result=out.get("result"))

    # -- audit recording -----------------------------------------------

    def _record_audit(self, ws: Workspace, envelope: dict) -> None:
        entry = AuditEntry(
            seq=len(ws.audit),
            arrived=self.now_iso(),
            envelope=copy.deepcopy(envelope),
        )
        if ws.chain_enabled or self.options.enable_chain:
            prev = ws.chain_head or ZERO_HASH
            entry.prev_hash = prev
            ws.chain_head = _link_hash(entry.envelope, prev)
        ws.audit.append(entry)
        for listener in self._audit_listeners:
            try:
                listener(ws.id, entry)
            except Exception:
                pass
        # Persist the updated workspace to the configured store, if any.
        # Failures are deliberately swallowed: the in-memory state is
        # authoritative within the process, and audit listeners are the
        # documented escape hatch for must-persist workloads.
        self._persist(ws)

    def _persist(self, ws: Workspace) -> None:
        if self.options.store is None:
            return
        try:
            record = self._snapshot_workspace(ws)
            self.options.store.save(record)
        except Exception:
            # Persistence failures must not break dispatch. See above.
            pass

    def _snapshot_workspace(self, ws: Workspace) -> "WorkspaceRecord":
        """JSON-safe snapshot of one workspace, plus a version counter."""
        from dataclasses import asdict
        from .storage.store import WorkspaceRecord
        data = asdict(ws)
        version = len(ws.audit)
        return WorkspaceRecord(
            id=ws.id, data=data, version=version, updated_at=self.now_iso(),
        )

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
            except Exception:
                # Skip records we can't parse; future schema migrations
                # should handle this explicitly.
                continue

    # -- signature verification (security-signed/1.0) ----------------

    def _verify_signature(self, envelope: dict) -> dict | None:
        sig = envelope.get("sig")
        if not sig or not isinstance(sig, str):
            return rpc_error(E.SIG_VERIFY_FAILED, "Missing top-level `sig` field")
        # Format: ed25519:<kid>:<b64>
        parts = sig.split(":", 2)
        if len(parts) != 3 or parts[0] != "ed25519":
            return rpc_error(E.SIG_VERIFY_FAILED, "Malformed signature tag")
        kid, sig_b64 = parts[1], parts[2]

        params = envelope.get("params") or {}
        sender = params.get("from") if isinstance(params, dict) else None
        ws_id = params.get("workspace") if isinstance(params, dict) else None
        ts = params.get("ts") or envelope.get("ts") or self.now_iso()
        # Fail closed: a signature is present (checked above) and
        # require_signatures is on, so it must verify. If we cannot resolve
        # the context needed to verify it, reject rather than skip -- a
        # signature we cannot check must never be treated as valid.
        if not sender or not ws_id:
            return rpc_error(E.SIG_VERIFY_FAILED,
                             "Cannot verify signature: missing from/workspace")

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

        try:
            import base64
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PublicKey,
            )
            raw_x = key.jwk.get("x", "")
            pad = "=" * (-len(raw_x) % 4)
            pub_bytes = base64.urlsafe_b64decode(raw_x + pad)
            pub = Ed25519PublicKey.from_public_bytes(pub_bytes)
            pub.verify(base64.b64decode(sig_b64), canonical)
            return None
        except Exception:
            return rpc_error(E.SIG_VERIFY_FAILED, "Signature failed verification")

    # -- step-up check (identity-oidc/1.0) ----------------------------

    def _check_step_up(self, params: dict) -> dict | None:
        ws_id = params.get("workspace")
        sender = params.get("from")
        if not ws_id or not sender:
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
        ws_id = p.get("workspace") or self.ids.workspace_id()
        if not isinstance(ws_id, str):
            return {"error": rpc_error(E.PARAMS, "workspace must be a string id")}
        if ws_id in self.workspaces:
            return {"error": rpc_error(E.PARAMS, f"workspace already exists: {ws_id}")}
        profiles = list(p.get("profiles") or self.options.default_profiles)
        ws = Workspace(
            id=ws_id,
            created=self.now_iso(),
            state="active",
            profiles=profiles,
            mode=p.get("mode") or "trial",
            mode_ceiling=p.get("mode_ceiling") or "production",
            routing_policy_uri=p.get("routing_policy_uri"),
            step_up_window_sec=int(p.get("step_up_window_sec") or 300),
            min_acr=p.get("min_acr"),
        )
        if "audit-scitt/1.0" in profiles or self.options.enable_chain:
            ws.chain_enabled = True
            ws.chain_head = ZERO_HASH
        self.workspaces[ws_id] = ws
        return {"result": {"workspace": ws_id, "created": ws.created}}

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
            "profiles": ws.profiles,
            "members": [m.to_dict() for m in ws.members.values()],
            "audit_count": len(ws.audit),
            "task_count": len(ws.tasks),
            "override_count": len(ws.overrides),
            "evidence_head": ws.chain_head,
        }
        if ws.routing_policy_uri:
            out["routing_policy_uri"] = ws.routing_policy_uri
        return {"result": out}

    def _op_workspace_set_profiles(self, p: dict) -> dict:
        miss = _missing(p, ["workspace", "from", "profiles"])
        if miss:
            return {"error": rpc_error(E.PARAMS, f"Missing field: {miss}")}
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
        new_profiles = list(p["profiles"])
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
        ws_id = p["workspace"]
        ws = self.workspaces.get(ws_id)
        if not ws:
            create = self._op_workspace_create({"workspace": ws_id})
            if "error" in create:
                return create
            ws = self.workspaces[ws_id]
        uri = p["from"]
        now = self.now_iso()

        member = Member(
            uri=uri,
            type=p["type"],
            role=p.get("role") or "participant",
            joined=now,
            display_name=p.get("display_name"),
            capabilities=p.get("capabilities"),
            scopes=p.get("scopes"),
        )

        # identity-oidc/1.0 binding
        if self.options.verify_oidc_token and isinstance(p.get("oidc_token"), str):
            claims = self.options.verify_oidc_token(p["oidc_token"])
            if claims is None:
                return {"error": rpc_error(E.OIDC_TOKEN_INVALID,
                                           "OIDC token invalid")}
            member.oidc_sub = claims.get("sub")
            at = claims.get("auth_time")
            if isinstance(at, (int, float)):
                member.oidc_auth_time = int(at)
            member.oidc_acr = claims.get("acr")
            # Pin the cnf.jwk if present (RFC 7800)
            cnf = claims.get("cnf") or {}
            cnf_jwk = cnf.get("jwk") if isinstance(cnf, dict) else None
            if isinstance(cnf_jwk, dict) and cnf_jwk.get("kid"):
                member.keys.append(KeyRecord(
                    jwk=cnf_jwk, kid=cnf_jwk["kid"], valid_from=now,
                ))

        # identity-vc/1.0 binding
        if self.options.verify_vc and isinstance(p.get("vc_presentation"), dict):
            subject = self.options.verify_vc(p["vc_presentation"])
            if subject is None:
                return {"error": rpc_error(E.VC_VP_INVALID,
                                           "VC presentation invalid")}
            member.vc_holder = subject.get("holder") or subject.get("id")
            # If the VP carried a proof-of-possession jwk, pin it
            vp_jwk = subject.get("cnf_jwk")
            if isinstance(vp_jwk, dict) and vp_jwk.get("kid"):
                member.keys.append(KeyRecord(
                    jwk=vp_jwk, kid=vp_jwk["kid"], valid_from=now,
                ))

        attested = list(member.keys)

        # Self-asserted JWKs are ignored for an identity-bound participant: the
        # verifier-pinned cnf.jwk / vp_jwk is the signing key (proof of possession).
        jwks = p.get("jwks")
        if isinstance(jwks, dict) and not attested:
            for j in jwks.get("keys", []) or []:
                if isinstance(j, dict) and j.get("kid"):
                    if not any(k.kid == j["kid"] for k in member.keys):
                        member.keys.append(KeyRecord(
                            jwk=j, kid=j["kid"], valid_from=now,
                        ))

        existing = ws.members.get(uri)
        if existing is not None:
            for f in ("oidc_sub", "oidc_auth_time", "oidc_acr", "vc_holder"):
                v = getattr(member, f)
                if v is not None:
                    setattr(existing, f, v)
            for k in attested:
                if not any(x.kid == k.kid for x in existing.keys):
                    existing.keys.append(k)
            return {"result": {"joined": True, "as": uri}}
        ws.members[uri] = member
        return {"result": {"joined": True, "as": uri}}

    def _op_participant_leave(self, p: dict) -> dict:
        # Self-removal: pops the caller's own `from`. Under require_signatures
        # the signature binds `from`, so a member can only remove itself;
        # evicting another member is an admin operation, not this. Unsigned, a
        # spoofed `from` is the transport's responsibility.
        ws = self.workspaces.get(p.get("workspace", ""))
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        ws.members.pop(p.get("from", ""), None)
        return {"result": {"left": True}}

    def _op_task_create(self, p: dict) -> dict:
        miss = _missing(p, ["workspace", "from", "kind", "input"])
        if miss:
            return {"error": rpc_error(E.PARAMS, f"Missing field: {miss}")}
        ws = self.workspaces.get(p["workspace"])
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        not_member = self._require_member(ws, p.get("from"))
        if not_member:
            return not_member
        assignee = p.get("assignee") or p.get("to")
        if not assignee or assignee not in ws.members:
            return {"error": rpc_error(E.PARAMS, "Assignee not in workspace")}

        # control/1.0 participant-paused check
        if ws.members[assignee].paused:
            return {"error": rpc_error(E.CONTROL_WORKSPACE_PAUSED,
                                       f"Assignee {assignee} is paused")}

        # modes/1.0 ceiling check
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
        task = ws.tasks.get(p.get("task_id", ""))
        if not task:
            return {"error": rpc_error(E.PARAMS, "Unknown task")}
        new_state = p.get("state")
        legal = {
            "created":          ["in_progress", "declined", "paused"],
            "in_progress":      ["in_progress", "completed", "declined",
                                 "review_requested", "paused"],
            "review_requested": ["in_progress"],
            "paused":           ["in_progress", "cancelled"],
        }
        if new_state not in legal.get(task.state, []):
            return {"error": rpc_error(
                E.PARAMS, f"Illegal transition {task.state} -> {new_state}"
            )}
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
        task = ws.tasks.get(p.get("task_id", ""))
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
        # review/1.0 S3.1: task.complete on a task whose review is required
        # opens a review implicitly rather than completing. The submitted output
        # becomes the artefact under review; only a reviewer decision (decide.*)
        # then takes the task to completed. Without this a review_required task
        # would reach completed with unreviewed output and no decision.
        if task.review_required:
            now = self.now_iso()
            task.pending_artefact = p.get("output")
            if task.review is None:
                # The producer must not satisfy its own review, so the implicit
                # review is addressed to the other members, excluding the caller
                # and the assignee. With nobody eligible there is no independent
                # reviewer, so refuse rather than open a review only its author
                # could approve. (An explicit review.request keeps its own `to`.)
                producer = p.get("from", "")
                eligible = [uri for uri in ws.members
                            if uri != producer and uri != task.assignee]
                if not eligible:
                    return {"error": rpc_error(
                        E.NOT_AUTHORISED,
                        "Task requires review but has no eligible reviewer "
                        "(needs a member other than its assignee and completer)")}
                task.review = ReviewState(requested_at=now,
                                          requested_to=list(eligible))
            task.state = "review_requested"
            task.updated_at = now
            task.history.append(TaskHistoryEntry(
                ts=now, from_=p.get("from", ""), state="review_requested",
                note="review required; opened on task.complete",
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
        rng = p.get("range") or {}
        flt = p.get("filter") or {}
        from_seq = int(rng.get("from_seq", 0))
        to_seq = int(rng.get("to_seq", len(ws.audit)))

        out: list[dict] = []
        for entry in ws.audit[from_seq:to_seq]:
            env = entry.envelope
            ep = env.get("params") or {}
            if flt.get("method") and env.get("method") != flt["method"]:
                continue
            if flt.get("from") and ep.get("from") != flt["from"]:
                continue
            if flt.get("task_id") and ep.get("task_id") != flt["task_id"]:
                continue
            out.append(entry.to_dict())
        return {"result": {"entries": out, "next_seq": to_seq}}

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
        task = ws.tasks.get(p["task_id"])
        if not task:
            return {"error": rpc_error(E.PARAMS, "Unknown task")}
        reviewers = p.get("to")
        if isinstance(reviewers, str):
            reviewers = [reviewers]
        if not reviewers:
            return {"error": rpc_error(E.PARAMS, "review.request needs 'to'")}
        now = self.now_iso()
        rule = p.get("rule") or "any_one_approves"

        # A review already open on this task. Replacing the artefact underneath
        # it would let a reviewer decide on content they never saw, and would
        # discard decisions already cast while their envelopes remain in the
        # audit log, so a quorum could appear to have been assembled across two
        # different artefacts. Re-requesting the *same* artefact is a different
        # thing: it widens the reviewer set on an open review, which is
        # legitimate and preserves what has been decided so far.
        if task.state == "review_requested" and task.review is not None:
            if content_hash(task.pending_artefact) != content_hash(p["artefact"]):
                return {"error": rpc_error(
                    E.REVIEW_ALREADY_OPEN,
                    "A review is already open on this task with different content. "
                    "Decide, abstain, escalate or cancel it before requesting "
                    "review of a new artefact.",
                )}
            if rule != task.review.rule:
                return {"error": rpc_error(
                    E.REVIEW_ALREADY_OPEN,
                    f"Cannot change the decision rule of an open review "
                    f"(currently {task.review.rule})",
                )}
            for r in reviewers:
                if r not in task.review.requested_to:
                    task.review.requested_to.append(r)
            if p.get("deadline") is not None:
                task.review.deadline = p["deadline"]
            task.updated_at = now
            return {"result": {
                "state": "review_requested",
                "review_id": task.id,
                "amended": True,
                "requested_to": list(task.review.requested_to),
            }}

        task.state = "review_requested"
        task.updated_at = now
        task.review = ReviewState(
            requested_at=now,
            requested_to=list(reviewers),
            rule=rule,
            deadline=p.get("deadline"),
        )
        task.history.append(TaskHistoryEntry(
            ts=now, from_=p["from"], state="review_requested",
        ))
        task.pending_artefact = p["artefact"]
        return {"result": {"state": "review_requested", "review_id": task.id}}

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
        bad_tags = _tags_error(p)
        if bad_tags:
            return bad_tags
        ws = self.workspaces.get(p["workspace"])
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        not_member = self._require_member(ws, p.get("from"))
        if not_member:
            return not_member
        task = ws.tasks.get(p["task_id"])
        if not task:
            return {"error": rpc_error(E.PARAMS, "Unknown task")}
        if task.state != "review_requested":
            return {"error": rpc_error(
                E.NOT_REVIEWABLE, f"Task not awaiting review: {task.state}"
            )}
        not_reviewer = self._require_reviewer(task, p.get("from"))
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
        bad_tags = _tags_error(p)
        if bad_tags:
            return bad_tags
        ws = self.workspaces.get(p["workspace"])
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        not_member = self._require_member(ws, p.get("from"))
        if not_member:
            return not_member
        task = ws.tasks.get(p["task_id"])
        if not task:
            return {"error": rpc_error(E.PARAMS, "Unknown task")}
        if task.state != "review_requested":
            return {"error": rpc_error(
                E.NOT_REVIEWABLE, f"Task not awaiting review: {task.state}"
            )}
        not_reviewer = self._require_reviewer(task, p.get("from"))
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
            applied = apply_json_patch(base, p["diff"])
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
        ws = self.workspaces.get(p["workspace"])
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        not_member = self._require_member(ws, p.get("from"))
        if not_member:
            return not_member
        task = ws.tasks.get(p["task_id"])
        if not task:
            return {"error": rpc_error(E.PARAMS, "Unknown task")}
        if task.state != "review_requested":
            return {"error": rpc_error(
                E.NOT_REVIEWABLE, f"Task not awaiting review: {task.state}"
            )}
        not_reviewer = self._require_reviewer(task, p.get("from"))
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
        orig = ws.tasks.get(p["original_task_id"])
        if not orig:
            return {"error": rpc_error(E.PARAMS, "Unknown original task")}
        if orig.state in ("completed", "cancelled", "superseded"):
            return {"error": rpc_error(
                E.PARAMS, f"Cannot escalate a terminal task: {orig.state}")}
        nt = p["new_task"]
        assignee = nt.get("assignee")
        if not assignee or assignee not in ws.members:
            return {"error": rpc_error(E.PARAMS,
                                       "Escalation assignee not in workspace")}

        now = self.now_iso()
        new_id = self.ids.task_id()
        ws.tasks[new_id] = Task(
            id=new_id,
            kind=nt.get("kind") or orig.kind,
            state="created",
            assignee=assignee,
            delegator=p["from"],
            input=nt.get("input") or {},
            created_at=now,
            updated_at=now,
            supersedes=orig.id,
            mode=orig.mode,
            history=[TaskHistoryEntry(
                ts=now, from_=p["from"], state="created",
                note=f"escalated from {orig.id}",
            )],
        )
        orig.state = "escalated"
        orig.updated_at = now
        orig.superseded_by = new_id
        orig.history.append(TaskHistoryEntry(
            ts=now, from_=p["from"], state="escalated", note=f"-> {new_id}",
        ))
        return {"result": {"new_task_id": new_id, "escalated_from": orig.id}}
