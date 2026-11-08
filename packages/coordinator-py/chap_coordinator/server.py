"""
chap_coordinator.server
=======================

The desk server: the one process that owns a store, runs the coordinator
on it, and serves

    GET  /              the review desk
    POST /chap          CHAP calls as JSON-RPC, for any agent or client
    GET  /api/...       what the desk reads: the config, the tasks and reviews,
                        the members with their keys, and the chain
    GET  /analytics/    the pages chap-analytics wrote, when they exist

and, with ``notify`` set, tells people that a review or a question waits
for them, on the console and to a webhook (:class:`Notifier`).

Two servers run the same routes. :func:`serve` runs them on the standard
library's HTTP server, as ``chap-coordinator serve`` does, and
:class:`ChapDesk` is the routes as an ASGI app, to mount in FastAPI or
Starlette. Everything comes from ``chap.config.json``, read by
:func:`load_config`::

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
- every response carries SECURITY_HEADERS, the Node server's: a
  Content-Security-Policy that allows no inline script and no framing,
  ``X-Frame-Options: DENY``, ``nosniff`` and ``Referrer-Policy:
  no-referrer``, so no other page can frame the desk;
- a page under ``/analytics/`` is served with ANALYTICS_POLICY, ``sandbox
  allow-scripts``: it runs in an origin of its own, where it reads nothing
  the desk keeps, and its calls here are refused as cross-origin.

The desk's pages name their files relative to the page, so the ASGI app
serves the desk mounted at any path. The standard library server is
opened at ``http://localhost:<port>/``, where it listens on this machine:
the desk may sign with WebAuthn, which needs a domain name, and an IP
address is none. On the loopback address it listens on IPv4 and IPv6
alike, so ``localhost`` reaches it whichever address the name resolves to.

One process owns one store. The coordinator is a single writer, and the
server makes every call and every read under one lock, ``ChapDesk.lock``;
code of your own that uses the same coordinator in the same process takes
it too. Other processes reach the server over HTTP, with
:mod:`chap_coordinator.client`.

A ``human:`` URI labels a participant; it does not authenticate a person.
Under security-signed/1.0 the desk signs each decision in the browser with
the reviewer's own key. Which reviews a person is shown is the deployment's
decision, as SPECIFICATION 15.1 says; the desk shows a reviewer the open
reviews addressed to the URI it is asked about.
"""
from __future__ import annotations

import asyncio
import errno
import hashlib
import hmac
import http.client
import json
import logging
import os
import queue
import re
import secrets
import socket
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, MutableMapping, NamedTuple, Optional, Union
from urllib.parse import parse_qs, quote, unquote, urlsplit

from . import __version__
from .canonical import canonicalize
from .coordinator import Coordinator, CoordinatorOptions
from .storage.sqlite import SqliteStore
from .storage.store import MemoryStore

