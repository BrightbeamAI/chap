"""
Getting a chain in, from wherever it lives.

Four sources, one shape. A :class:`Chain` is the envelope stream plus whatever
server state was available, and it knows which of the two it has, so the
projections can null out a column honestly rather than guessing.

    from chap_analytics import from_sqlite, from_url, from_json, from_coordinator

    chain = from_sqlite("./chap.db", workspace="wsp_support")
    chain = from_url("http://localhost:8080/chap", workspace="wsp_support")
    chain = from_json("export.json")
    chain = from_coordinator(coord, workspace="wsp_support")

Redaction is applied at load, before anything is projected, because an
artefact holds whatever the agent was working on: customer messages,
contracts, source. Pass ``redact=`` a callable and it sees every artefact
before it reaches a table.
"""
from __future__ import annotations

import json
import sqlite3
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

__all__ = ["Chain", "from_json", "from_sqlite", "from_url", "from_coordinator", "redact_artefacts"]

#: Called with every artefact before it is stored. Return a replacement.
Redactor = Callable[[Any], Any]


def redact_artefacts(_artefact: Any) -> Any:
    """
    A redactor that drops artefact bodies entirely.

    Keeps the shape of an analysis intact while removing the content: counts,
    rates, latencies and patch paths all survive, because they are computed
    from metadata rather than from what the artefact said. Pass this when the
    person running the analysis should not see customer data.
    """
    return None


@dataclass
class Chain:
    """
    One workspace's history, ready to project.

    ``events`` is always present. ``state`` is present only where the source
    carried it, and :attr:`has_state` says which, so a projection can mark a
    column unavailable instead of inventing a value for it.
    """

    workspace: str
    events: list[dict[str, Any]]
    state: dict[str, Any] | None = None
    source: str = "unknown"
    _redactor: Redactor | None = field(default=None, repr=False)

    @property
    def has_state(self) -> bool:
        """Whether server-computed values, such as deliberation outcomes, are available."""
        return self.state is not None

    def __len__(self) -> int:
        return len(self.events)

    def __repr__(self) -> str:
        state = "with state" if self.has_state else "envelopes only"
        return f"<Chain {self.workspace!r} {len(self.events)} events, {state}, from {self.source}>"

    def summary(self) -> str:
        methods: dict[str, int] = {}
        for e in self.events:
            m = (e.get("envelope") or {}).get("method", "?")
            methods[m] = methods.get(m, 0) + 1
        lines = [repr(self)]
        for m, n in sorted(methods.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"  {n:>5}  {m}")
        return "\n".join(lines)


def _apply_redaction(events: list[dict[str, Any]], redact: Redactor | None) -> list[dict[str, Any]]:
    """Rewrite artefact-bearing params in place on a copy, before anything reads them."""
    if redact is None:
        return events
    # The three params that carry arbitrary caller content. `diff` is left
    # alone: its values can hold content, but its paths are the analysis, and
    # a redactor that wanted the values gone can strip them itself.
    carriers = ("artefact", "output", "input")
    out: list[dict[str, Any]] = []
    for e in events:
        e = json.loads(json.dumps(e))  # deep copy; events may be shared
        params = (e.get("envelope") or {}).get("params")
        if isinstance(params, dict):
            for key in carriers:
                if key in params:
                    params[key] = redact(params[key])
        out.append(e)
    return out


def _redact_state(state: dict | None, redact: Redactor | None) -> dict | None:
    """
    Redact the snapshot as well as the envelopes.

    Redacting only the envelope stream leaks: a snapshot stores the artefact a
    reviewer worked from and the result of correcting it, and the projection
    prefers those where it has them. Both paths have to be covered or the
    redactor is a false assurance.
    """
    if state is None or redact is None:
        return state
    state = json.loads(json.dumps(state))
    for task in (state.get("tasks") or {}).values():
        for key in ("input", "output", "pending_artefact"):
            if key in task:
                task[key] = redact(task[key])
    for art in (state.get("overrides") or {}).values():
        for key in ("based_on_artefact", "result"):
            if key in art:
                art[key] = redact(art[key])
    for w in (state.get("whispers") or {}).values():
        for key in ("default_if_lapsed", "answer"):
            if key in w:
                w[key] = redact(w[key])
    return state


