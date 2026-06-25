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

from .canonical import ZERO_HASH, canonicalize, sha256_hex
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


# Methods classified as privileged for step-up auth (identity-oidc/1.0 S4
# and control/1.0 S5). Compared against an OIDC auth_time freshness window.
PRIVILEGED_METHODS = frozenset({
    "control.pause", "control.resume", "control.cancel", "control.supersede",
    "control.snapshot", "control.rollback", "control.set_mode_ceiling",
    "workspace.set_profiles",
    "participant.rotate_key", "participant.revoke_key",
})


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


def _link_hash(envelope: dict, prev: str) -> str:
    """Chain link: sha256( JCS(envelope) || prev_hash )."""
    return sha256_hex(canonicalize(envelope) + prev.encode("utf-8"))


def _rehydrate_workspace(data: dict) -> "Workspace":
    """Reconstruct a Workspace dataclass tree from a snapshot dict.

    Mirror of `_snapshot_workspace`; the two should round-trip cleanly.
    """
    from .types import (
        Workspace, Member, Task, TaskHistoryEntry, KeyRecord,
        WhisperPrompt, Deliberation, Handoff, HandoffTask, AuditEntry,
    )

    def _opt(cls, d):
        return cls(**d) if d is not None else None

    members = {
        k: Member(**v) for k, v in (data.get("members") or {}).items()
    }
    # Member.keys can be a dict of KeyRecord; rehydrate those too.
    for m in members.values():
        if isinstance(m.keys, dict):
            m.keys = {k: KeyRecord(**v) if isinstance(v, dict) else v
                      for k, v in m.keys.items()}

    tasks = {}
    for k, v in (data.get("tasks") or {}).items():
        v = dict(v)
        v["history"] = [TaskHistoryEntry(**h) for h in v.get("history", [])]
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

    # -- public dispatch -----------------------------------------------

    def dispatch(self, envelope: dict) -> dict:
        """Process one JSON-RPC envelope; return the response."""
        if not is_valid_envelope(envelope):
            return make_response(
                envelope.get("id") if isinstance(envelope, dict) else None,
                error=rpc_error(E.REQUEST, "Invalid JSON-RPC 2.0 request"),
            )

        method = envelope.get("method")
        env_id = envelope.get("id")
        params = envelope.get("params") or {}

        if not isinstance(method, str):
            return make_response(env_id, error=rpc_error(E.REQUEST, "Missing method"))

        # security-signed/1.0: verify top-level `sig` field if required.
        if self.options.require_signatures and method != "participant.join":
            sig_err = self._verify_signature(envelope)
            if sig_err:
                return make_response(env_id, error=sig_err)

        # identity-oidc/1.0: step-up freshness check on privileged methods.
        if self.options.enforce_step_up and method in PRIVILEGED_METHODS:
            stale = self._check_step_up(params)
            if stale:
                return make_response(env_id, error=stale)

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

        handler = self._handlers.get(method)
        if handler is None:
            return make_response(
                env_id, error=rpc_error(E.METHOD, f"Unknown method: {method}")
            )

        try:
            out = handler(params)
        except Exception as exc:
            return make_response(
                env_id, error=rpc_error(E.INTERNAL, f"Internal error: {exc}")
            )

        if "error" in out:
            return make_response(env_id, error=out["error"])

        # Record audit on successful operations that name a workspace
        ws_id = params.get("workspace")
        if isinstance(ws_id, str):
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
        if not sender or not ws_id:
            return None  # Cannot verify; let normal handler reject.

        ws = self.workspaces.get(ws_id)
        if not ws:
            return None
        member = ws.members.get(sender)
        if not member:
            return rpc_error(E.SIG_KEY_NOT_FOUND, f"No member: {sender}")
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
        if not member or member.oidc_auth_time is None:
            return None
        now_unix = int(_dt.datetime.now(_dt.timezone.utc).timestamp())
        age = now_unix - member.oidc_auth_time
        if age > ws.step_up_window_sec:
            return rpc_error(E.OIDC_STEP_UP_REQUIRED,
                             "Step-up authentication required",
                             {"window_sec": ws.step_up_window_sec,
                              "age_sec": age})
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
        out: dict[str, Any] = {
            "id": ws.id,
            "created": ws.created,
            "state": ws.state,
            "mode": ws.mode,
            "mode_ceiling": ws.mode_ceiling,
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
        miss = _missing(p, ["workspace", "profiles"])
        if miss:
            return {"error": rpc_error(E.PARAMS, f"Missing field: {miss}")}
        ws = self.workspaces.get(p["workspace"])
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
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

        # security-signed/1.0: register any JWKs supplied in the join envelope.
        jwks = p.get("jwks")
        if isinstance(jwks, dict):
            for j in jwks.get("keys", []) or []:
                if isinstance(j, dict) and j.get("kid"):
                    # Skip if we already pinned via cnf.jwk
                    if not any(k.kid == j["kid"] for k in member.keys):
                        member.keys.append(KeyRecord(
                            jwk=j, kid=j["kid"], valid_from=now,
                        ))

        ws.members[uri] = member
        return {"result": {"joined": True, "as": uri}}

    def _op_participant_leave(self, p: dict) -> dict:
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

        # modes/1.0: trial mode forces review.required
        if task.mode == "trial":
            task.review_required = True
        elif "review_required" in p:
            task.review_required = bool(p["review_required"])

        ws.tasks[task_id] = task
        return {"result": {"task_id": task_id, "state": "created"}}

    def _op_task_update(self, p: dict) -> dict:
        ws = self.workspaces.get(p.get("workspace", ""))
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        task = ws.tasks.get(p.get("task_id", ""))
        if not task:
            return {"error": rpc_error(E.PARAMS, "Unknown task")}
        new_state = p.get("state")
        legal = {
            "created":          ["in_progress", "declined", "paused"],
            "in_progress":      ["in_progress", "completed", "declined",
                                 "review_requested", "paused"],
            "review_requested": ["in_progress", "completed", "declined"],
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
        task = ws.tasks.get(p.get("task_id", ""))
        if not task:
            return {"error": rpc_error(E.PARAMS, "Unknown task")}
        if task.state in ("completed", "declined"):
            return {"error": rpc_error(E.PARAMS, f"Task is terminal: {task.state}")}
        task.output = p.get("output")
        if "confidence" in p:
            task.confidence = float(p["confidence"])
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
        task = ws.tasks.get(p["task_id"])
        if not task:
            return {"error": rpc_error(E.PARAMS, "Unknown task")}
        reviewers = p.get("to")
        if isinstance(reviewers, str):
            reviewers = [reviewers]
        if not reviewers:
            return {"error": rpc_error(E.PARAMS, "review.request needs 'to'")}
        now = self.now_iso()
        task.state = "review_requested"
        task.updated_at = now
        task.review = ReviewState(
            requested_at=now,
            requested_to=list(reviewers),
            rule=p.get("rule") or "any_one_approves",
            deadline=p.get("deadline"),
        )
        task.history.append(TaskHistoryEntry(
            ts=now, from_=p["from"], state="review_requested",
        ))
        task.pending_artefact = p["artefact"]
        return {"result": {"state": "review_requested", "review_id": task.id}}

    def _op_decide(self, p: dict, kind: str) -> dict:
        miss = _missing(p, ["workspace", "from", "task_id"])
        if miss:
            return {"error": rpc_error(E.PARAMS, f"Missing field: {miss}")}
        ws = self.workspaces.get(p["workspace"])
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        task = ws.tasks.get(p["task_id"])
        if not task:
            return {"error": rpc_error(E.PARAMS, "Unknown task")}
        if task.state != "review_requested":
            return {"error": rpc_error(
                E.NOT_REVIEWABLE, f"Task not awaiting review: {task.state}"
            )}
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
        ws = self.workspaces.get(p["workspace"])
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        task = ws.tasks.get(p["task_id"])
        if not task:
            return {"error": rpc_error(E.PARAMS, "Unknown task")}
        if task.state != "review_requested":
            return {"error": rpc_error(
                E.NOT_REVIEWABLE, f"Task not awaiting review: {task.state}"
            )}
        base = p.get("based_on_artefact", task.pending_artefact)
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
        task = ws.tasks.get(p["task_id"])
        if not task:
            return {"error": rpc_error(E.PARAMS, "Unknown task")}
        if task.state != "review_requested":
            return {"error": rpc_error(
                E.NOT_REVIEWABLE, f"Task not awaiting review: {task.state}"
            )}
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
        orig = ws.tasks.get(p["original_task_id"])
        if not orig:
            return {"error": rpc_error(E.PARAMS, "Unknown original task")}
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