__all__ = [
    "ANALYTICS_POLICY",
    "ChapDesk",
    "ChapServer",
    "MAX_BODY_BYTES",
    "Notifier",
    "SECURITY_HEADERS",
    "bootstrap",
    "desk_url",
    "list_members",
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

#: The headers on every answer, the Node server's own. The policy lets the
#: desk load its own scripts, styles and images and call its own server,
#: and nothing else: no inline script or style, no plugin, no form posted
#: anywhere, no frame around it.
SECURITY_HEADERS = {
    "content-security-policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
                               "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    "x-frame-options": "DENY",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
}

#: The policy for the analytics pages: their scripts run, in an origin of their own.
ANALYTICS_POLICY = "sandbox allow-scripts; frame-ancestors 'none'"

TYPES = {
    ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8", ".svg": "image/svg+xml", ".json": "application/json", ".png": "image/png",
    ".ico": "image/x-icon", ".txt": "text/plain; charset=utf-8", ".md": "text/markdown; charset=utf-8",
    ".jsonl": "application/x-ndjson",
}


def security_headers(path: str) -> list[tuple[str, str]]:
    """The headers every response carries; ``path`` is the request's path
    below the desk's mount point."""
    policy = ANALYTICS_POLICY if path.startswith("/analytics/") else SECURITY_HEADERS["content-security-policy"]
    return [(name, policy if name == "content-security-policy" else value) for name, value in SECURITY_HEADERS.items()]


#: The addresses of this machine alone, and the ones of every interface. A
#: server listening on either is opened at localhost.
_LOOPBACK = ("127.0.0.1", "::1", "localhost")
_WILDCARD = ("0.0.0.0", "::", "")


def desk_url(host: str, port: int) -> str:
    """Where a person opens the desk of a server listening on ``host`` and
    ``port``: ``http://localhost:<port>/`` on this machine, since the desk
    may sign with WebAuthn, which needs a domain name; else the host as
    given, bracketed where it is an IPv6 address."""
    shown = "localhost" if host in _LOOPBACK + _WILDCARD else f"[{host}]" if ":" in host else host
    return f"http://{shown}:{port}/"


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


#: How the desk signs where signatures are required, as the console says it.
DESK_SIGNING = {
    "auto": "the desk signs with a passkey where the browser has one, and otherwise with a key it keeps in the browser",
    "webauthn": "the desk signs with a passkey",
    "browser-key": "the desk signs with a key it keeps in the browser",
}


def _desk_signing(value: Any) -> str:
    """``desk_signing`` from CHAP_DESK_SIGNING or the file, as the Node
    server reads it: "auto", "webauthn" or "browser-key", the last where
    none is given."""
    if value is None or value == "":
        return "browser-key"
    if not isinstance(value, str) or value not in DESK_SIGNING:
        raise ValueError(f"desk_signing in chap.config.json, or CHAP_DESK_SIGNING, is {json.dumps(value)}; "
                         'it is "auto", "webauthn" or "browser-key"')
    return value


def _notify(value: Any) -> Optional[dict]:
    """``notify`` from chap.config.json, checked: ``console``, true or
    false, and ``webhook``, a URL and the secret its notices are signed
    with; None where neither is on. A setting that says something else is
    refused, so a typing slip does not leave people untold."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {"console", "webhook"}:
        raise ValueError('notify in chap.config.json takes {"console": true, "webhook": {"url": ..., "secret": ...}}')
    console, webhook = value.get("console", False), value.get("webhook")
    if not isinstance(console, bool):
        raise ValueError("notify.console in chap.config.json is true or false")
    if webhook is not None:
        if not isinstance(webhook, dict) or set(webhook) != {"url", "secret"}:
            raise ValueError('notify.webhook in chap.config.json takes {"url": ..., "secret": ...}')
        url, secret = webhook["url"], webhook["secret"]
        if not isinstance(url, str) or urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).hostname:
            raise ValueError(f"notify.webhook.url in chap.config.json is {json.dumps(url)}; it is an http or https URL")
        if not isinstance(secret, str) or not secret:
            raise ValueError("notify.webhook.secret in chap.config.json is the secret each notice is signed with: "
                             "a string of one character or more")
    return {"console": console, "webhook": webhook} if console or webhook else None


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
    port, host and store; port 0 picks a free port. CHAP_REQUIRE_SIGNATURES
    (``1``, ``true``, ``humans``, ``0`` or ``false``) and CHAP_CHAIN (``1``
    or ``true``) replace the two settings; CHAP_ALLOWED_HOSTS adds host
    names, comma separated.

    ``store`` and ``analytics`` are paths against the config file's
    directory, from wherever the server starts, and a leading ``~`` is the
    home directory; CHAP_DB_PATH follows the same rule. ``store`` defaults
    to ``./data/chap.db``, and ``":memory:"`` keeps the store in the process.
    ``analytics``, by default ``./analytics``, is where the analytics script
    writes the pages served under ``/analytics/``. Both come back absolute.

    ``require_signatures`` is ``true`` (every call is signed), ``"humans"``
    (a person's calls are signed, and a call from a member that is not a
    person need not be) or ``false``. It comes back as a flag, with
    ``signature_policy`` saying ``"all"``, ``"humans"``, or None where no
    call is signed. ``desk_signing``, or CHAP_DESK_SIGNING, says how the
    desk signs: ``"auto"``, a passkey where the browser has one that
    verifies its user and the browser key otherwise; ``"webauthn"``, a
    passkey; or ``"browser-key"``, a key the desk keeps in the browser, the
    default.

    ``notify`` is ``{"console": true, "webhook": {"url": ..., "secret":
    ...}}``, either part optional, or absent: see :class:`Notifier`.

    A profile in the list is enforced: security-signed/1.0 requires
    signatures, and the coordinator turns the chain on for a workspace that
    advertises audit-scitt/1.0 whatever the flag says, so each setting
    reports the profile as well as its own value.
    """
    config_path = Path(os.path.abspath(os.path.expanduser(
        path if path is not None else os.environ.get("CHAP_CONFIG") or "chap.config.json")))
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise ValueError(f"{config_path} is not JSON: {err}") from None
    if not isinstance(config, dict) or not isinstance(config.get("workspace"), str) or not config["workspace"]:
        raise ValueError(f"{config_path} names no workspace")
    if not isinstance(config.get("profiles"), list) or not all(isinstance(p, str) for p in config["profiles"]):
        raise ValueError(f"{config_path} needs profiles, a list such as [\"core/1.0\", \"review/1.0\"]")
    base = config_path.parent
    port = os.environ.get("PORT") or config.get("port")
    config["port"] = int(8792 if port is None or port == "" else port)
    config["host"] = os.environ.get("CHAP_HOST") or config.get("host") or "127.0.0.1"
    config["store"] = _project_path(os.environ.get("CHAP_DB_PATH") or config.get("store") or "./data/chap.db", base)
    config["analytics"] = _project_path(config.get("analytics") or "./analytics", base)
    signatures = _signatures(config)
    config["require_signatures"] = signatures is not False
    config["signature_policy"] = None if signatures is False else "humans" if signatures == "humans" else "all"
    config["desk_signing"] = _desk_signing(os.environ.get("CHAP_DESK_SIGNING") or config.get("desk_signing"))
    config["chain"] = (env_flag("CHAP_CHAIN", bool(config.get("chain", False)))
                       or has_profile(config["profiles"], "audit-scitt"))
    config["allowed_hosts"] = [*(config.get("allowed_hosts") or []), *env_list("CHAP_ALLOWED_HOSTS")]
    config["notify"] = _notify(config.get("notify"))
    config.setdefault("humans", [])
    config.setdefault("agent", None)
    _check_enrolment_code()
    return config


def _check_enrolment_code() -> None:
    """An enrolment code set through CHAP_ENROLMENT_CODE that a caller could
    guess is refused: a refused join is not recorded and nothing limits
    how many a caller sends."""
    set_code = (os.environ.get("CHAP_ENROLMENT_CODE") or "").strip()
    if set_code and len(_code_letters(set_code)) < 8:
        raise ValueError("CHAP_ENROLMENT_CODE needs 8 letters or digits at least; "
                         "unset it and the server makes a code of 10")


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
        # The policy a new workspace takes: under "humans" a call from a
        # member that is not a person need not be signed.
        signature_policy=config.get("signature_policy") or "all",
    ))
    bootstrap(coord, config)
    return coord


