"""
chap_coordinator.jsonrpc

Error codes (JSON-RPC 2.0 standard + CHAP private range) and helpers.

Error code allocations come directly from each profile spec. See
profiles/*.md for the authoritative definitions.
"""
from __future__ import annotations

from typing import Any


class E:
    """Error code constants for the JSON-RPC wire."""

    # JSON-RPC 2.0 standard
    PARSE = -32700
    REQUEST = -32600
    METHOD = -32601
    PARAMS = -32602
    INTERNAL = -32603

    # review/1.0 profile
    NOT_REVIEWABLE = -32010
    NOT_AUTHORISED = -32011
    PATCH_FAILED = -32012
    REVIEW_LAPSED = -32013

    # whisper/1.0 profile (profiles/whisper.md S6)
    WHISPER_ALREADY_ANSWERED = -32020
    WHISPER_LAPSED = -32021
    WHISPER_OPTION_NOT_IN_SET = -32022

    # deliberation/1.0 profile (profiles/deliberation.md S5)
    DELIB_VOTER_NOT_IN_LIST = -32030
    DELIB_ALREADY_VOTED = -32031
    DELIB_CLOSED_OR_LAPSED = -32032
    DELIB_UNKNOWN_RULE = -32033

    # modes/1.0 profile (profiles/modes.md S6)
    MODE_CEILING_EXCEEDED = -32040
    MODE_STEP_UP_REQUIRED = -32041

    # handoff/1.0 profile (profiles/handoff.md S6)
    HANDOFF_TASKS_NOT_ASSIGNED_TO_PROPOSER = -32050
    HANDOFF_ALREADY_RESOLVED = -32051
    HANDOFF_RECIPIENT_NOT_MEMBER = -32052

    # control/1.0 profile (profiles/control.md S6)
    CONTROL_STEP_UP_REQUIRED = -32060
    CONTROL_NOT_AUTHORISED = -32061
    CONTROL_SNAPSHOT_NOT_FOUND = -32062
    CONTROL_WORKSPACE_PAUSED = -32063

    # security-signed/1.0 profile (profiles/security-signed.md S7)
    SIG_VERIFY_FAILED = -32070
    SIG_KEY_NOT_FOUND = -32071
    SIG_KEY_REVOKED = -32072
    SIG_ROTATION_KEY_MISMATCH = -32073

    # audit-scitt/1.0 profile (profiles/audit-scitt.md S8)
    SCITT_UNREACHABLE = -32080
    SCITT_STATEMENT_REJECTED = -32081
    SCITT_RECEIPT_INVALID = -32082

    # identity-oidc/1.0 profile (profiles/identity-oidc.md S8)
    # Note: uses the JSON-RPC reserved range -32400 per the spec
    OIDC_STEP_UP_REQUIRED = -32402
    OIDC_TOKEN_INVALID = -32403
    OIDC_CNF_MISMATCH = -32404
    OIDC_SCOPE_MISSING = -32405

    # identity-vc/1.0 profile (profiles/identity-vc.md S8)
    VC_VP_INVALID = -32410
    VC_HOLDER_BINDING_INVALID = -32411
    VC_CREDENTIAL_REVOKED = -32412
    VC_SCHEMA_UNKNOWN = -32413

    # routing/1.0 profile (profiles/routing.md S3, S4, S5)
    ROUTING_NO_ELIGIBLE_ASSIGNEE = -32510
    ROUTING_POLICY_VIOLATION = -32511
    ROUTING_AUTO_ESCALATION_TRIGGERED = -32512
    ROUTING_CANDIDATES_EMPTY = -32513
    ROUTING_DEPTH_NOT_APPLICABLE = -32514
    ROUTING_POLICY_UNREACHABLE = -32515
    ROUTING_ESC_TARGET_UNAVAILABLE = -32516


def rpc_error(code: int, message: str, data: Any = None) -> dict:
    """Build a JSON-RPC error object."""
    out: dict = {"code": code, "message": message}
    if data is not None:
        out["data"] = data
    return out


def is_valid_envelope(env: Any) -> bool:
    """Quick structural validity check on an incoming envelope."""
    return isinstance(env, dict) and env.get("jsonrpc") == "2.0"


def make_response(env_id: Any, result: Any = None, error: dict | None = None) -> dict:
    """Build a JSON-RPC 2.0 response envelope."""
    out: dict = {"jsonrpc": "2.0", "id": env_id}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result
    return out
