"""
chap_coordinator.canonical

RFC 8785 (JCS) JSON canonicalisation. CHAP signs and hashes the JCS
canonicalisation of envelopes (with ``evidence.sig`` removed for the
``security-signed/1.0`` profile, or the bare envelope for plain Core
chain linkage).

This is a pragmatic JCS implementation sufficient for the JSON value
space CHAP uses (objects, arrays, strings, booleans, null, integers,
and short decimal floats). It conforms to RFC 8785 for those values.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

ZERO_HASH = "sha256:" + "0" * 64


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
        return str(obj)
    if isinstance(obj, float):
        if obj != obj or obj in (float("inf"), float("-inf")):
            raise ValueError("Non-finite numbers are not permitted in JCS.")
        if obj.is_integer():
            return str(int(obj))
        # Shortest round-trip via repr; sufficient for the value space CHAP uses.
        return repr(obj)
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