def _chain(workspace: str, events: Iterable[dict], state: dict | None,
           source: str, redact: Redactor | None) -> Chain:
    evs = _apply_redaction(list(events), redact)
    evs.sort(key=lambda e: e.get("seq", 0))
    return Chain(workspace=workspace, events=evs,
                 state=_redact_state(state, redact), source=source, _redactor=redact)


def from_json(path: str, *, workspace: str | None = None, redact: Redactor | None = None) -> Chain:
    """
    Load from a JSON file.

    Accepts either an ``audit.read`` result (an object with ``entries``), a
    bare list of entries, or a full workspace snapshot (an object with
    ``audit``). A snapshot carries server state; the other two do not.
    """
    with open(path, encoding="utf-8") as fh:
        blob = json.load(fh)

    if isinstance(blob, list):
        return _chain(workspace or "unknown", blob, None, f"json:{path}", redact)
    if "entries" in blob:
        ws = workspace or blob.get("workspace") or "unknown"
        return _chain(ws, blob["entries"], None, f"json:{path}", redact)
    if "audit" in blob:
        ws = workspace or blob.get("id") or "unknown"
        return _chain(ws, blob["audit"], blob, f"json:{path}", redact)
    raise ValueError(
        f"{path} is not a chain: expected a list of entries, an object with "
        "'entries' from audit.read, or a workspace snapshot with 'audit'."
    )


def from_sqlite(path: str, *, workspace: str | None = None, redact: Redactor | None = None) -> Chain:
    """
    Load from a SqliteStore file.

    The richest source: the store holds the whole workspace snapshot, so
    server-computed values are available alongside the envelopes. Opens the
    file read-only, so it is safe against a database a coordinator is using.
    """
    uri = f"file:{path}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        rows = con.execute("SELECT id, data FROM chap_workspaces").fetchall()
    except sqlite3.OperationalError as exc:  # pragma: no cover - depends on the file
        raise ValueError(
            f"{path} does not look like a CHAP SqliteStore: {exc}"
        ) from exc
    finally:
        con.close()

    if not rows:
        raise ValueError(f"{path} holds no workspaces.")
    available = [r[0] for r in rows]
    if workspace is None:
        if len(rows) > 1:
            raise ValueError(
                f"{path} holds {len(rows)} workspaces; name one with "
                f"workspace=. Available: {', '.join(available)}"
            )
        workspace = available[0]
    for ws_id, data in rows:
        if ws_id == workspace:
            snap = json.loads(data)
            return _chain(ws_id, snap.get("audit", []), snap, f"sqlite:{path}", redact)
    raise ValueError(f"{workspace!r} is not in {path}. Available: {', '.join(available)}")


def from_url(url: str, workspace: str, *, actor: str = "service:analytics",
             timeout: int = 30, redact: Redactor | None = None) -> Chain:
    """
    Load over HTTP from a running coordinator, via ``audit.read``.

    Envelopes only: ``audit.read`` returns the log, not server state, so
    columns whose provenance is ``state`` come back null. Everything derived
    by replay is still available, which is most of what matters.
    """
    body = json.dumps({
        "jsonrpc": "2.0", "id": "analytics-audit-read", "method": "audit.read",
        "params": {"workspace": workspace, "from": actor},
    }).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.load(resp)

    if "error" in payload:
        err = payload["error"]
        raise ValueError(
            f"audit.read was refused with {err.get('code')}: {err.get('message')}. "
            "A coordinator with requireReadMembership set needs `actor` to name a member."
        )
    entries = (payload.get("result") or {}).get("entries", [])
    return _chain(workspace, entries, None, f"url:{url}", redact)


def from_coordinator(coord: Any, workspace: str, *, redact: Redactor | None = None) -> Chain:
    """
    Load from an in-process ``Coordinator``.

    Intended for tests and notebooks that drive a coordinator directly. Reads
    the workspace object, so server state is available.
    """
    ws = coord.get_workspace(workspace) if hasattr(coord, "get_workspace") else None
    if ws is None:
        raise ValueError(f"{workspace!r} is not a workspace on this coordinator.")
    # dataclasses.asdict is what the coordinator itself persists through, so a
    # chain read this way and one read from the resulting SqliteStore file are
    # the same shape rather than two shapes that happen to agree today.
    from dataclasses import asdict

    snap = asdict(ws)
    return _chain(workspace, snap.get("audit", []), snap, "coordinator", redact)
