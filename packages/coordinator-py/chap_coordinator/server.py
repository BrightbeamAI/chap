"""
chap_coordinator.server
=======================

The desk server: the one process that owns a store, runs the coordinator
on it, and serves

    GET  /              the review desk
    POST /chap          CHAP calls as JSON-RPC, for any agent or client
    GET  /api/...       what the desk needs that the protocol does not carry
    GET  /analytics/    the pages chap-analytics wrote, when they exist

Two servers run the same routes. :func:`serve` runs them on the standard
library's HTTP server, and :class:`ChapDesk` is the routes as an ASGI app,
to mount in FastAPI or Starlette. Everything comes from
``chap.config.json``, read by :func:`load_config`::

    from chap_coordinator.server import load_config, serve

    serve(load_config("chap.config.json"))

Whichever server runs them, the routes check every request:

- the Host must be one of the server's own names (421), so a page that
  rebinds its own name to this address (DNS rebinding) gets nothing;
- a browser request from another origin is refused (403), so a page open
  elsewhere cannot call these routes; a client that is not a browser sends
  no Origin;
- ``POST /chap`` takes ``application/json`` only (415), and no body over
  MAX_BODY_BYTES is read (413);
- every response carries a Content-Security-Policy that allows no inline
  script and no framing, ``X-Frame-Options: DENY``, ``nosniff`` and
  ``Referrer-Policy: no-referrer``, so no other page can frame the desk;
- a page under ``/analytics/`` is served with ``Content-Security-Policy:
  sandbox allow-scripts``: it runs in an origin of its own, where it reads
  nothing the desk keeps, and its calls here are refused as cross-origin.

One process owns one store. The coordinator is a single writer, and the
server makes every call and every read under one lock, ``ChapDesk.lock``;
code of your own that uses the same coordinator in the same process takes
it too. Other processes reach the server over HTTP.

A ``human:`` URI labels a participant; it does not authenticate a person.
Under security-signed/1.0 the desk signs each decision in the browser with
the reviewer's own key. Which reviews a person is shown is the deployment's
decision, as SPECIFICATION 15.1 says; the desk shows a reviewer the open
reviews addressed to the URI it is asked about.
"""
from __future__ import annotations

import asyncio
import errno
import json
import logging
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any, Awaitable, Callable, MutableMapping, NamedTuple, Optional, Union
from urllib.parse import parse_qs, unquote, urlsplit

from .coordinator import Coordinator, CoordinatorOptions
from .storage.sqlite import SqliteStore
from .storage.store import MemoryStore

__all__ = [
    "ANALYTICS_CSP",
    "ChapDesk",
    "ChapServer",
    "DESK_CSP",
    "MAX_BODY_BYTES",
    "bootstrap",
    "list_tasks",
    "load_config",
    "make_coordinator",
    "make_server",
    "open_reviews",
    "public_config",
    "security_headers",
    "serve",
    "task_evidence",
    "task_view",
]

logger = logging.getLogger(__name__)

#: The largest request body read: a little above the coordinator's envelope limit.
MAX_BODY_BYTES = 1_100_000

#: The desk's policy. Its scripts and styles are its own files; the style
#: attributes its views set are allowed, a style element is not, and its
#: icon is a data: URL. It reaches this server only, and no page may frame it.
DESK_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; "
            "frame-ancestors 'none'")

#: The policy of the pages under /analytics/: their scripts run, in an
#: origin of their own, and no page may frame them.
ANALYTICS_CSP = "sandbox allow-scripts; frame-ancestors 'none'"

TYPES = {
    ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8", ".svg": "image/svg+xml", ".json": "application/json", ".png": "image/png",
    ".ico": "image/x-icon", ".txt": "text/plain; charset=utf-8", ".md": "text/markdown; charset=utf-8",
    ".jsonl": "application/x-ndjson",
}


def security_headers(path: str) -> list[tuple[str, str]]:
    """The headers every response carries; ``path`` is the request's path."""
    return [
        ("content-security-policy", ANALYTICS_CSP if path.startswith("/analytics/") else DESK_CSP),
        ("x-frame-options", "DENY"),
        ("x-content-type-options", "nosniff"),
        ("referrer-policy", "no-referrer"),
    ]


