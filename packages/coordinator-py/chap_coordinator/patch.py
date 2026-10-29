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

import json
import re
from typing import Any

# Rejected in every JSON Pointer segment. In a JavaScript runtime these
# enable prototype pollution; CHAP refuses them in both references so a
# patch refused by one implementation is refused by the other.
_DANGEROUS_KEYS = frozenset({"__proto__", "constructor", "prototype"})

# An array index is an RFC 6901 array index: "0" or a positive integer with no
# leading zero. A shared strict rule keeps the two references in step -- JS
# Number() and Python int() otherwise accept disjoint token sets (e.g. "1e1",
# "0x0a", "3.0", "", "1_0"), which would apply the same patch differently.
_ARRAY_INDEX_RE = re.compile(r"^(0|[1-9][0-9]*)$")


def _quoted(value: Any) -> str:
    """A value as it appears on the wire, so both references say the same thing.

    Python's repr and JavaScript's JSON.stringify quote differently, which made
    the same refusal read two ways across the two references.
    """
    return json.dumps(value)


def _json_type(value: Any) -> str:
    """The JSON type name of a value.

    Python and JavaScript name their own types differently (dict against
    object, list against object, str against string). The refusal names the
    JSON type, which both references and the specification share.
    """
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "null"


def _array_index(seg: str) -> int:
    if not _ARRAY_INDEX_RE.match(seg):
        raise PatchError(f"Array index expected at {_quoted(seg)}")
    return int(seg)


class PatchError(Exception):
    """Raised when a JSON Patch operation fails."""


