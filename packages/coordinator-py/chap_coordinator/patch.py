"""
chap_coordinator.patch

RFC 6902 JSON Patch implementation.

CHAP's ``decide.override`` carries a JSON Patch as its diff. This
implements all six RFC 6902 operations: ``add``, ``remove``,
``replace``, ``move``, ``copy``, ``test``.

The patch is applied to a deep copy; the original input is not mutated.
Path segments that could be used for prototype pollution in a
JavaScript runtime (``__proto__``, ``constructor``, ``prototype``) are
rejected, so the Python and TypeScript references accept and reject
exactly the same patches.
"""
from __future__ import annotations

import copy
from typing import Any

# Rejected in every JSON Pointer segment. In a JavaScript runtime these
# enable prototype pollution; CHAP refuses them in both references so a
# patch refused by one implementation is refused by the other.
_DANGEROUS_KEYS = frozenset({"__proto__", "constructor", "prototype"})


class PatchError(Exception):
    """Raised when a JSON Patch operation fails."""


def _unescape(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def _split_path(path: str) -> list[str]:
    """Split an RFC 6901 JSON Pointer into segments, rejecting unsafe keys."""
    if path == "":
        return []
    if not path.startswith("/"):
        raise PatchError(f"JSON Pointer must start with '/': {path!r}")
    segments = [_unescape(tok) for tok in path[1:].split("/")]
    for seg in segments:
        if seg in _DANGEROUS_KEYS:
            raise PatchError(f"Refusing unsafe path segment {seg!r}")
    return segments


def _navigate(doc: Any, parts: list[str]) -> tuple[Any, str | int]:
    """Navigate to the *parent* of the target; return (parent, last_key)."""
    if not parts:
        raise PatchError("Cannot operate on root with this helper.")
    parent = doc
    for i, part in enumerate(parts[:-1]):
        if isinstance(parent, list):
            try:
                idx = int(part)
            except ValueError as exc:
                raise PatchError(f"Array index expected at {part!r}") from exc
            if idx < 0 or idx >= len(parent):
                raise PatchError(f"Index out of range at /{'/'.join(parts[:i+1])}")
            parent = parent[idx]
        elif isinstance(parent, dict):
            if part not in parent:
                raise PatchError(f"Path not found: /{'/'.join(parts[:i+1])}")
            parent = parent[part]
        else:
            raise PatchError(f"Cannot traverse into {type(parent).__name__} at {part!r}")
    last = parts[-1]
    if isinstance(parent, list):
        if last == "-":
            return parent, "-"  # append marker
        try:
            return parent, int(last)
        except ValueError as exc:
            raise PatchError(f"Array index expected at {last!r}") from exc
    return parent, last


def _get(doc: Any, path: str) -> Any:
    parts = _split_path(path)
    if not parts:
        return doc
    parent, key = _navigate(doc, parts)
    if isinstance(parent, list):
        if key == "-":
            raise PatchError("Cannot read '-' position.")
        if not isinstance(key, int) or key < 0 or key >= len(parent):
            raise PatchError(f"Index out of range: {path}")
        return parent[key]
    if not isinstance(parent, dict) or key not in parent:
        raise PatchError(f"Path not found: {path}")
    return parent[key]


def _apply_one(doc: Any, op: dict) -> Any:
    """Apply a single op in place, returning the document."""
    kind = op.get("op")
    path = op.get("path", "")

    if kind == "add":
        if "value" not in op:
            raise PatchError("'add' requires 'value'")
        parts = _split_path(path)
        if not parts:
            return op["value"]  # replace whole document
        parent, key = _navigate(doc, parts)
        if isinstance(parent, list):
            if key == "-":
                parent.append(op["value"])
            else:
                if not isinstance(key, int) or key < 0 or key > len(parent):
                    raise PatchError(f"Index out of range for add: {path}")
                parent.insert(key, op["value"])
        elif isinstance(parent, dict):
            parent[key] = op["value"]
        else:
            raise PatchError(f"Cannot add into {type(parent).__name__}")
        return doc

    if kind == "replace":
        if "value" not in op:
            raise PatchError("'replace' requires 'value'")
        parts = _split_path(path)
        if not parts:
            return op["value"]
        parent, key = _navigate(doc, parts)
        if isinstance(parent, list):
            if not isinstance(key, int) or key < 0 or key >= len(parent):
                raise PatchError(f"Path not found for replace: {path}")
            parent[key] = op["value"]
        elif isinstance(parent, dict):
            if key not in parent:
                raise PatchError(f"Path not found for replace: {path}")
            parent[key] = op["value"]
        else:
            raise PatchError(f"Cannot replace in {type(parent).__name__}")
        return doc

    if kind == "remove":
        parts = _split_path(path)
        if not parts:
            raise PatchError("Cannot remove root.")
        parent, key = _navigate(doc, parts)
        if isinstance(parent, list):
            if not isinstance(key, int) or key < 0 or key >= len(parent):
                raise PatchError(f"Index out of range for remove: {path}")
            del parent[key]
        elif isinstance(parent, dict):
            if key not in parent:
                raise PatchError(f"Path not found for remove: {path}")
            del parent[key]
        else:
            raise PatchError(f"Cannot remove from {type(parent).__name__}")
        return doc

    if kind == "copy":
        src = op.get("from")
        if src is None:
            raise PatchError("'copy' requires 'from'")
        value = copy.deepcopy(_get(doc, src))
        return _apply_one(doc, {"op": "add", "path": path, "value": value})

    if kind == "move":
        src = op.get("from")
        if src is None:
            raise PatchError("'move' requires 'from'")
        # RFC 6902 4.4: 'from' MUST NOT be a proper prefix of 'path'.
        if path.startswith(src + "/"):
            raise PatchError("'move' cannot move a location into its own child.")
        value = copy.deepcopy(_get(doc, src))
        doc = _apply_one(doc, {"op": "remove", "path": src})
        return _apply_one(doc, {"op": "add", "path": path, "value": value})

    if kind == "test":
        if "value" not in op:
            raise PatchError("'test' requires 'value'")
        actual = _get(doc, path)
        if actual != op["value"]:
            raise PatchError(f"'test' failed at {path}")
        return doc

    raise PatchError(f"Unsupported op: {kind!r}")


def apply_json_patch(doc: Any, patch: list[dict]) -> Any:
    """Apply an RFC 6902 JSON Patch, returning the patched document.

    The input ``doc`` is not mutated.
    """
    out = copy.deepcopy(doc)
    for op in patch:
        out = _apply_one(out, op)
    return out