# -- configuration ------------------------------------------------------------

def env_flag(name: str, fallback: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return fallback
    return value == "1" or value.lower() == "true"


def env_list(name: str) -> list[str]:
    """A comma-separated environment variable as a list, empty when unset."""
    return [part.strip() for part in (os.environ.get(name) or "").split(",") if part.strip()]


def has_profile(profiles: list[str], name: str) -> bool:
    """Whether a profile list names ``name`` at any version."""
    return any(p == name or p.startswith(name + "/") for p in profiles or [])


#: CHAP_REQUIRE_SIGNATURES, as chap.config.json's require_signatures.
_SIGNATURES_FROM_ENV = {"1": True, "true": True, "humans": "humans", "0": False, "false": False}


def _signatures(config: dict) -> Union[bool, str]:
    """``require_signatures`` from CHAP_REQUIRE_SIGNATURES or the file: True,
    False or "humans". A value that is none of these is refused, so a typing
    slip does not turn signatures off."""
    raw = (os.environ.get("CHAP_REQUIRE_SIGNATURES") or "").strip()
    if raw:
        if raw.lower() not in _SIGNATURES_FROM_ENV:
            raise ValueError(f"CHAP_REQUIRE_SIGNATURES is {raw!r}; it takes 1, true, humans, 0 or false")
        value = _SIGNATURES_FROM_ENV[raw.lower()]
    else:
        value = config.get("require_signatures", False)
        if value is not True and value is not False and value != "humans":
            raise ValueError(f"require_signatures in chap.config.json is {json.dumps(value)}; "
                             'it takes true, false or "humans"')
    if value is False and has_profile(config["profiles"], "security-signed"):
        return True
    return value


def _project_path(value: str, base: Path) -> str:
    """A path from the configuration, against the config file's directory;
    a leading ~ is the home directory, and ":memory:" stays as it is."""
    if value == ":memory:":
        return value
    return os.path.normpath(os.path.join(base, os.path.expanduser(value)))


def load_config(path: Union[str, os.PathLike, None] = None) -> dict:
    """Read chap.config.json and apply the environment overrides.

    ``path`` is the file, by default CHAP_CONFIG, else ``chap.config.json``
    in the current directory. PORT, CHAP_HOST and CHAP_DB_PATH replace the
    port, host and store; CHAP_REQUIRE_SIGNATURES (``1``, ``true``,
    ``humans``, ``0`` or ``false``) and CHAP_CHAIN (``1`` or ``true``)
    replace the two settings; CHAP_ALLOWED_HOSTS adds host names, comma
    separated.

    ``store`` and ``analytics`` are paths against the config file's
    directory, from wherever the server starts, and a leading ``~`` is the
    home directory; CHAP_DB_PATH follows the same rule. ``store`` defaults
    to ``./data/chap.db``, and ``":memory:"`` keeps the store in the process.
    ``analytics``, by default ``./analytics``, is where the analytics script
    writes the pages served under ``/analytics/``. Both come back absolute.

    ``require_signatures`` is ``true`` (every call is signed), ``"humans"``
    (a person's calls are signed) or ``false``. It comes back as a flag,
    with ``signature_policy`` saying ``"all"`` or ``"humans"``.

    A profile in the list is enforced: security-signed/1.0 requires
    signatures, and the coordinator turns the chain on for a workspace that
    advertises audit-scitt/1.0 whatever the flag says, so each setting
    reports the profile as well as its own value.
    """
    config_path = Path(os.path.abspath(os.path.expanduser(
        path if path is not None else os.environ.get("CHAP_CONFIG") or "chap.config.json")))
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or not isinstance(config.get("workspace"), str) or not config["workspace"]:
        raise ValueError(f"{config_path} names no workspace")
    if not isinstance(config.get("profiles"), list) or not all(isinstance(p, str) for p in config["profiles"]):
        raise ValueError(f"{config_path} needs profiles, a list such as [\"core/1.0\", \"review/1.0\"]")
    base = config_path.parent
    config["port"] = int(os.environ.get("PORT") or config.get("port") or 8787)
    config["host"] = os.environ.get("CHAP_HOST") or config.get("host") or "127.0.0.1"
    config["store"] = _project_path(os.environ.get("CHAP_DB_PATH") or config.get("store") or "./data/chap.db", base)
    config["analytics"] = _project_path(config.get("analytics") or "./analytics", base)
    signatures = _signatures(config)
    config["require_signatures"] = signatures is not False
    config["signature_policy"] = "humans" if signatures == "humans" else "all"
    config["chain"] = (env_flag("CHAP_CHAIN", bool(config.get("chain", False)))
                       or has_profile(config["profiles"], "audit-scitt"))
    config["allowed_hosts"] = [*(config.get("allowed_hosts") or []), *env_list("CHAP_ALLOWED_HOSTS")]
    config.setdefault("humans", [])
    config.setdefault("agent", None)
    return config


def reconcile_signed_profile(config: dict) -> None:
    """security-signed/1.0 is enforced by require_signatures, and the coordinator
    adds the profile where signatures are required (SPECIFICATION 15.1, item
    3). load_config already requires signatures where the profile is listed;
    this adds the profile where the flag alone asked for them, so the console,
    /api/config and the descriptor say the same thing."""
    if config.get("require_signatures") and not has_profile(config["profiles"], "security-signed"):
        config["profiles"] = [*config["profiles"], "security-signed/1.0"]
        print("security-signed/1.0 added to the profiles: signatures are required.")


# -- the coordinator ----------------------------------------------------------

def open_store(store_path: str):
    """A path opens SQLite; ":memory:" keeps everything in the process."""
    if store_path == ":memory:":
        return MemoryStore()
    Path(store_path).parent.mkdir(parents=True, exist_ok=True)
    return SqliteStore(store_path)


def make_coordinator(config: dict) -> Coordinator:
    """Build the coordinator from a config and bootstrap its workspace."""
    reconcile_signed_profile(config)
    coord = Coordinator(CoordinatorOptions(
        store=open_store(config["store"]),
        default_profiles=list(config["profiles"]),
        enable_chain=bool(config.get("chain")),
        require_signatures=bool(config.get("require_signatures")),
    ))
    bootstrap(coord, config)
    return coord


def bootstrap(coord: Coordinator, config: dict) -> None:
    """Create the workspace and join the configured participants.

    With signatures required, nobody is joined here: each participant joins
    itself with its own key, since a re-join cannot add a key for someone.
    The desk does this for a human; an agent joins with its key file.
    """
    def send(method: str, params: dict) -> dict:
        return coord.dispatch({
            "jsonrpc": "2.0", "id": f"boot-{method}", "method": method,
            "params": {"workspace": config["workspace"], **params},
        })

    if coord.get_workspace(config["workspace"]) is None:
        params: dict[str, Any] = {"profiles": list(config["profiles"])}
        if config.get("mode"):
            params["mode"] = config["mode"]
        if config.get("mode_ceiling"):
            params["mode_ceiling"] = config["mode_ceiling"]
        r = send("workspace.create", params)
        if "error" in r:
            raise RuntimeError(f"workspace.create: {r['error']['message']}")
    if config.get("require_signatures"):
        return
    ws = coord.get_workspace(config["workspace"])
    members = [{**h, "type": "human"} for h in (config.get("humans") or [])]
    if config.get("agent"):
        members.append({**config["agent"], "type": "agent"})
    for m in members:
        if m["uri"] in ws.members:
            continue
        default_role = "reviewer" if m["type"] == "human" else "drafter"
        r = send("participant.join", {
            "from": m["uri"], "type": m["type"], "role": m.get("role") or default_role,
            "display_name": m.get("display_name"),
        })
        if "error" in r:
            raise RuntimeError(f"participant.join {m['uri']}: {r['error']['message']}")


def _close(coord: Coordinator) -> None:
    store = coord.options.store
    if store is not None:
        store.close()


# -- what the read API shows --------------------------------------------------

def _as_dict(value: Any) -> Any:
    """A dataclass with to_dict, a dict, or None, as plain JSON data."""
    if value is None or isinstance(value, dict):
        return value
    to_dict = getattr(value, "to_dict", None)
    return to_dict() if callable(to_dict) else value


def open_reviews(coord: Coordinator, workspace: str, reviewer: Optional[str] = None) -> list[dict]:
    """The open reviews a reviewer can act on, read from the owning process."""
    ws = coord.get_workspace(workspace)
    if ws is None:
        return []
    submissions = decision_log(ws)["\0submissions"]
    out = []
    for task in ws.tasks.values():
        if task.state != "review_requested" or not task.review:
            continue
        review = _as_dict(task.review)
        if reviewer and reviewer not in review["requested_to"]:
            continue
        artefact = task.pending_artefact if task.pending_artefact is not None else task.output
        out.append({
            "task_id": task.id,
            "kind": task.kind,
            "state": task.state,
            "assignee": task.assignee,
            "input": task.input,
            "artefact": artefact,
            "reviewers": review["requested_to"],
            "rule": review["rule"],
            "requested_at": review["requested_at"],
            "decisions": review["decisions"],
            "submission": submissions.get(task.id),
        })
    return sorted(out, key=lambda r: r["requested_at"])


def decision_log(ws: Any) -> dict[str, list[dict]]:
    """Every accepted decide.* call on the chain, by task, in order. A review
    opened again with review.request starts with no decisions, so the task's
    own review holds the current round only; this holds every round, with
    the note, the rationale, the tags and whether a revision was asked for.
    One scan of the log per request."""
    by_task: dict[str, list[dict]] = {}
    submissions: dict[str, dict] = {}
    for entry in ws.audit:
        call = getattr(entry, "envelope", None)
        if not isinstance(call, dict):
            continue
        p = call.get("params") or {}
        method = str(call.get("method", ""))
        if not isinstance(p.get("task_id"), str):
            continue
        if method in ("task.complete", "review.request"):
            # The latest submission opens the current review round. A reviewer's
            # decision names it, so an approval cannot be counted in another round.
            submissions[p["task_id"]] = {"seq": entry.seq, "arrived": entry.arrived, "envelope": call}
            continue
        if not method.startswith("decide."):
            continue
        by_task.setdefault(p["task_id"], []).append({
            "seq": entry.seq, "ts": entry.arrived, "reviewer": p.get("from"),
            "kind": method[len("decide."):], "comment": p.get("comment"),
            "rationale": p.get("rationale"), "tags": p.get("tags") if isinstance(p.get("tags"), list) else [],
            "request_revision": p.get("request_revision") is True, "round": p.get("round"),
        })
    by_task["\0submissions"] = submissions  # type: ignore[assignment]
    return by_task


def _view_of(ws: Any, task: Any, log: Optional[dict] = None) -> dict:
    """A task as the read API shows it. ``artefact`` is what is or was under
    review, whatever was decided. An override's rationale lives on the
    override artefact, and is put on the decision here as ``rationale``.
    ``decision_log`` is every decision on the task, across review rounds."""
    artefact = task.pending_artefact if task.pending_artefact is not None else task.output
    review = _as_dict(task.review)
    if review:
        decisions = []
        for d in review.get("decisions") or []:
            d = dict(d)
            override_id = d.get("override_artefact_id")
            if override_id:
                override = _as_dict(ws.overrides.get(override_id))
                d["rationale"] = (override or {}).get("rationale")
                d.setdefault("comment", d["rationale"])
                if d.get("comment") is None:
                    d["comment"] = d["rationale"]
            decisions.append(d)
        review = {**review, "decisions": decisions}
    return {
        "task_id": task.id, "kind": task.kind, "state": task.state, "assignee": task.assignee,
        "mode": task.mode, "created_at": task.created_at, "updated_at": task.updated_at,
        "input": task.input, "output": task.output, "artefact": artefact, "review": review,
        "decision_log": (log or {}).get(task.id, []) if log is not None else None,
        "submission": ((log or {}).get("\0submissions") or {}).get(task.id),
        "history": [_as_dict(h) for h in task.history],
    }


def task_view(coord: Coordinator, workspace: str, task_id: str) -> Optional[dict]:
    """One task, for an agent waiting on a decision and for the desk's lists."""
    ws = coord.get_workspace(workspace)
    task = ws.tasks.get(task_id) if ws is not None else None
    return _view_of(ws, task, decision_log(ws)) if task is not None else None


def list_tasks(coord: Coordinator, workspace: str, kind: Optional[str] = None, state: Optional[str] = None,
               limit: Optional[int] = None) -> list[dict]:
    """The workspace's tasks, newest first, narrowed by kind and by state when
    given (several states comma separated)."""
    ws = coord.get_workspace(workspace)
    if ws is None:
        return []
    states = {x.strip() for x in state.split(",") if x.strip()} if state else None
    log = decision_log(ws)
    out = [_view_of(ws, t, log) for t in ws.tasks.values()
           if (not kind or t.kind == kind) and (states is None or t.state in states)]
    out.sort(key=lambda v: v.get("created_at") or "", reverse=True)
    return out[:limit] if limit else out


def task_evidence(coord: Coordinator, workspace: str, task_id: str) -> Optional[dict]:
    """The evidence behind a task's decisions: the task, the accepted
    task.complete and review.request entries from the chain for it (each
    carries the artefact as submitted, and each opens a review round), the
    accepted decide.* entries (signed envelopes under security-signed/1.0),
    the keys on record for the reviewers who decided and for the assignee,
    and the chain head. A committer writes this beside what it commits, and a
    verifier reads it."""
    ws = coord.get_workspace(workspace)
    task = ws.tasks.get(task_id) if ws is not None else None
    if task is None:
        return None
    decisions: list[dict] = []
    submissions: list[dict] = []
    for entry in ws.audit:
        call = getattr(entry, "envelope", None)
        if not isinstance(call, dict) or (call.get("params") or {}).get("task_id") != task_id:
            continue
        row = {"seq": entry.seq, "arrived": entry.arrived, "prev_hash": entry.prev_hash, "envelope": call}
        method = str(call.get("method", ""))
        if method.startswith("decide."):
            decisions.append(row)
        elif method in ("task.complete", "review.request"):
            submissions.append(row)

    def jwks(member) -> list:
        out = []
        for k in member.keys or []:
            record = _as_dict(k) if not isinstance(k, dict) else k
            if not record.get("revoked_at"):
                out.append(record.get("jwk"))
        return out

    keys: dict[str, list] = {}
    for uri in [*((d["envelope"].get("params") or {}).get("from") for d in decisions), task.assignee]:
        member = ws.members.get(uri) if isinstance(uri, str) else None
        if member is not None and uri not in keys:
            keys[uri] = [(k.jwk if hasattr(k, "jwk") else k.get("jwk")) for k in (member.keys or [])]
    # Every member's type and public keys, so a checker can tell a person
    # from an agent by the workspace's own record.
    members = {uri: {"type": m.type, "role": m.role, "keys": jwks(m)} for uri, m in ws.members.items()}
    return {"workspace": workspace, "task": _view_of(ws, task, decision_log(ws)), "submissions": submissions,
            "decisions": decisions, "keys": keys, "members": members, "chain_head": ws.chain_head,
            "chain_enabled": bool(getattr(ws, "chain_enabled", False))}


def public_config(config: dict) -> dict:
    return {
        "workspace": config["workspace"],
        "profiles": config["profiles"],
        "humans": [{"uri": h.get("uri"), "display_name": h.get("display_name"), "role": h.get("role")}
                   for h in (config.get("humans") or [])],
        "agent": config.get("agent"),
        "require_signatures": bool(config.get("require_signatures")),
        "chain_enabled": bool(config.get("chain")),
        "persistent": config["store"] != ":memory:",
        "oidc": None,
        "mcp": False,
    }


# -- the routes ---------------------------------------------------------------

class _Reply(NamedTuple):
    status: int
    body: bytes
    content_type: str = "application/json"
    headers: tuple[tuple[str, str], ...] = ()


def _json(status: int, body: Any, headers: tuple[tuple[str, str], ...] = ()) -> _Reply:
    return _Reply(status, json.dumps(body, ensure_ascii=False).encode("utf-8"), "application/json", headers)


def _too_large() -> _Reply:
    return _json(413, {"error": f"the request body is over {MAX_BODY_BYTES} bytes"})


def _header(headers: list[tuple[str, str]], name: str) -> str:
    """The first value of a header, or an empty string."""
    return next((value for key, value in headers if key == name), "")


def read_desk() -> dict[str, tuple[bytes, str]]:
    """The desk's files from the package, read once: name -> (bytes, content type)."""
    desk: dict[str, tuple[bytes, str]] = {}
    for entry in files(__package__).joinpath("desk").iterdir():
        content_type = TYPES.get(os.path.splitext(entry.name)[1])
        if content_type and entry.is_file() and not entry.name.startswith("."):
            desk[entry.name] = (entry.read_bytes(), content_type)
    if "index.html" not in desk:
        raise RuntimeError("The desk is missing from this installation of chap-coordinator; reinstall the package.")
    return desk


Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]


