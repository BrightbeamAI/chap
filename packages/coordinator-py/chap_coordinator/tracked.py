"""
Change tracking for a workspace's collections.

When the store implements ``apply``, the Coordinator writes only the objects
a call changed. A ``TrackedDict`` records which keys may hold a changed
object since the last write: every key set or deleted, and, while a call is
being handled, every key read with ``[]`` or ``get`` or reached through
``values()`` or ``items()``, because a handler changes an object in place
after reading it. Reads outside a call, such as an application listing tasks,
record nothing.

A ``TrackedDict`` is a ``dict``. Copies of it (``copy``, ``deepcopy``,
``dict(...)``, pickling) are plain dicts.
"""

from __future__ import annotations

import copy as _copy
from typing import Any, Iterable


class Scope:
    """Whether reads are recorded: while ``depth`` is above zero."""

    __slots__ = ("depth",)

    def __init__(self) -> None:
        self.depth = 0


class TrackedDict(dict):
    """A dict that records the keys whose values may have changed."""

    __slots__ = ("dirty", "scope")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        dict.__init__(self, *args, **kwargs)
        self.dirty: set = set()
        # None until bound: an unbound TrackedDict records nothing. This is
        # also what dataclasses.asdict builds when it copies one.
        self.scope: Scope | None = None

    @classmethod
    def bound(cls, items: Iterable | dict, scope: Scope) -> "TrackedDict":
        d = cls(items)
        d.scope = scope
        return d

    def _reading(self) -> bool:
        return self.scope is not None and self.scope.depth > 0

    def _touch_all(self) -> None:
        if self._reading():
            self.dirty.update(dict.keys(self))

    # -- reads -------------------------------------------------------------

    def __getitem__(self, key: Any) -> Any:
        value = dict.__getitem__(self, key)
        if self._reading():
            self.dirty.add(key)
        return value

    def get(self, key: Any, default: Any = None) -> Any:
        if dict.__contains__(self, key):
            return self[key]
        return default

    def values(self):  # type: ignore[override]
        self._touch_all()
        return dict.values(self)

    def items(self):  # type: ignore[override]
        self._touch_all()
        return dict.items(self)

    def peek(self, key: Any, default: Any = None) -> Any:
        """Read one value without recording it."""
        return dict.get(self, key, default)

    # -- writes ------------------------------------------------------------

    def __setitem__(self, key: Any, value: Any) -> None:
        if self.scope is not None:
            self.dirty.add(key)
        dict.__setitem__(self, key, value)

    def __delitem__(self, key: Any) -> None:
        dict.__delitem__(self, key)
        if self.scope is not None:
            self.dirty.add(key)

    def pop(self, key: Any, *default: Any) -> Any:
        if self.scope is not None and dict.__contains__(self, key):
            self.dirty.add(key)
        return dict.pop(self, key, *default)

    def popitem(self) -> tuple:
        key, value = dict.popitem(self)
        if self.scope is not None:
            self.dirty.add(key)
        return key, value

    def setdefault(self, key: Any, default: Any = None) -> Any:
        if dict.__contains__(self, key):
            return self[key]
        self[key] = default
        return default

    def update(self, *args: Any, **kwargs: Any) -> None:
        for key, value in dict(*args, **kwargs).items():
            self[key] = value

    def __ior__(self, other: Any) -> "TrackedDict":
        self.update(other)
        return self

    def clear(self) -> None:
        if self.scope is not None:
            self.dirty.update(dict.keys(self))
        dict.clear(self)

    # -- copies are plain dicts ----------------------------------------------

    def copy(self) -> dict:
        return dict(dict.items(self))

    def __copy__(self) -> dict:
        return dict(dict.items(self))

    def __deepcopy__(self, memo: dict) -> dict:
        return {_copy.deepcopy(k, memo): _copy.deepcopy(v, memo)
                for k, v in dict.items(self)}

    def __reduce__(self):
        return (dict, (dict(dict.items(self)),))


__all__ = ["Scope", "TrackedDict"]
