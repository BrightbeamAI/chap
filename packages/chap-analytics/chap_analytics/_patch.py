"""
RFC 6902 JSON Patch, for reconstructing the corrected artefact.

``decide.override`` carries the patch a reviewer applied; ``review.request``
carried the artefact they applied it to. Applying the one to the other gives
the corrected artefact from envelopes alone, which is what makes the
``result`` column available to a client that has only ``audit.read``.

This is the coordinator's own applier, copied rather than imported, because
the package runs with pandas alone and the two implementations have to agree
exactly for ``result`` to equal what the coordinator stored. The differential
suite applies every patch both ways and requires the same document. Keep this
file in step with ``chap_coordinator/patch.py``; the limits and the refused
path segments are deliberately the same.
"""
from __future__ import annotations

import copy
import re
from typing import Any

__all__ = ["apply_json_patch", "PatchError"]

# Refused in every JSON Pointer segment, as the coordinator refuses them: in a
# JavaScript runtime they enable prototype pollution, and every implementation
# refuses the same patches.
_DANGEROUS_KEYS = frozenset({"__proto__", "constructor", "prototype"})

# An RFC 6901 array index: "0" or a positive integer with no leading zero.
_ARRAY_INDEX_RE = re.compile(r"^(0|[1-9][0-9]*)$")

MAX_PATCH_OPS = 1000
MAX_DOCUMENT_NODES = 100_000


class PatchError(Exception):
    """A patch operation that fails to apply to this document."""


def _array_index(seg: str) -> int:
    if not _ARRAY_INDEX_RE.match(seg):
        raise PatchError(f"Array index expected at {seg!r}")
    return int(seg)


def _unescape(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def _split_path(path: str) -> list[str]:
    if path == "":
        return []
    if not isinstance(path, str) or not path.startswith("/"):
        raise PatchError(f"JSON Pointer must start with '/': {path!r}")
    segments = [_unescape(tok) for tok in path[1:].split("/")]
    for seg in segments:
        if seg in _DANGEROUS_KEYS:
            raise PatchError(f"Refusing unsafe path segment {seg!r}")
    return segments


def _navigate(doc: Any, parts: list[str]) -> tuple[Any, str | int]:
    """The parent of the target, and the key of the target within it."""
    if not parts:
        raise PatchError("Cannot operate on root with this helper.")
    parent = doc
    for i, part in enumerate(parts[:-1]):
        if isinstance(parent, list):
            idx = _array_index(part)
            if idx >= len(parent):
                raise PatchError(f"Index out of range at /{'/'.join(parts[:i + 1])}")
            parent = parent[idx]
        elif isinstance(parent, dict):
            if part not in parent:
                raise PatchError(f"Path not found: /{'/'.join(parts[:i + 1])}")
            parent = parent[part]
        else:
            raise PatchError(f"Cannot traverse into {type(parent).__name__} at {part!r}")
    last = parts[-1]
    if isinstance(parent, list):
        if last == "-":
            return parent, "-"
        return parent, _array_index(last)
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


def _apply_one(doc: Any, op: dict) -> Any:  # noqa: C901 - one branch per RFC 6902 operation
    kind = op.get("op")
    path = op.get("path", "")

    if kind == "add":
        if "value" not in op:
            raise PatchError("'add' requires 'value'")
        parts = _split_path(path)
        if not parts:
            return op["value"]
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
        if isinstance(path, str) and path.startswith(src + "/"):
            raise PatchError("'move' cannot move a location into its own child.")
        value = copy.deepcopy(_get(doc, src))
        doc = _apply_one(doc, {"op": "remove", "path": src})
        return _apply_one(doc, {"op": "add", "path": path, "value": value})

    if kind == "test":
        if "value" not in op:
            raise PatchError("'test' requires 'value'")
        if _get(doc, path) != op["value"]:
            raise PatchError(f"'test' failed at {path}")
        return doc

    raise PatchError(f"Unsupported op: {kind!r}")


def _node_count(value: Any) -> int:
    if isinstance(value, dict):
        return 1 + sum(_node_count(v) for v in value.values())
    if isinstance(value, list):
        return 1 + sum(_node_count(v) for v in value)
    return 1


def apply_json_patch(doc: Any, patch: list[dict]) -> Any:
    """Apply a patch to a copy of ``doc`` and return the result."""
    if not isinstance(patch, list):
        raise PatchError("A patch is a list of operations.")
    if len(patch) > MAX_PATCH_OPS:
        raise PatchError(f"Patch has too many operations ({len(patch)} > {MAX_PATCH_OPS})")
    out = copy.deepcopy(doc)
    for op in patch:
        if not isinstance(op, dict):
            raise PatchError("Each operation is an object.")
        out = _apply_one(out, op)
        if _node_count(out) > MAX_DOCUMENT_NODES:
            raise PatchError(f"Patched document exceeds the node limit ({MAX_DOCUMENT_NODES})")
    return out
