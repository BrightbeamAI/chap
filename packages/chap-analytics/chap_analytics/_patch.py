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
# How deeply a rebuilt artefact may nest, counting it as level one, as the
# coordinators count it.
MAX_DOCUMENT_DEPTH = 64
# How much a patch's copies, and its moves to a deeper place or onto the whole
# document, may carry between them, as a multiple of the document's limits, as
# the coordinators allow.
MAX_CARRIED_TIMES = 3


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


def _json_equal(a: Any, b: Any) -> bool:
    """JSON-type-aware deep equality for the RFC 6902 ``test`` operation.

    Python's ``==`` treats ``True == 1`` and ``False == 0`` as equal because
    ``bool`` is a subclass of ``int``.  JSON distinguishes them: ``true`` is a
    boolean, ``1`` is a number.  The TypeScript reference's ``deepEqual``
    handles this via ``typeof``; this function is the Python equivalent.
    """
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    if a is None or b is None:
        return a is b
    if isinstance(a, dict):
        if not isinstance(b, dict) or len(a) != len(b):
            return False
        return all(k in b and _json_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        if not isinstance(b, list) or len(a) != len(b):
            return False
        return all(_json_equal(x, y) for x, y in zip(a, b))
    return a == b


def _tree_copy(value: Any) -> Any:
    """A copy of a JSON value that shares nothing with it, or within itself."""
    if isinstance(value, dict):
        return {k: _tree_copy(v) if isinstance(v, (dict, list)) else v for k, v in value.items()}
    if isinstance(value, list):
        return [_tree_copy(v) if isinstance(v, (dict, list)) else v for v in value]
    return value


def _add(doc: Any, path: str, value: Any, size: _Size | None,
         copied: bool = False, moved_from: int | None = None) -> Any:
    """Put ``value``, which the document owns from here on, at ``path``.

    A copied value counts towards what the patch carries. A moved value is
    counted already. ``moved_from`` is the number of segments of the place it
    left: only its key, a value it replaces and how deep it now reaches change
    the count. A move to a deeper place, or onto the whole document, costs a
    pass over the value, so it counts towards what the patch carries as well.
    """
    parts = _split_path(path)
    if not parts:
        if size is not None:
            size.reset(value)
            if copied or moved_from is not None:
                size.carry((size.nodes, size.bytes, size.depth))
        return value
    parent, key = _navigate(doc, parts)
    new_key = None
    if isinstance(parent, list):
        if key == "-":
            parent.append(value)
        else:
            if not isinstance(key, int) or key < 0 or key > len(parent):
                raise PatchError(f"Index out of range for add: {path}")
            parent.insert(key, value)
    elif isinstance(parent, dict):
        if key in parent:
            if size is not None:
                size.take(parent[key], None)
        else:
            new_key = key
        parent[key] = value
    else:
        raise PatchError(f"Cannot add into {type(parent).__name__}")
    if size is not None:
        if moved_from is None:
            inserted = size.insert(value, len(parts), new_key)
            if copied:
                size.carry(inserted)
        else:
            if new_key is not None:
                size.bytes += _utf8_length(new_key)
            if len(parts) > moved_from:
                # A move to a deeper place costs a pass over the value, for its depth.
                moved = _size_of(value)
                size.depth = max(size.depth, len(parts) + moved[2])
                size.carry(moved)
    return doc


def _remove(doc: Any, path: str, size: _Size | None, moved: bool = False) -> Any:
    """Remove the value at ``path``. A moved value goes back in elsewhere, so
    only its key leaves the count."""
    parts = _split_path(path)
    if not parts:
        raise PatchError("Cannot remove root.")
    parent, key = _navigate(doc, parts)
    if isinstance(parent, list):
        if not isinstance(key, int) or key < 0 or key >= len(parent):
            raise PatchError(f"Index out of range for remove: {path}")
        if size is not None and not moved:
            size.take(parent[key], None)
        del parent[key]
    elif isinstance(parent, dict):
        if key not in parent:
            raise PatchError(f"Path not found for remove: {path}")
        if size is not None:
            if moved:
                size.bytes -= _utf8_length(key)
            else:
                size.take(parent[key], key)
        del parent[key]
    else:
        raise PatchError(f"Cannot remove from {type(parent).__name__}")
    return doc


def _apply_one(doc: Any, op: dict, size: _Size | None = None) -> Any:  # noqa: C901 - one branch per RFC 6902 operation
    kind = op.get("op")
    path = op.get("path", "")

    if kind == "add":
        if "value" not in op:
            raise PatchError("'add' requires 'value'")
        return _add(doc, path, _tree_copy(op["value"]), size)

    if kind == "replace":
        if "value" not in op:
            raise PatchError("'replace' requires 'value'")
        parts = _split_path(path)
        value = _tree_copy(op["value"])
        if not parts:
            if size is not None:
                size.reset(value)
            return value
        parent, key = _navigate(doc, parts)
        if isinstance(parent, list):
            if not isinstance(key, int) or key < 0 or key >= len(parent):
                raise PatchError(f"Path not found for replace: {path}")
        elif isinstance(parent, dict):
            if key not in parent:
                raise PatchError(f"Path not found for replace: {path}")
        else:
            raise PatchError(f"Cannot replace in {type(parent).__name__}")
        if size is not None:
            size.take(parent[key], None)
            size.insert(value, len(parts), None)
        parent[key] = value
        return doc

    if kind == "remove":
        return _remove(doc, path, size)

    if kind == "copy":
        src = op.get("from")
        if src is None:
            raise PatchError("'copy' requires 'from'")
        return _add(doc, path, _tree_copy(_get(doc, src)), size, copied=True)

    if kind == "move":
        src = op.get("from")
        if src is None:
            raise PatchError("'move' requires 'from'")
        if isinstance(path, str) and path.startswith(src + "/"):
            raise PatchError("'move' cannot move a location into its own child.")
        value = _get(doc, src)
        doc = _remove(doc, src, size, moved=True)
        return _add(doc, path, value, size, moved_from=len(_split_path(src)))

    if kind == "test":
        if "value" not in op:
            raise PatchError("'test' requires 'value'")
        if not _json_equal(_get(doc, path), op["value"]):
            raise PatchError(f"'test' failed at {path}")
        return doc

    raise PatchError(f"Unsupported op: {kind!r}")


# The cap on the string and key text a rebuilt artefact may hold, in UTF-8
# bytes. The node cap alone does not bound size: a ``copy`` of the root doubles
# the document, long strings included, so fifteen of them grow 64 KiB to about
# 2 GiB within 100,000 nodes. A coordinator refuses an override past its own
# ``max_envelope_bytes``, 1 MiB by default and 16 MiB in the code gate, so this
# reader allows 64 MiB: it rebuilds what a coordinator accepted, and stops a
# chain from anywhere else from exhausting memory.
DEFAULT_MAX_PATCHED_BYTES = 67_108_864


def _utf8_length(s: str) -> int:
    # surrogatepass counts a lone surrogate as three bytes, as the TypeScript
    # count does, so the two references refuse the same patches.
    return len(s) if s.isascii() else len(s.encode("utf-8", "surrogatepass"))


def _size_of(value: Any) -> tuple[int, int, int]:
    """A value's count of values, the UTF-8 bytes of its strings and keys, and
    its depth, counting the value itself as level one, in one pass that does
    not recurse."""
    if not isinstance(value, (dict, list)):
        return 1, _utf8_length(value) if isinstance(value, str) else 0, 1
    nodes = size = depth = 0
    stack = [(value, 1)]
    while stack:
        v, level = stack.pop()
        nodes += 1
        if level > depth:
            depth = level
        child = level + 1
        # A scalar child is counted here, which saves a trip through the stack.
        if isinstance(v, dict):
            for k, x in v.items():
                size += _utf8_length(k if isinstance(k, str) else str(k))
                if isinstance(x, (dict, list)):
                    stack.append((x, child))
                else:
                    nodes += 1
                    if child > depth:
                        depth = child
                    if isinstance(x, str):
                        size += _utf8_length(x)
        else:
            for x in v:
                if isinstance(x, (dict, list)):
                    stack.append((x, child))
                else:
                    nodes += 1
                    if child > depth:
                        depth = child
                    if isinstance(x, str):
                        size += _utf8_length(x)
    return nodes, size, depth


class _Size:
    """What the working document holds, kept in step by each operation: the
    values and bytes exactly, the depth as a bound that passes the limit when
    the document does, or when the document started deeper."""

    __slots__ = ("nodes", "bytes", "depth", "carried_nodes", "carried_bytes")

    def __init__(self, doc: Any) -> None:
        self.reset(doc)
        # What copies, and moves to a deeper place or onto the whole document,
        # have carried.
        self.carried_nodes = self.carried_bytes = 0

    def reset(self, doc: Any) -> None:
        self.nodes, self.bytes, self.depth = _size_of(doc)

    def insert(self, value: Any, segments: int, key: str | None) -> tuple[int, int, int]:
        measured = _size_of(value)
        nodes, size, depth = measured
        self.nodes += nodes
        self.bytes += size + (0 if key is None else _utf8_length(key))
        self.depth = max(self.depth, segments + depth)
        return measured

    def carry(self, measured: tuple[int, int, int]) -> None:
        self.carried_nodes += measured[0]
        self.carried_bytes += measured[1]

    def take(self, value: Any, key: str | None) -> None:
        nodes, size, _ = _size_of(value)
        self.nodes -= nodes
        self.bytes -= size + (0 if key is None else _utf8_length(key))


def apply_json_patch(doc: Any, patch: list[dict], *,
                     max_string_bytes: int = DEFAULT_MAX_PATCHED_BYTES) -> Any:
    """Apply a patch to a copy of ``doc`` and return the result."""
    if not isinstance(patch, list):
        raise PatchError("A patch is a list of operations.")
    if len(patch) > MAX_PATCH_OPS:
        raise PatchError(f"Patch has too many operations ({len(patch)} > {MAX_PATCH_OPS})")
    out = _tree_copy(doc)
    # Each operation counts what it puts in and takes out, so a patch costs
    # what it changes, whatever the document's size.
    size = _Size(out)
    for op in patch:
        if not isinstance(op, dict):
            raise PatchError("Each operation is an object.")
        out = _apply_one(out, op, size)
        if size.depth > MAX_DOCUMENT_DEPTH:
            size.depth = _size_of(out)[2]
        if size.nodes > MAX_DOCUMENT_NODES:
            raise PatchError(f"Patched document exceeds the node limit ({MAX_DOCUMENT_NODES})")
        if size.bytes > max_string_bytes:
            raise PatchError(
                f"Patched document exceeds the size limit ({max_string_bytes} bytes of strings and keys)"
            )
        if size.depth > MAX_DOCUMENT_DEPTH:
            raise PatchError(f"Patched document nests deeper than {MAX_DOCUMENT_DEPTH} levels")
        if (size.carried_nodes > MAX_CARRIED_TIMES * MAX_DOCUMENT_NODES
                or size.carried_bytes > MAX_CARRIED_TIMES * max_string_bytes):
            raise PatchError(
                f"Patch copies or moves more than its limit ({MAX_CARRIED_TIMES * MAX_DOCUMENT_NODES} values"
                f" or {MAX_CARRIED_TIMES * max_string_bytes} bytes of strings and keys)"
            )
    return out
