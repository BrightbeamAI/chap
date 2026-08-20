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
        return {"error": rpc_error(
            E.SCITT_RECEIPT_INVALID,
            "No verify_scitt_receipt hook configured; receipt not verified")}

    def audit_verify_chain(p: dict) -> dict:
        """Local prev_hash chain replay (supplementary to SCITT).

        Recomputes the chain from the envelopes and checks (a) that every
        entry's stored prev_hash equals the recomputed running hash, and
        (b) that the final recomputed head equals the stored chain_head.
        The head check is essential: without it the last entry is
        unprotected, since no stored prev_hash covers it.
        """
        ws = coord.workspaces.get(p.get("workspace", ""))
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        if not (ws.chain_enabled or coord.options.enable_chain):
            return {"error": rpc_error(E.PARAMS, "Chain not enabled for this workspace")}
        # Coverage begins at the first entry carrying a prev_hash. A
        # workspace may enable chaining part-way through its life, in which
        # case every earlier entry is outside the chain and cannot be
        # evaluated against anything. Those entries are not evidence of
        # tampering and not evidence of integrity: they were never checked,
        # and the verdict below has to say so rather than pass over them.
        start = next((i for i, e in enumerate(ws.audit) if e.prev_hash is not None),
                     len(ws.audit))
        errors: list[str] = []
        prev = ZERO_HASH
        for e in ws.audit[start:]:
            expected_prev = prev
            # A chain-enabled workspace must have prev_hash on every entry;
            # a missing value is a defect, not a reason to skip the check.
            if e.prev_hash != expected_prev:
                errors.append(f"seq {e.seq}: prev_hash mismatch")
            prev = sha256_hex(canonicalize(e.envelope) + expected_prev.encode("utf-8"))
        # The recomputed head must match the stored head; this is what
        # makes the final entry tamper-evident.
        stored_head = ws.chain_head or ZERO_HASH
        if prev != stored_head:
            errors.append("chain_head mismatch: replay does not match stored head")
        if errors:
            return {"error": rpc_error(E.PARAMS, "; ".join(errors))}
        entries_total = len(ws.audit)
        entries_checked = entries_total - start
        entries_unchecked = start
        checked_from_seq = ws.audit[start].seq if entries_checked > 0 else None
        # Three terminal verdicts, mutually exclusive: a broken chain
        # returned above as an error, an unevaluated range here, and a pass
        # only when coverage is complete. "not_evaluated" never rides
        # alongside ok=True; a reader that looks at ok alone fails closed
        # rather than reading a pass over entries nothing was checked
        # against.
        if entries_unchecked > 0:
            return {"result": {
                "status": "not_evaluated",
                "ok": False,
                "reason": "unchained_prefix",
                "entries_total": entries_total,
                "entries_checked": entries_checked,
                "entries_unchecked": entries_unchecked,
                "checked_from_seq": checked_from_seq,
                "chain_head": stored_head,
            }}
        return {"result": {
            "status": "verified",
            "ok": True,
            "entries_total": entries_total,
            "entries_checked": entries_checked,
            "entries_unchecked": 0,
            "checked_from_seq": checked_from_seq,
            "chain_head": stored_head,
        }}

    coord._handlers["audit.submit_to_scitt"] = audit_submit_to_scitt
    coord._handlers["audit.verify_receipt"] = audit_verify_receipt
    coord._handlers["audit.verify_chain"] = audit_verify_chain