#: What each signature policy asks, as the console says it, in the Node server's words.
POLICY_WORDS = {"all": "every call after the join is signed",
                "humans": "people sign every call; an agent member's calls need no signature",
                None: "off: whoever reaches this server can decide as a person"}


def bootstrap(coord: Coordinator, config: dict) -> None:
    """Create the workspace and join the configured participants.

    With signatures off, the people and the agent are joined here. Where
    every call is signed, nobody is: each participant joins itself with its
    own key, since a join cannot add a key to a member who holds none. The
    desk does this for a person, and an agent joins with its key file.
    Under "humans" the agent is joined here, and calls unsigned as a member
    that is not a person; each person joins from the desk, with the key the
    browser makes for them.

    A workspace keeps the signature policy it was created with, and one
    that advertises security-signed/1.0 requires signatures whatever the
    configuration says. Where a store holds a workspace made under other
    settings, ``require_signatures`` and ``signature_policy`` are made to
    say what the workspace does, so the console and /api/config tell the
    agent and the desk how to call, and the console says what changed.
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
        rule = (config.get("review") or {}).get("rule")
        if rule:
            params["review_rule"] = rule
        r = send("workspace.create", params)
        if "error" in r:
            raise RuntimeError(f"workspace.create: {r['error']['message']}")
    ws = coord.get_workspace(config["workspace"])
    required = bool(config.get("require_signatures")) or ws.has_profile("security-signed")
    policy = ws.signature_policy if required else None
    if (required, policy) != (bool(config.get("require_signatures")), config.get("signature_policy")):
        print(f"{ws.id} was created under other settings, and runs with {POLICY_WORDS[policy]}. "
              "require_signatures in chap.config.json applies to a new store.")
        config["require_signatures"], config["signature_policy"] = required, policy
    members = []
    if policy is None:
        members += [{**h, "type": "human"} for h in (config.get("humans") or [])]
    if policy != "all" and config.get("agent"):
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
    closed: set[str] = set()
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
            if _amends_round(call, (submissions.get(p["task_id"]) or {}).get("envelope"),
                             p["task_id"] in closed):
                continue
            submissions[p["task_id"]] = {"seq": entry.seq, "arrived": entry.arrived, "envelope": call}
            closed.discard(p["task_id"])
            continue
        if method in _CLOSES_ROUND:
            closed.add(p["task_id"])
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


def list_members(coord: Coordinator, workspace: str) -> list[dict]:
    """The workspace's members and the keys each signs with now: the keys
    it joined or rotated to, less those revoked or rotated away. A member's
    public keys are no secret, and the desk reads them here to know whether
    the workspace holds its key for the reviewer, with no signed call."""
    ws = coord.get_workspace(workspace)
    if ws is None:
        return []
    return [{"uri": m.uri, "type": m.type, "role": m.role,
             **({"display_name": m.display_name} if m.display_name else {}),
             "keys": [k.jwk for k in m.keys if not k.revoked_at and not k.valid_until]}
            for m in ws.members.values()]


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
    closed = False
    for entry in ws.audit:
        call = getattr(entry, "envelope", None)
        if not isinstance(call, dict) or (call.get("params") or {}).get("task_id") != task_id:
            continue
        row = {"seq": entry.seq, "arrived": entry.arrived, "prev_hash": entry.prev_hash, "envelope": call}
        method = str(call.get("method", ""))
        closed = closed or method in _CLOSES_ROUND
        if method.startswith("decide."):
            decisions.append(row)
        elif method in ("task.complete", "review.request"):
            # A review.request that adds reviewers opens no round: the code
            # gate's note names the submission that opened the round its
            # approvals name.
            if _amends_round(call, submissions[-1]["envelope"] if submissions else None, closed):
                continue
            submissions.append(row)
            closed = False

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


def unnamed_join(envelope: Any, config: dict) -> dict | None:
    """The refusal of a join under a name chap.config.json does not give.

    The server admits the people, the agent and the operator it is
    configured with. A participant.join for any other URI is refused before
    the coordinator sees it, so a process that can reach /chap cannot add a
    "person" of its own making and decide as them. None for every other call.
    """
    if not isinstance(envelope, dict) or envelope.get("method") != "participant.join":
        return None
    params = envelope.get("params")
    if not isinstance(params, dict) or params.get("workspace") != config.get("workspace"):
        return None
    named = {h.get("uri") for h in config.get("humans") or []}
    named |= {(config.get("agent") or {}).get("uri"), (config.get("operator") or {}).get("uri")}
    if params.get("from") in named - {None}:
        return None
    return {"jsonrpc": "2.0", "id": envelope.get("id"), "error": {"code": -32011, "message":
            f"{params.get('from')} is not a member chap.config.json names; add them to humans, or agent, to let them join"}}


#: Crockford's base 32: the letters of an enrolment code, none of them
#: mistaken for another.
_CODE_LETTERS = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def enrolment_code(config: dict) -> Optional[str]:
    """The enrolment code of a desk server, or None where it takes none.

    Where signatures are required and nobody signs in at an identity
    provider, the first join of a person chap.config.json names holds that
    name for good, with the key it carries. So that join needs a code the
    server prints when it starts: a process on this machine that does not
    see the server's console, an agent among them, cannot take a person's
    name before they open the desk. CHAP_ENROLMENT_CODE sets the code for a
    scripted setup; ``"enrolment": false`` in chap.config.json turns it off.
    """
    if "_enrolment_code" not in config:
        code = None
        if (config.get("require_signatures") and config.get("enrolment") is not False
                and config.get("humans")):
            code = (os.environ.get("CHAP_ENROLMENT_CODE") or "").strip() or None
            if code is None or len(_code_letters(code)) < 8:
                letters = "".join(_CODE_LETTERS[b % 32] for b in secrets.token_bytes(10))
                code = f"{letters[:5]}-{letters[5:]}"
        config["_enrolment_code"] = code
    return config["_enrolment_code"]


#: A value a call leaves out, apart from one it gives as null.
_ABSENT = object()


def _same_json(a: Any, b: Any) -> bool:
    """Whether two JSON values are the same, as the Node server compares
    them: 1 and 1.0 alike, true and 1 apart, a value left out apart from
    null."""
    if a is _ABSENT or b is _ABSENT:
        return a is b
    try:
        return canonicalize(a) == canonicalize(b)
    except (TypeError, ValueError):
        return False


#: The calls that close a review round, after which a review.request opens
#: another.
_CLOSES_ROUND = ("decide.reject", "decide.override", "abstain.declare")


def _amends_round(call: dict, before: Optional[dict], closed: bool) -> bool:
    """Whether a review.request adds reviewers to the round ``before``
    opened: it carries the artefact under review, and no reject, override or
    abstention has closed that round since. Such a request opens no round,
    and is no submission."""
    if call.get("method") != "review.request" or not isinstance(before, dict) or closed:
        return False
    was = before.get("params") or {}
    under_review = was["artefact"] if was.get("artefact") is not None else was.get("output", _ABSENT)
    return _same_json((call.get("params") or {}).get("artefact", _ABSENT), under_review)


def announce_enrolment(config: dict, coord: Coordinator, base: Optional[str] = None) -> None:
    """Print the enrolment code, with the people still to join with it."""
    code = enrolment_code(config)
    waiting = unenrolled(coord, config) if code else []
    if not waiting:
        return
    joins = "joins" if len(waiting) == 1 else "join"
    where = f"{base}/" if base else "the desk at its address with "
    print(f"  enrol    {code}: the code {', '.join(waiting)} {joins} with the first time, "
          f"or open {where}#enrol={code}", flush=True)


def _code_letters(code: Any) -> str:
    """A code as it is compared: its letters and digits, upper case."""
    return re.sub(r"[^0-9A-Z]", "", str(code or "").upper())


def unenrolled(coord: Coordinator, config: dict) -> list[str]:
    """The people chap.config.json names who have not joined yet, and so
    need the code."""
    ws = coord.get_workspace(config["workspace"])
    return [h.get("uri") for h in config.get("humans") or []
            if h.get("uri") and (ws is None or h.get("uri") not in ws.members)]


def unenrolled_join(envelope: Any, config: dict, coord: Coordinator,
                    headers: Iterable[tuple[str, str]]) -> dict | None:
    """The refusal of a person's first join without the enrolment code in
    its x-chap-enrolment header, before the coordinator sees it, so it is
    not recorded. None for every other call."""
    code = enrolment_code(config)
    if not code or not isinstance(envelope, dict) or envelope.get("method") != "participant.join":
        return None
    params = envelope.get("params")
    if not isinstance(params, dict) or params.get("workspace") != config.get("workspace"):
        return None
    if params.get("from") not in unenrolled(coord, config):
        return None
    given = _header(list(headers), "x-chap-enrolment")
    if hmac.compare_digest(_code_letters(given).encode(), _code_letters(code).encode()):
        return None
    return {"jsonrpc": "2.0", "id": envelope.get("id"), "error": {
        "code": -32011,
        "message": f"{params.get('from')} joins for the first time with the enrolment code the desk server "
                   "printed when it started",
        "data": {"reason": "enrolment"}}}


def public_config(config: dict, coord: Optional[Coordinator] = None) -> dict:
    ws = coord.get_workspace(config["workspace"]) if coord is not None else None
    return {
        "workspace": config["workspace"],
        "profiles": config["profiles"],
        "humans": [{"uri": h.get("uri"), "display_name": h.get("display_name"), "role": h.get("role")}
                   for h in (config.get("humans") or [])],
        "agent": config.get("agent"),
        "require_signatures": bool(config.get("require_signatures")),
        "signature_policy": config.get("signature_policy"),
        "desk_signing": config.get("desk_signing") or "browser-key",
        # What the store's workspace keeps: a store created with the chain
        # off keeps it off whatever chap.config.json says now.
        "chain_enabled": bool(ws.chain_enabled) if ws is not None else bool(config.get("chain")),
        "persistent": config["store"] != ":memory:",
        # Whether a person's first join needs the code the server printed
        # at start; never the code.
        "enrolment": bool(enrolment_code(config)),
        "oidc": None,
        "mcp": False,
    }


# -- notifications --------------------------------------------------------------

#: Control and bidirectional characters, which could rewrite a terminal.
_UNPRINTABLE = re.compile("[\x00-\x1f\x7f-\x9f‎‏‪-‮⁦-⁩]")


def _printable(text: str) -> str:
    """Text for a console line, each control or bidirectional character
    shown as its code point, as the Node server shows it."""
    return _UNPRINTABLE.sub(lambda m: f"<U+{ord(m.group()):04X}>", text)


class Notifier:
    """Tells people that a review or a question waits for them.

    It listens to the coordinator's log, which tells it of an entry once the
    entry is written, and notices two things in the desk's workspace: a
    review opened or reopened, by an accepted task.complete or
    review.request that leaves its task under review, and a question asked
    by an accepted whisper.ask. Each notice is a summary and a link to the
    task in the desk, which grant nothing: whoever follows the link decides
    only as the desk lets them. The notifier holds no key and makes no CHAP
    call.

    ``settings`` is ``notify`` from :func:`load_config`. With ``console``
    each notice is a line on standard output. With ``webhook`` it is
    POSTed to ``url`` as JSON, with ``X-Chap-Signature: sha256=<hex>``, the
    HMAC-SHA256 of the body under ``secret``, so the receiver can tell it
    came from here. Notices go out in order on a thread of their own, so
    no call waits on them; one the webhook does not take is logged and
    dropped.
    """

    def __init__(self, settings: dict, coord: Coordinator, workspace: str, desk: str) -> None:
        self.console = bool(settings.get("console"))
        self.webhook = settings.get("webhook")
        self.coord = coord
        self.workspace = workspace
        #: The desk's address, which each link names.
        self.desk = desk
        self._queue: queue.Queue = queue.Queue()
        threading.Thread(target=self._deliver, name="chap-notify", daemon=True).start()

    def __call__(self, workspace: str, entry: Any) -> None:
        """The audit listener: queue the notice an entry calls for, if any."""
        notice = self.notice(workspace, entry)
        if notice is not None:
            self._queue.put(notice)

    def notice(self, workspace: str, entry: Any) -> Optional[dict]:
        """The notice for a written entry, or None. Read under the
        coordinator's lock, as the call that wrote the entry ends."""
        call = getattr(entry, "envelope", None)
        ws = self.coord.get_workspace(workspace) if workspace == self.workspace else None
        if ws is None or not isinstance(call, dict) or not isinstance(call.get("params"), dict):
            return None
        params, method = call["params"], call.get("method")
        task = ws.tasks.get(params.get("task_id")) if isinstance(params.get("task_id"), str) else None
        if task is None:
            return None
        asker = params.get("from")
        extra: dict[str, Any] = {}
        if method in ("task.complete", "review.request"):
            if task.state != "review_requested" or task.review is None:
                return None
            event, to = "review.requested", list(task.review.requested_to)
            summary = f"{asker} asks {', '.join(to)} to review {task.kind} {task.id}"
        elif method == "whisper.ask":
            asked = [w for w in ws.whispers.values() if w.task_id == task.id and w.asker == asker]
            if not asked:
                return None
            event, to = "whisper.asked", list(asked[-1].askee)
            extra["whisper_id"] = asked[-1].id
            summary = f"{asker} asks {', '.join(to)} a question on {task.kind} {task.id}"
        else:
            return None
        return {"event": event, "workspace": workspace, "task_id": task.id, "kind": task.kind, "from": asker,
                "to": to, **extra, "summary": summary, "link": f"{self.desk}#task={quote(task.id, safe='')}",
                "seq": entry.seq, "at": entry.arrived}

    def _deliver(self) -> None:
        while True:
            notice = self._queue.get()
            if self.console:
                # The summary names what an agent sent, its kind and URIs.
                print(f"{_printable(notice['summary'])}: {notice['link']}", flush=True)
            if self.webhook:
                self._post(notice)

    def _post(self, notice: dict) -> None:
        """POST one notice to the webhook; a failure is logged, and the notice dropped."""
        url = urlsplit(self.webhook["url"])
        body = json.dumps(notice, ensure_ascii=False).encode("utf-8")
        signature = hmac.new(self.webhook["secret"].encode("utf-8"), body, hashlib.sha256).hexdigest()
        connection = (http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection)(
            url.hostname, url.port, timeout=10)
        try:
            connection.request("POST", (url.path or "/") + (f"?{url.query}" if url.query else ""), body, {
                "Content-Type": "application/json", "X-Chap-Signature": f"sha256={signature}",
                "User-Agent": f"chap-coordinator/{__version__}"})
            response = connection.getresponse()
            response.read()
            if not 200 <= response.status < 300:
                logger.warning("The webhook at %s answered HTTP %s to the notice of %s on %s; it is not sent again.",
                               self.webhook["url"], response.status, notice["event"], notice["task_id"])
        except (OSError, http.client.HTTPException) as err:
            logger.warning("The webhook at %s could not be reached (%s); the notice of %s on %s is not sent again.",
                           self.webhook["url"], err, notice["event"], notice["task_id"])
        finally:
            connection.close()