class ChapDesk:
    """The desk's routes over one coordinator: an ASGI app, and what the
    standard library server from :func:`make_server` runs for each request.

    ``config`` is what :func:`load_config` returns and ``coord`` the
    coordinator :func:`make_coordinator` built from it. The desk's pages
    name their files from ``/``, so mount it at the root, after your own
    routes: ``app.mount("/", ChapDesk(config, coord))``. Under an ASGI
    server the calls run on asyncio's default thread pool, under ``lock``.
    """

    def __init__(self, config: dict, coord: Coordinator) -> None:
        self.config = config
        self.coord = coord
        self.lock = threading.Lock()
        self.desk = read_desk()

    def allowed_hosts(self, port: Optional[int]) -> set[str]:
        """The host names this server answers to: its loopback names on its
        own port, the configured host, and ``allowed_hosts`` from
        chap.config.json or CHAP_ALLOWED_HOSTS, for a name a proxy or a
        Compose service reaches it by."""
        allowed = {h.lower() for h in self.config.get("allowed_hosts") or []}
        if port is not None:
            allowed |= {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}",
                        f"{self.config['host']}:{port}".lower()}
        return allowed

    def check(self, method: str, path: str, headers: list[tuple[str, str]], port: Optional[int]) -> Optional[_Reply]:
        """The answer to a request refused before its body is read, or None.

        ``headers`` are the request's, names in lower case; ``port`` is the
        one the server listens on.

        A request whose Host is not one of the server's names is refused
        first, so a page that resolves its own name to this address arrives
        with that name and gets nothing. The desk is served from here, so
        its requests carry one of the same names in their Origin, under http
        or under the https a proxy in front terminates. A page on another
        origin, or a sandboxed one whose origin is ``null``, is refused; two
        Origin headers name no one origin and are refused too.
        """
        allowed = self.allowed_hosts(port)
        if _header(headers, "host").lower() not in allowed:
            return _json(421, {"error": "unknown host; set allowed_hosts in chap.config.json, "
                                        "or CHAP_ALLOWED_HOSTS, to serve under another name"})
        origins = [value for key, value in headers if key == "origin"]
        if len(origins) > 1 or (origins and urlsplit(origins[0]).netloc.lower() not in allowed):
            return _json(403, {"error": "cross-origin requests are refused"})
        if method not in ("GET", "POST"):
            return _json(405, {"error": f"{method} is not served here"}, (("allow", "GET, POST"),))
        if method == "POST":
            if path == "/chap" and not _header(headers, "content-type").lower().startswith("application/json"):
                return _json(415, {"error": "POST /chap takes application/json"})
            declared = _header(headers, "content-length")
            if declared and not re.fullmatch(r"[0-9]+", declared):
                return _json(400, {"error": "Content-Length is not a number"})
            if declared and int(declared) > MAX_BODY_BYTES:
                return _too_large()
        return None

    def handle(self, method: str, path: str, query: str, body: bytes) -> _Reply:
        """The answer to a request that passed :meth:`check`. A fault is
        logged here with its traceback, and the client is told only that it
        happened."""
        try:
            return self._route(method, path, query, body)
        except Exception:  # noqa: BLE001 - the detail goes to the log, never to the client
            logger.exception("%s %s failed", method, path)
            return _json(500, {"error": "internal error; the server's log has the detail"})

    def _route(self, method: str, path: str, query: str, body: bytes) -> _Reply:
        config, coord, workspace = self.config, self.coord, self.config["workspace"]
        if path in ("/", "/desk", "/index.html"):
            return _Reply(200, *self.desk["index.html"])
        if method == "GET" and path[1:] in self.desk:
            return _Reply(200, *self.desk[path[1:]])
        if method == "GET" and path.startswith("/analytics/"):
            return self._analytics(path[len("/analytics/"):] or "index.html")
        if path == "/chap" and method == "POST":
            try:
                envelope = json.loads(body)
            except ValueError:
                return _json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            with self.lock:
                return _json(200, coord.dispatch(envelope))
        if path == "/api/config":
            return _json(200, public_config(config))
        query_params = parse_qs(query)
        first = lambda name: (query_params.get(name) or [None])[0] or None  # noqa: E731
        if path == "/api/reviews":
            with self.lock:
                return _json(200, {"reviews": open_reviews(coord, workspace, first("reviewer"))})
        if path == "/api/tasks":
            raw_limit = first("limit")
            if raw_limit is not None and not re.fullmatch(r"[0-9]+", raw_limit):
                return _json(400, {"error": "limit is a whole number"})
            limit = int(raw_limit or 0) or None
            with self.lock:
                return _json(200, {"tasks": list_tasks(coord, workspace, first("kind"), first("state"), limit)})
        if path.startswith("/api/tasks/"):
            task_id, *rest = path[len("/api/tasks/"):].split("/")
            if rest == ["evidence"]:
                with self.lock:
                    evidence = task_evidence(coord, workspace, task_id)
                return _json(200, evidence) if evidence else _json(404, {"error": "unknown task"})
            if rest:
                return _json(404, {"error": "not found"})
            with self.lock:
                view = task_view(coord, workspace, task_id)
            return _json(200, view) if view else _json(404, {"error": "unknown task"})
        if path == "/api/health":
            with self.lock:
                ws = coord.get_workspace(workspace)
                return _json(200, {
                    "ok": True, "workspace": workspace,
                    "members": len(ws.members) if ws else 0,
                    "tasks": len(ws.tasks) if ws else 0,
                    "audit": len(ws.audit) if ws else 0,
                })
        return _json(404, {"error": "not found"})

    def _analytics(self, name: str) -> _Reply:
        """A page chap-analytics wrote, read on each request, so a report
        written again while the server runs is served as it is now. A name
        is one file in the directory: no separator, drive or dot file."""
        content_type = TYPES.get(os.path.splitext(name)[1])
        directory = self.config.get("analytics")
        if (not content_type or not directory or name.startswith(".")
                or any(c in name for c in ("/", "\\", ":", "\0"))):
            return _json(404, {"error": "not found"})
        try:
            return _Reply(200, (Path(directory) / name).read_bytes(), content_type)
        except (OSError, ValueError):
            return _json(404, {"error": "no analytics page by that name; run the analytics script to write them"})

    # -- ASGI ----------------------------------------------------------------

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            raise RuntimeError(f"ChapDesk serves HTTP, and was given a {scope['type']} connection")
        method, path = scope["method"], _route_path(scope)
        headers = [(name.decode("latin-1").lower(), value.decode("latin-1")) for name, value in scope["headers"]]
        reply = self.check(method, path, headers, (scope.get("server") or (None, None))[1])
        if reply is None:
            body = await _read_body(receive) if method == "POST" else b""
            if body is None:
                reply = _too_large()
            else:
                query = scope.get("query_string", b"").decode("latin-1")
                reply = await asyncio.get_running_loop().run_in_executor(None, self.handle, method, path, query, body)
        await send({"type": "http.response.start", "status": reply.status,
                    "headers": [(name.encode("latin-1"), value.encode("latin-1"))
                                for name, value in _response_headers(path, reply)]})
        await send({"type": "http.response.body", "body": reply.body})


