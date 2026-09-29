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


def entry_is_well_formed(entry: "AuditEntry") -> bool:
    """An accepted envelope alone, or a refused request with its outcome.

    The outcome of a refusal has status ``refused`` and an integer code.
    """
    accepted = entry.envelope is not None
    refused = entry.request is not None
    if accepted == refused:
        return False
    if accepted:
        return entry.outcome is None
    o = entry.outcome
    return (isinstance(o, dict) and o.get("status") == "refused"
            and isinstance(o.get("code"), int) and not isinstance(o.get("code"), bool))


def link_hash(record: Any, prev: str) -> str:
    """Chain link: sha256( JCS(record) || prev_hash )."""
    return sha256_hex(canonicalize(record) + prev.encode("utf-8"))


# Refusals the log never records: a call that was malformed or invalid, a
# fault in the Coordinator, and a call whose signature or key did not check
# out, which leaves its sender unauthenticated.
_UNRECORDED_CODES = frozenset({
    E.PARSE, E.REQUEST, E.PARAMS, E.INTERNAL,
    E.SIG_VERIFY_FAILED, E.SIG_KEY_NOT_FOUND, E.SIG_KEY_REVOKED,
    E.SIG_ROTATION_KEY_MISMATCH,
})


def refusal_is_recorded(method: str, error: dict, privileged: frozenset[str]) -> bool:
    """Whether a refusal with this error is one the log records.

    Decided before the test that the caller is a member. ``-32601`` is
    recorded only when the profile gate refused a privileged method, which
    is an attempt to pull an emergency brake the workspace has switched
    off. The gate refusing an ordinary method, and a method that does not
    exist, are not recorded.
    """
    code = error.get("code")
    if code in _UNRECORDED_CODES:
        return False
    if code == E.METHOD:
        data = error.get("data")
        return (isinstance(data, dict) and isinstance(data.get("profile"), str)
                and method in privileged)
    return True