# -- the routes ---------------------------------------------------------------

#: What a caller is told of a call the store did not record.
STORE_FAILED = "The store did not record this call, so it took no effect; the server's log says why"


def _store_failure_said(response: dict) -> dict:
    """A response, with a call the store did not record said so plainly.

    Where a write fails, the coordinator answers -32603 with data
    ``{"reason": "store"}`` and puts the cause in its log, and a person or
    an agent would read "Internal error". The code and the data stay as
    they are."""
    error = response.get("error") if isinstance(response, dict) else None
    data = error.get("data") if isinstance(error, dict) else None
    if isinstance(data, dict) and data.get("reason") == "store" and error.get("code") == -32603:
        return {**response, "error": {**error, "message": STORE_FAILED}}
    return response


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
    name their files relative to the page, so it serves under any path:
    ``app.mount("/review", ChapDesk(config, coord))`` puts the desk at
    ``/review/``, and the mount point without its slash is sent there.
    Under an ASGI server the calls run on asyncio's default thread pool,
    under ``lock``.
    """

    def __init__(self, config: dict, coord: Coordinator, announce: bool = True) -> None:
        self.config = config
        self.coord = coord
        self.lock = threading.Lock()
        self.desk = read_desk()
        # Mounted in an app of its own, nothing else prints the code a
        # person's first join needs; serve() prints it with the address.
        if announce:
            announce_enrolment(config, coord)
        #: Tells people of what waits for them, where ``notify`` is set.
        self.notifier: Optional[Notifier] = None
        if config.get("notify"):
            self.notifier = Notifier(config["notify"], coord, config["workspace"],
                                     desk_url(config["host"], int(config["port"])))
            coord.add_audit_listener(self.notifier)

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

    def handle(self, method: str, path: str, query: str, body: bytes,
               headers: Iterable[tuple[str, str]] = ()) -> _Reply:
        """The answer to a request that passed :meth:`check`. A fault is
        logged here with its traceback, and the client is told only that it
        happened."""
        try:
            return self._route(method, path, query, body, list(headers))
        except Exception:  # noqa: BLE001 - the detail goes to the log, never to the client
            logger.exception("%s %s failed", method, path)
            return _json(500, {"error": "internal error; the server's log has the detail"})

    def _route(self, method: str, path: str, query: str, body: bytes,
               headers: Optional[list[tuple[str, str]]] = None) -> _Reply:
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
            stranger = unnamed_join(envelope, config)
            if stranger is not None:
                return _json(200, stranger)
            with self.lock:
                first = unenrolled_join(envelope, config, coord, headers or [])
                if first is not None:
                    return _json(200, first)
                return _json(200, _store_failure_said(coord.dispatch(envelope)))
        if path.startswith("/api/") and path != "/api/config":
            # The read API reads the coordinator's memory; a cheap call first
            # lets it take in what another process wrote to the store.
            with self.lock:
                coord.dispatch({"jsonrpc": "2.0", "id": "api-read", "method": "workspace.describe",
                                "params": {"workspace": workspace}})
        if path == "/api/config":
            with self.lock:
                return _json(200, public_config(config, coord))
        if path == "/api/members":
            with self.lock:
                return _json(200, {"members": list_members(coord, workspace)})
        if path in ("/api/chain", "/api/chain/verify"):
            return self._chain(path, query)
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

    def _chain(self, path: str, query: str) -> _Reply:
        """The chain, read and verified with the coordinator's own
        audit.read and audit.verify_chain, run here as the read API reads
        the tasks. They change nothing and are never recorded, so a reader
        needs no signature: the desk would otherwise sign each poll, and a
        key that asks for a gesture on every signature would ask each time.
        ``/api/chain`` takes ``from_seq`` and ``to_seq`` and answers as
        audit.read does; ``/api/chain/verify`` answers audit.verify_chain's
        ``{"result": ...}`` or ``{"error": ...}``."""
        params: dict[str, Any] = {"workspace": self.config["workspace"]}
        if path == "/api/chain/verify":
            with self.lock:
                return _json(200, self.coord._handlers["audit.verify_chain"](params))
        values = parse_qs(query, keep_blank_values=True)
        params["range"] = {}
        for name in ("from_seq", "to_seq"):
            if name in values:
                if not re.fullmatch(r"[0-9]+", values[name][0]):
                    return _json(400, {"error": f"{name} is a whole number"})
                params["range"][name] = int(values[name][0])
        with self.lock:
            read = self.coord._handlers["audit.read"](params)
            return _json(400, {"error": read["error"]["message"]}) if "error" in read else _json(200, read["result"])

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
        query = scope.get("query_string", b"").decode("latin-1")
        reply = self.check(method, path, headers, (scope.get("server") or (None, None))[1])
        root = scope.get("root_path", "")
        if reply is None and root and scope["path"] in ("", root):
            # The mount point itself: the desk's paths are relative, so its
            # page is at the mount point with a slash.
            reply = _Reply(308, b"", "text/plain; charset=utf-8",
                           (("location", f"{root}/" + (f"?{query}" if query else "")),))
        elif reply is None:
            body = await _read_body(receive) if method == "POST" else b""
            if body is None:
                reply = _too_large()
            else:
                reply = await asyncio.get_running_loop().run_in_executor(
                    None, self.handle, method, path, query, body, headers)
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

#: The loopback address of the other family, for a server on one.
_OTHER_LOOPBACK = {"127.0.0.1": "::1", "localhost": "::1", "::1": "127.0.0.1"}


class _Listener(ThreadingHTTPServer):
    """The standard library's HTTP server running a desk's routes on one
    address, of either family."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], desk: ChapDesk) -> None:
        self.desk = desk
        self.address_family = socket.AF_INET6 if ":" in address[0] else socket.AF_INET
        super().__init__(address, _Handler)


