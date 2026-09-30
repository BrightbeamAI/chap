"""
chap_coordinator.audit

Audit entries, the chain link over them, and the rule for recording a
refusal (SPECIFICATION 10.1). Mirror of ``packages/coordinator/src/audit.ts``.

An accepted call is recorded under ``envelope``. A refused call that the
rule names is recorded under ``request``, with an ``outcome`` beside it, so
a reader keyed on ``envelope`` passes it by instead of replaying it as
effective. The chain link hashes what the entry records: the envelope of an
accepted call, and the outcome together with the request of a refusal.
Neither half of a refusal can be altered or stripped without breaking the
chain, and an entry with no outcome hashes exactly as it always has, so
every existing chain still verifies.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .canonical import canonicalize, sha256_hex
from .jsonrpc import E

if TYPE_CHECKING:
    from .types import AuditEntry


def entry_record(entry: "AuditEntry") -> Any:
    """The value an entry's chain link hashes."""
    if entry.outcome is not None:
        return {"outcome": entry.outcome, "request": entry.request}
    return entry.envelope


def entry_call(entry: "AuditEntry") -> dict | None:
    """The call an entry records, whether it took effect or was refused."""
    return entry.envelope if entry.envelope is not None else entry.request


def is_refusal(entry: "AuditEntry") -> bool:
    """True when an entry records a refused call."""
    return entry.outcome is not None


def _is_call(value: Any) -> bool:
    """A JSON-RPC call: an object with ``jsonrpc`` "2.0" and a string ``method``."""
    return (isinstance(value, dict) and value.get("jsonrpc") == "2.0"
            and isinstance(value.get("method"), str))


def _is_integer(value: Any) -> bool:
    """An integer, as JSON carries one: ``-32011`` or ``-32011.0``, never a boolean."""
    if isinstance(value, bool):
        return False
    return isinstance(value, int) or (isinstance(value, float) and value.is_integer())


def entry_is_well_formed(entry: "AuditEntry") -> bool:
    """An accepted call alone, or a refused call alone with its outcome.

    The accepted call is under ``envelope``; the refused call is under
    ``request``, with an outcome of status ``refused`` and an integer code.
    Requiring a JSON-RPC call on either side is what stops a refusal's
    record being moved under ``envelope``, where it would hash to the same
    bytes and read as a call that took effect.
    """
    accepted = entry.envelope is not None
    refused = entry.request is not None
    if accepted == refused:
        return False
    if accepted:
        return _is_call(entry.envelope) and entry.outcome is None
    o = entry.outcome
    return (_is_call(entry.request) and isinstance(o, dict)
            and o.get("status") == "refused" and _is_integer(o.get("code")))


def request_digest(request: Any) -> str:
    """The digest that identifies a request byte for byte: SHA-256 of its JCS."""
    return sha256_hex(canonicalize(request))


def link_hash(record: Any, prev: str) -> str:
    """Chain link: sha256( JCS(record) || prev_hash )."""
    return sha256_hex(canonicalize(record) + prev.encode("utf-8"))


# Refusals the log never records: a call that was malformed or invalid, a
# fault in the Coordinator, and a call whose signature or key did not check
# out.
_UNRECORDED_CODES = frozenset({
    E.PARSE, E.REQUEST, E.PARAMS, E.INTERNAL,
    E.SIG_VERIFY_FAILED, E.SIG_KEY_NOT_FOUND, E.SIG_KEY_REVOKED,
    E.SIG_ROTATION_KEY_MISMATCH,
})

# Methods whose refusals are never recorded. They run before the caller is
# established as a member and are exempt from signature checks, so a refusal
# of one proves nothing about who sent it.
_UNRECORDED_METHODS = frozenset({"workspace.create", "participant.join"})


def refusal_is_recorded(method: str, error: dict, privileged: frozenset[str]) -> bool:
    """Whether a refusal with this error is one the log records.

    Decided before the test that the caller is a member. ``-32601`` is
    recorded only when the profile gate refused a privileged method, which
    is an attempt to pull an emergency brake the workspace has switched
    off. The gate refusing an ordinary method, and a method that does not
    exist, are not recorded.
    """
    code = error.get("code")
    if not _is_integer(code):
        return False
    if method in _UNRECORDED_METHODS:
        return False
    if code in _UNRECORDED_CODES:
        return False
    if code == E.METHOD:
        data = error.get("data")
        return (isinstance(data, dict) and isinstance(data.get("profile"), str)
                and method in privileged)
    return True
