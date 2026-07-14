"""
chap_coordinator.canonical

Deterministic JSON canonicalisation for CHAP. CHAP signs and hashes the
canonicalisation of envelopes (with ``evidence.sig`` removed for the
``security-signed/1.0`` profile, or the bare envelope for plain Core
chain linkage).

This follows RFC 8785 (JCS) for objects, arrays, strings, booleans, and
null, with one deliberate restriction on numbers. RFC 8785 mandates the
ECMAScript number-to-string algorithm, which is genuinely hard to
reproduce byte-identically across languages; getting it subtly wrong
would make a chain written by one implementation fail verification
against another. To make cross-implementation agreement provable rather
than approximate, a CHAP canonical number MUST be an integer within the
IEEE-754 / ECMAScript safe-integer range (abs value <= 2**53 - 1).
Non-integer values and larger magnitudes are rejected: represent them as
strings (for example ``"8.2"`` for a decimal reading, or the digits of a
large identifier as a string). The TypeScript reference enforces the
identical rule, so both accept and reject exactly the same inputs.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

ZERO_HASH = "sha256:" + "0" * 64

# ECMAScript Number.MAX_SAFE_INTEGER (2**53 - 1). Integers with a larger
# magnitude cannot be represented exactly as a double and are rejected so
# the Python and TypeScript canonicalisers can never diverge on them.
_MAX_SAFE_INTEGER = 9007199254740991

_NON_INTEGER_ERROR = (
    "CHAP canonical numbers must be integers; represent decimals as strings "
    "(e.g. \"8.2\") so the hash is deterministic across implementations."
)
_NUMBER_RANGE_ERROR = (
    "CHAP canonical integers must be within the safe-integer range "
    "(abs value <= 2**53 - 1); represent larger numbers as strings."
)


def _canon(obj: Any) -> str:
    # bool must be checked before int (bool is a subclass of int in Python)
    if obj is None:
        return "null"
    if obj is True:
        return "true"
    if obj is False:
        return "false"
    if isinstance(obj, str):
        return json.dumps(obj, ensure_ascii=False)
    if isinstance(obj, int):
        if abs(obj) > _MAX_SAFE_INTEGER:
            raise ValueError(_NUMBER_RANGE_ERROR)
        return str(obj)
    if isinstance(obj, float):
        if obj != obj or obj in (float("inf"), float("-inf")):
            raise ValueError("Non-finite numbers are not permitted in a CHAP canonical value.")
        if not obj.is_integer():
            raise ValueError(_NON_INTEGER_ERROR)
        as_int = int(obj)
        if abs(as_int) > _MAX_SAFE_INTEGER:
            raise ValueError(_NUMBER_RANGE_ERROR)
        return str(as_int)
    if isinstance(obj, dict):
        items: list[str] = []
        for key in sorted(obj.keys()):
            if not isinstance(key, str):
                raise TypeError("JCS object keys must be strings.")
            items.append(f"{json.dumps(key, ensure_ascii=False)}:{_canon(obj[key])}")
        return "{" + ",".join(items) + "}"
    if isinstance(obj, (list, tuple)):
        return "[" + ",".join(_canon(v) for v in obj) + "]"
    raise TypeError(f"Cannot canonicalise value of type {type(obj)!r}")


def canonicalize(obj: Any) -> bytes:
    """Return the JCS canonical UTF-8 bytes for a JSON-compatible value."""
    return _canon(obj).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    """Return a CHAP-style ``sha256:<64 hex>`` digest of ``data``."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def content_hash(content: Any) -> str:
    """Content hash for an artefact payload (JCS for JSON content)."""
    return sha256_hex(canonicalize(content))