class ChapServer(_Listener):
    """The standard library's HTTP server, running a :class:`ChapDesk`.

    On a loopback address it listens on the other family's as well, on the
    same port: 127.0.0.1 and ::1, so ``http://localhost:<port>/`` reaches
    this server whichever of the two the name resolves to first, and never
    another program listening on the other. Where this machine has no
    loopback address of the other family, it listens on the one it has.
    """

    def __init__(self, address: tuple[str, int], desk: ChapDesk) -> None:
        self.other: Optional[_Listener] = None
        self._other_serving: Optional[threading.Thread] = None
        super().__init__(address, desk)
        if desk.notifier is not None:
            # Port 0 is a port picked now: the links name it.
            desk.notifier.desk = self.desk_url
        if address[0] in _OTHER_LOOPBACK:
            other = _OTHER_LOOPBACK[address[0]]
            try:
                self.other = _Listener((other, self.server_port), desk)
            except OSError as err:
                if err.errno == errno.EADDRINUSE:
                    super().server_close()
                    raise OSError(errno.EADDRINUSE, os.strerror(errno.EADDRINUSE), other) from err

    @property
    def desk_url(self) -> str:
        """Where a person opens the desk: :func:`desk_url` for this server."""
        return desk_url(self.desk.config["host"], self.server_port)

    def serve_forever(self, poll_interval: float = 0.5) -> None:
        if self.other is not None and self._other_serving is None:
            self._other_serving = threading.Thread(target=self.other.serve_forever, args=(poll_interval,),
                                                   daemon=True)
            self._other_serving.start()
        super().serve_forever(poll_interval)

    def shutdown(self) -> None:
        """Stop serving on every address; serve_forever returns."""
        if self._other_serving is None:
            return super().shutdown()
        other = threading.Thread(target=self.other.shutdown)
        other.start()
        super().shutdown()
        other.join()
        self._other_serving = None

    def server_close(self) -> None:
        if self._other_serving is not None:
            # serve_forever ended with an exception, Ctrl-C say, and the
            # other address is still served.
            self.other.shutdown()
            self._other_serving = None
        if self.other is not None:
            self.other.server_close()
        super().server_close()