def _route_path(scope: Scope) -> str:
    """The request's path below the app's mount point, as Starlette finds it."""
    path, root = scope["path"], scope.get("root_path", "")
    if root and path.startswith(root) and (len(path) == len(root) or path[len(root)] == "/"):
        path = path[len(root):]
    return path or "/"


async def _read_body(receive: Receive) -> Optional[bytes]:
    """The request's body, or None once it is over MAX_BODY_BYTES, when no
    more of it is read."""
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] != "http.request":
            break
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            return None
        chunks.append(chunk)
        if not message.get("more_body"):
            break
    return b"".join(chunks)


def _response_headers(path: str, reply: _Reply) -> list[tuple[str, str]]:
    return [("content-type", reply.content_type), ("content-length", str(len(reply.body))),
            *reply.headers, *security_headers(path)]


# -- the standard library server ----------------------------------------------

class ChapServer(ThreadingHTTPServer):
    """The standard library's HTTP server, running a :class:`ChapDesk`."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], desk: ChapDesk) -> None:
        self.desk = desk
        super().__init__(address, _Handler)

    @property
    def base(self) -> str:
        return f"http://{self.desk.config['host']}:{self.server_port}"


class _Handler(BaseHTTPRequestHandler):
    server_version = "CHAPDesk"
    server: ChapServer

    def log_message(self, *_: Any) -> None:
        pass

    def end_headers(self) -> None:
        # Every response carries the security headers, the server's own
        # error pages for a request it could not parse included.
        path = unquote(urlsplit(getattr(self, "path", "")).path)
        for name, value in security_headers(path):
            self.send_header(name, value)
        super().end_headers()

    def _serve(self) -> None:
        desk = self.server.desk
        url = urlsplit(self.path)
        path = unquote(url.path)
        headers = [(name.lower(), value) for name, value in self.headers.items()]
        try:
            reply = desk.check(self.command, path, headers, self.server.server_port)
            if reply is None:
                # check() has read Content-Length on a POST: a number, and
                # under the limit.
                size = int(self.headers.get("content-length") or 0) if self.command == "POST" else 0
                body = self.rfile.read(size) if size > 0 else b""
                reply = desk.handle(self.command, path, url.query, body)
            self.send_response(reply.status)
            for name, value in [("content-type", reply.content_type), ("content-length", str(len(reply.body))),
                                *reply.headers]:
                self.send_header(name, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(reply.body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    do_GET = do_POST = do_HEAD = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _serve


def make_server(config: dict, coord: Coordinator) -> ChapServer:
    """Bind the standard library server on the configured host and port.
    Port 0 picks a free port; read it from ``server.server_port``."""
    return ChapServer((config["host"], int(config["port"])), ChapDesk(config, coord))


def serve(config: dict) -> int:
    """Run the desk server until it is interrupted, and return the exit code:
    0 after Ctrl-C, 1 when the port is taken. ``config`` is what
    :func:`load_config` returns."""
    coord = make_coordinator(config)
    try:
        server = make_server(config, coord)
    except OSError as err:
        _close(coord)
        if err.errno != errno.EADDRINUSE:
            raise
        print(f"Port {config['port']} on {config['host']} is in use, by another desk or another program. "
              "Stop that one, or set PORT to a free port.", file=sys.stderr)
        return 1
    base = server.base
    flags = (", chain on" if config.get("chain") else "") + (", signatures required" if config.get("require_signatures") else "")
    print(f"CHAP {config['workspace']} on {base}")
    print(f"  desk     {base}/")
    print(f"  calls    POST {base}/chap")
    print(f"  profiles {', '.join(config['profiles'])}")
    print(f"  store    {config['store']}{flags}")
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        _close(coord)
    return 0
