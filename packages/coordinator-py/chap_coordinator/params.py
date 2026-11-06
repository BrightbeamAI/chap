"""
Type checks for the params a method reads.

A param of the wrong type reached code written for the right one, and the
two references then did different things with it: JavaScript reads ``[]``
and ``{}`` as true where Python reads them as false, a Python dict cannot be
looked up with a list, and a value written into a message reads one way in
each language. Checking the type first gives both the same refusal.
``packages/coordinator/src/params.ts`` is the mirror.
"""
from __future__ import annotations

from typing import Any, Iterable

from .jsonrpc import E, rpc_error

_NAMES = {
    "string": "a string",
    "strings": "a list of strings",
    "boolean": "a boolean",
    "object": "an object",
    "list": "a list",
    "integer": "an integer",
}


def is_integer(value: Any) -> bool:
    """An integer as JSON carries one."""
    # JSON has one number type: 5.0 arrives as a float in Python and as an
    # integer in JavaScript, so it counts as one here too. A bool does not.
    if isinstance(value, bool):
        return False
    return isinstance(value, int) or (isinstance(value, float) and value.is_integer())


def _is_kind(value: Any, kind: str) -> bool:
    if kind == "string":
        return isinstance(value, str)
    if kind == "strings":
        return isinstance(value, list) and all(isinstance(v, str) for v in value)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "object":
        return isinstance(value, dict)
    if kind == "list":
        return isinstance(value, list)
    if kind == "integer":
        return is_integer(value)
    raise ValueError(f"unknown param kind {kind!r}")


def params_error(params: dict, checks: Iterable[tuple[str, str]]) -> dict | None:
    """The refusal for the first param in ``checks`` that holds a value of
    another kind, or None when every one is absent, None or of its kind.
    ``checks`` names params of ``params``, or of an object inside it as
    ``outer.inner``."""
    for name, kind in checks:
        value: Any = params
        for part in name.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if value is not None and not _is_kind(value, kind):
            return {"error": rpc_error(E.PARAMS, f"{name} must be {_NAMES[kind]}")}
    return None


def lookup(collection: dict, key: Any) -> Any:
    """The object a collection holds under ``key``, or None.

    Every key is a string, so a key of another type names nothing, as in the
    TypeScript coordinator, whose Map finds nothing for it. A dict lookup with
    a list or an object would raise.
    """
    return collection.get(key) if isinstance(key, str) else None


__all__ = ["is_integer", "lookup", "params_error"]