class _Handler(BaseHTTPRequestHandler):
    server_version = "CHAPDesk"
    server: _Listener

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
                reply = desk.handle(self.command, path, url.query, body, headers)
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


def make_server(config: dict, coord: Coordinator, announce: bool = True) -> ChapServer:
    """Bind the standard library server on the configured host and port.
    Port 0 picks a port free on both loopback addresses; read it from
    ``server.server_port``, and the desk's address from ``server.desk_url``.
    It prints the enrolment code with that address unless ``announce`` is
    false, as :func:`serve` asks, which prints it among its own lines."""
    desk = ChapDesk(config, coord, announce=False)
    address = (config["host"], int(config["port"]))
    attempts = 5
    while True:
        try:
            server = ChapServer(address, desk)
            if announce:
                announce_enrolment(config, coord, server.desk_url.rstrip("/"))
            return server
        except OSError as err:
            # A port picked free on one loopback address can be held on the
            # other by another program; another port is picked.
            attempts -= 1
            if address[1] != 0 or err.errno != errno.EADDRINUSE or attempts == 0:
                raise


def serve(config: dict) -> int:
    """Run the desk server until it is interrupted, and return the exit code:
    0 after Ctrl-C, 1 when the store cannot be opened or the port is taken.
    ``config`` is what :func:`load_config` returns."""
    try:
        coord = make_coordinator(config)
    except (OSError, sqlite3.Error) as err:
        print(f"The store at {config['store']} could not be opened: {err}", file=sys.stderr)
        return 1
    try:
        server = make_server(config, coord, announce=False)
    except OSError as err:
        _close(coord)
        if err.errno != errno.EADDRINUSE:
            raise
        if err.filename:
            print(f"Port {config['port']} on {err.filename} is in use, and localhost may reach that address first: "
                  "another program answers there. Stop it, or set PORT to a free port.", file=sys.stderr)
        else:
            print(f"Port {config['port']} on {config['host']} is in use, by another desk or another program. "
                  "Stop that one, or set PORT to a free port.", file=sys.stderr)
        return 1
    base = server.desk_url.rstrip("/")
    print(f"CHAP {config['workspace']} on {base}")
    print(f"  desk     {base}/")
    print(f"  calls    POST {base}/chap")
    print(f"  profiles {', '.join(config['profiles'])}")
    kept = coord.get_workspace(config["workspace"])
    chain_on = kept.chain_enabled if kept is not None else config.get("chain")
    print(f"  store    {config['store']}{', chain on' if chain_on else ''}")
    print(f"  signing  {POLICY_WORDS[config.get('signature_policy')]}")
    if config.get("require_signatures"):
        print(f"           {DESK_SIGNING[config.get('desk_signing') or 'browser-key']}")
    announce_enrolment(config, coord, base)
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        _close(coord)
    return 0
