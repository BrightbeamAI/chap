"""
chap_coordinator.profiles.audit_scitt

The audit-scitt/1.0 profile (profiles/audit-scitt.md).

The spec defers entirely to SCITT for transparency-log semantics:
each accepted envelope is wrapped as a COSE_Sign1 signed statement
and submitted to a SCITT transparency service that returns a
receipt. Receipts are verified by anyone with the TS's public key,
out-of-band.

CHAP does not run a SCITT TS itself. This module provides:
  - audit.submit_to_scitt : produce a SCITT-style statement for a
                            given audit entry (or range), call the
                            deployment-supplied submitter, and
                            record the returned receipt on the entry
  - audit.verify_receipt  : verify a receipt against a public key
                            (delegated to a deployment hook)

It also enables the simple chain-linkage (prev_hash) on every audit
entry, which is a useful local integrity check independent of
external SCITT delivery. The spec lists this as something CHAP
deletes in favour of SCITT; in practice both are useful, so we
expose both and let the deployment choose.

Error codes:
  -32080 SCITT transparency service unreachable
  -32081 statement rejected by transparency service
  -32082 receipt verification failed
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..canonical import ZERO_HASH, canonicalize, sha256_hex
from ..jsonrpc import E, rpc_error

if TYPE_CHECKING:
    from ..coordinator import Coordinator


def _build_statement(workspace_id: str, entry_envelope: dict,
                     sender: str | None, issuer: str) -> dict:
    """Build a SCITT-style signed statement for a CHAP envelope.

    The COSE_Sign1 structure here is JSON-modelled rather than binary
    CBOR; a real deployment will pass this to a SCITT client library
    that produces the actual COSE encoding before submission.
    """
    payload_canonical = canonicalize(entry_envelope).decode("utf-8")
    return {
        "protected": {
            "alg": -8,  # Ed25519 per COSE
            "iss": issuer,
            "kid": "scitt-issuer",
            "cwt_claims": {
                "sub": workspace_id,
                "iat": None,
            },
            "content-type": "application/chap+json;version=0.2",
        },
        "payload": payload_canonical,
        "signature": "<deployment-supplied>",
    }


def register_audit_scitt(coord: "Coordinator") -> None:

    def audit_submit_to_scitt(p: dict) -> dict:
        ws = coord.workspaces.get(p.get("workspace", ""))
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}

        rng = p.get("range") or {}
        from_seq = int(rng.get("from_seq", 0))
        to_seq = int(rng.get("to_seq", len(ws.audit)))
        issuer = p.get("issuer") or "service:coordinator"

        receipts: list[dict] = []
        if coord.options.scitt_submitter is None:
            # No deployment submitter; return the statements so the caller
            # can submit out-of-band themselves.
            statements = [
                _build_statement(ws.id, entry.envelope,
                                 entry.envelope.get("params", {}).get("from"),
                                 issuer)
                for entry in ws.audit[from_seq:to_seq]
            ]
            return {"result": {
                "statements": statements,
                "note": "No scitt_submitter configured; submit these out-of-band",
            }}

        for entry in ws.audit[from_seq:to_seq]:
            statement = _build_statement(
                ws.id, entry.envelope,
                entry.envelope.get("params", {}).get("from"),
                issuer,
            )
            try:
                receipt = coord.options.scitt_submitter(statement)
            except Exception as exc:
                return {"error": rpc_error(E.SCITT_UNREACHABLE,
                                           f"SCITT submission error: {exc}")}
            if receipt is None:
                return {"error": rpc_error(E.SCITT_STATEMENT_REJECTED,
                                           f"Statement rejected at seq {entry.seq}")}
            receipts.append({"seq": entry.seq, "receipt": receipt})

        return {"result": {"receipts": receipts}}

    def audit_verify_receipt(p: dict) -> dict:
        receipt = p.get("receipt")
        if not isinstance(receipt, dict):
            return {"error": rpc_error(E.PARAMS, "receipt must be an object")}
        # The verification path is deployment-specific (depends on the
        # TS's public key and the SCITT library in use). The Coordinator
        # exposes a hook via options if one is set; otherwise echo the
        # receipt back with a note.
        verify_hook = getattr(coord.options, "verify_scitt_receipt", None)
        if verify_hook is not None:
            try:
                ok = bool(verify_hook(receipt))
            except Exception as exc:
                return {"error": rpc_error(E.SCITT_RECEIPT_INVALID,
                                           f"verify error: {exc}")}
            if not ok:
                return {"error": rpc_error(E.SCITT_RECEIPT_INVALID,
                                           "Receipt did not verify")}
            return {"result": {"verified": True}}
        return {"result": {"verified": None,
                           "note": "No verify_scitt_receipt hook configured"}}

    def audit_verify_chain(p: dict) -> dict:
        """Local prev_hash chain replay (supplementary to SCITT)."""
        ws = coord.workspaces.get(p.get("workspace", ""))
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        errors: list[str] = []
        prev = ZERO_HASH
        for e in ws.audit:
            expected_prev = prev
            if e.prev_hash is not None and e.prev_hash != expected_prev:
                errors.append(f"seq {e.seq}: prev_hash mismatch")
            prev = sha256_hex(canonicalize(e.envelope) + expected_prev.encode("utf-8"))
        if errors:
            return {"error": rpc_error(E.PARAMS, "; ".join(errors))}
        return {"result": {
            "ok": True,
            "entries_checked": len(ws.audit),
            "chain_head": ws.chain_head or ZERO_HASH,
        }}

    coord._handlers["audit.submit_to_scitt"] = audit_submit_to_scitt
    coord._handlers["audit.verify_receipt"] = audit_verify_receipt
    coord._handlers["audit.verify_chain"] = audit_verify_chain