def _unescape(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def _split_path(path: str) -> list[str]:
    """Split an RFC 6901 JSON Pointer into segments, rejecting unsafe keys."""
    if path == "":
        return []
    if not path.startswith("/"):
        raise PatchError(f"JSON Pointer must start with '/': {_quoted(path)}")
    segments = [_unescape(tok) for tok in path[1:].split("/")]
    for seg in segments:
        if seg in _DANGEROUS_KEYS:
            raise PatchError(f"Refusing unsafe path segment {_quoted(seg)}")
    return segments


def _navigate(doc: Any, parts: list[str]) -> tuple[Any, str | int]:
    """Navigate to the *parent* of the target; return (parent, last_key)."""
    if not parts:
        raise PatchError("Cannot operate on root with this helper.")
    parent = doc
    for i, part in enumerate(parts[:-1]):
        if isinstance(parent, list):
            idx = _array_index(part)
            if idx >= len(parent):
                raise PatchError(f"Index out of range at /{'/'.join(parts[:i+1])}")
            parent = parent[idx]
        elif isinstance(parent, dict):
            if part not in parent:
                raise PatchError(f"Path not found: /{'/'.join(parts[:i+1])}")
            parent = parent[part]
        else:
            raise PatchError(f"Cannot traverse into {_json_type(parent)} at {_quoted(part)}")
    last = parts[-1]
    if isinstance(parent, list):
        if last == "-":
            return parent, "-"  # append marker
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
    """A copy of a JSON value that shares nothing with it, or within itself.

    ``copy.deepcopy`` keeps a value that appears in two places as one object,
    and an operation on one place would then change the other as well.
    """
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
        return value  # replace whole document
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
        raise PatchError(f"Cannot add into {_json_type(parent)}")
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
        raise PatchError(f"Cannot remove from {_json_type(parent)}")
    return doc


def _apply_one(doc: Any, op: dict, size: _Size | None = None) -> Any:
    """Apply a single op in place, returning the document, and keep ``size``
    in step with it."""
    kind = op.get("op")
    path = op.get("path", "")

    if kind == "add":
        if "value" not in op:
            raise PatchError("'add' requires 'value'")
        # A copy, so an operation on the inserted value leaves the patch as
        # it was sent.
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
            raise PatchError(f"Cannot replace in {_json_type(parent)}")
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
        # RFC 6902 4.4: 'from' MUST NOT be a proper prefix of 'path'.
        if path.startswith(src + "/"):
            raise PatchError("'move' cannot move a location into its own child.")
        # The remove detaches the value, so it moves as it is, uncopied.
        value = _get(doc, src)
        doc = _remove(doc, src, size, moved=True)
        return _add(doc, path, value, size, moved_from=len(_split_path(src)))

    if kind == "test":
        if "value" not in op:
            raise PatchError("'test' requires 'value'")
        actual = _get(doc, path)
        if not _json_equal(actual, op["value"]):
            raise PatchError(f"'test' failed at {path}")
        return doc

    raise PatchError(f"Unsupported op: {_quoted(kind)}")


MAX_PATCH_OPS = 1000
MAX_DOCUMENT_NODES = 100_000

# How deeply a patched document may nest, counting the document as level one,
# as the coordinator counts a request's nesting. Each operation's value comes
# within the request's limit, and a patch that puts one value inside another
# would otherwise nest the document without limit.
MAX_DOCUMENT_DEPTH = 64

# How much a patch's copies, and its moves to a deeper place or onto the whole
# document, may carry between them, as a multiple of the document's limits.
# Each costs a pass over the value it carries, so a patch that copied the same
# large value back and forth could otherwise keep a coordinator busy for
# seconds.
MAX_CARRIED_TIMES = 3

# The default cap on the string and key text a patched document may hold, in
# UTF-8 bytes: the default ``max_envelope_bytes``. An artefact that arrives in
# a request holds no more text than the request's size. The node cap alone
# does not bound size: a ``copy`` of the root doubles the document, long
# strings included, so fifteen of them grow 64 KiB to about 2 GiB within
# 100,000 nodes.
DEFAULT_MAX_PATCHED_BYTES = 1_048_576


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
    """What the working document holds, kept in step by each operation.

    The values and the bytes are exact: each operation counts what it puts in
    and what it takes out, so a patch costs what it changes, whatever the
    document's size. The depth is a bound, raised by what each operation puts
    in; it passes the limit when the document does, or when the document
    started deeper.
    """

    __slots__ = ("nodes", "bytes", "depth", "carried_nodes", "carried_bytes")

    def __init__(self, doc: Any) -> None:
        self.reset(doc)
        # What copies, and moves to a deeper place or onto the whole document,
        # have carried.
        self.carried_nodes = self.carried_bytes = 0

    def reset(self, doc: Any) -> None:
        self.nodes, self.bytes, self.depth = _size_of(doc)

    def insert(self, value: Any, segments: int, key: str | None) -> tuple[int, int, int]:
        """``value`` goes in ``segments`` levels below the top, under a new ``key``."""
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
        """``value`` comes out, with its ``key``."""
        nodes, size, _ = _size_of(value)
        self.nodes -= nodes
        self.bytes -= size + (0 if key is None else _utf8_length(key))


def apply_json_patch(doc: Any, patch: list[dict], *,
                     max_string_bytes: int = DEFAULT_MAX_PATCHED_BYTES) -> Any:
    """Apply an RFC 6902 JSON Patch, returning the patched document.

    The input ``doc`` is not mutated, and the result shares nothing with it or
    with the patch. ``copy`` can amplify the document (copying a node into
    itself doubles it each step), and a patch can put one value inside
    another, so the op count is bounded, and so are the working document's
    values, the UTF-8 bytes of its strings and keys, and its depth after every
    operation. The limits match the TypeScript reference, so a patch refused
    by one is refused by the other.
    """
    if len(patch) > MAX_PATCH_OPS:
        raise PatchError(
            f"Patch has too many operations ({len(patch)} > {MAX_PATCH_OPS})"
        )
    out = _tree_copy(doc)
    size = _Size(out)
    for op in patch:
        out = _apply_one(out, op, size)
        if size.depth > MAX_DOCUMENT_DEPTH:
            size.depth = _size_of(out)[2]
        if size.nodes > MAX_DOCUMENT_NODES:
            raise PatchError(
                f"Patched document exceeds the node limit ({MAX_DOCUMENT_NODES})"
            )
        if size.bytes > max_string_bytes:
            raise PatchError(
                f"Patched document exceeds the size limit "
                f"({max_string_bytes} bytes of strings and keys)"
            )
        if size.depth > MAX_DOCUMENT_DEPTH:
            raise PatchError(
                f"Patched document nests deeper than {MAX_DOCUMENT_DEPTH} levels"
            )
        if (size.carried_nodes > MAX_CARRIED_TIMES * MAX_DOCUMENT_NODES
                or size.carried_bytes > MAX_CARRIED_TIMES * max_string_bytes):
            raise PatchError(
                f"Patch copies or moves more than its limit "
                f"({MAX_CARRIED_TIMES * MAX_DOCUMENT_NODES} values or "
                f"{MAX_CARRIED_TIMES * max_string_bytes} bytes of strings and keys)"
            )
    return out
