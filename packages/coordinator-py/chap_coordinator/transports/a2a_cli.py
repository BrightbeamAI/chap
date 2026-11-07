"""
chap_coordinator.transports.a2a_cli
====================================

The CHAP A2A server, as a command::

    python -m chap_coordinator.transports.a2a_cli --port 9090 --as agent:orchestrator@local

It serves a coordinator as an A2A 1.0 agent, answering A2A 0.3 clients as
well, with every call made as one participant: ``--as`` or
``CHAP_PARTICIPANT``, ``agent:orchestrator@local`` by default. A ``human:``
URI is refused: a person decides in a desk, never through an orchestrator.
It offers the agent's skills, the ``AGENT_MCP_TOOLS`` set, unless ``--skills
all`` offers every CHAP method.
The app is :func:`chap_coordinator.transports.a2a_app.make_chap_a2a_app`,
so the server answers only under its own names (421), refuses a browser
request from another origin (403), takes JSON only (415) and caps the body
(413). It has no authentication.

Options, with the environment variable that stands in for each (an empty
one counts as unset):

``--host <address>`` (``CHAP_HOST``)
    The address to listen on, 127.0.0.1 by default.
``--port <number>``
    The port, 9090 by default.
``--as <uri>`` (``CHAP_PARTICIPANT``)
    The participant every call is made as.
``--skills agent|all``
    ``agent``, the default, offers ``AGENT_MCP_TOOLS``; ``all`` offers every
    method, still as the one participant.
``--db <path>`` (``CHAP_DB_PATH``)
    Keep the workspaces in this SQLite file. A leading ``~`` is the home
    directory. Without it state is in memory only.
``--allowed-host <name>`` (``CHAP_ALLOWED_HOSTS``, comma separated)
    Another name the server answers to, as a Host header carries it, such
    as ``a2a.internal:9090``. Repeatable.

``main(argv)`` returns the exit code: 0 when the server stops, 2 for a usage
error, 1 when the store cannot be opened.

The TypeScript counterpart is ``reference/a2a-server-ts/server.ts``.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Optional

import uvicorn

from chap_coordinator import __version__
from chap_coordinator.coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.storage.sqlite import SqliteStore

from .a2a_app import make_chap_a2a_app

__all__ = ["main", "DEFAULT_PARTICIPANT", "PROFILES"]

DEFAULT_PARTICIPANT = "agent:orchestrator@local"

#: Every profile, audit-scitt/1.0 among them, so the chain is on from a
#: workspace's first entry. The same set as the TypeScript reference server.
PROFILES = [
    "core/1.0", "review/1.0", "whisper/1.0", "deliberation/1.0", "handoff/1.0",
    "control/1.0", "routing/1.0", "modes/1.0", "audit-scitt/1.0",
]

#: A participant URI: a scheme, a colon, then printable characters with no
#: quote, backslash or space.
_URI = re.compile(r"[a-z][a-z0-9+.-]*:[\x21\x23-\x5b\x5d-\x7e]+")

_LOOPBACK = ("127.0.0.1", "::1", "localhost")
_WILDCARD = ("0.0.0.0", "::")


def _env(name: str) -> Optional[str]:
    """An environment variable's value, or None when it is unset or blank."""
    value = (os.environ.get(name) or "").strip()
    return value or None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m chap_coordinator.transports.a2a_cli",
        description="Serves a CHAP coordinator as an A2A agent, making every call as one participant.",
        allow_abbrev=False,
    )
    parser.add_argument("--host", default=_env("CHAP_HOST") or "127.0.0.1",
                        help="the address to listen on (CHAP_HOST, default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=9090, help="the port (default 9090)")
    parser.add_argument("--as", dest="participant", metavar="URI",
                        help=f"the participant every call is made as (CHAP_PARTICIPANT, default "
                             f"{DEFAULT_PARTICIPANT}); a human: URI is refused")
    parser.add_argument("--skills", choices=("agent", "all"), default="agent",
                        help="agent, the default: the skills an agent's own work needs, the "
                             "AGENT_MCP_TOOLS set; all: every CHAP method")
    parser.add_argument("--db", metavar="PATH",
                        help="keep the workspaces in this SQLite file (CHAP_DB_PATH); a leading ~ is the "
                             "home directory")
    parser.add_argument("--allowed-host", dest="allowed_hosts", metavar="NAME", action="append", default=[],
                        help="another name the server answers to, as a Host header carries it, such as "
                             "a2a.internal:9090; repeatable (CHAP_ALLOWED_HOSTS, comma separated)")
    return parser


def _bracketed(host: str) -> str:
    return f"[{host}]" if ":" in host else host


def main(argv: Optional[list[str]] = None) -> int:
    """Run the server until it is stopped; return the exit code."""
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exit_:  # --help, or argparse's own usage error
        return int(exit_.code or 0)

    participant, source = ((args.participant, "--as") if args.participant is not None
                           else (_env("CHAP_PARTICIPANT"), "CHAP_PARTICIPANT"))
    if participant is None:
        participant, source = DEFAULT_PARTICIPANT, "--as"
    problem = None
    if not _URI.fullmatch(participant):
        problem = f"{source} {participant} is not a participant URI. Use the form agent:name@example.org."
    elif participant.startswith("human:"):
        problem = (f"{source} {participant} names a person. This server acts for an agent, and a person "
                   "decides in a desk; give the agent's own URI, such as agent:orchestrator@local.")
    elif not 0 < args.port < 65536:
        problem = f"--port takes a port number; {args.port} is not one"
    if problem:
        print(f"{problem}\nRun with --help for the options.", file=sys.stderr)
        return 2

    allowed_hosts = [*args.allowed_hosts,
                     *(h.strip() for h in (_env("CHAP_ALLOWED_HOSTS") or "").split(",") if h.strip())]
    # The address it listens on is one of its names, as in the create-chap-app servers.
    if args.host not in _LOOPBACK + _WILDCARD:
        allowed_hosts.append(f"{_bracketed(args.host)}:{args.port}")
    shown = "localhost" if args.host in _LOOPBACK + _WILDCARD else _bracketed(args.host)
    base_url = f"http://{shown}:{args.port}/"

    db = args.db if args.db is not None else _env("CHAP_DB_PATH")
    store = None
    if db is not None:
        db = os.path.abspath(os.path.expanduser(db))
        try:
            os.makedirs(os.path.dirname(db), exist_ok=True)
            store = SqliteStore(db)
        except Exception as err:  # noqa: BLE001 - reported, then the command stops
            print(f"The SQLite store at {db} could not be opened: {err}", file=sys.stderr)
            return 1
    coord = Coordinator(CoordinatorOptions(default_profiles=PROFILES, store=store))

    # Unset, the card and the executor offer the agent's skills.
    skill_filter = (lambda _name: True) if args.skills == "all" else None
    app = make_chap_a2a_app(coord, base_url=base_url, participant=participant, skill_filter=skill_filter,
                            allowed_hosts=allowed_hosts, version=__version__)

    skill_set = "every CHAP skill" if args.skills == "all" else "the agent skills"
    print(f"CHAP A2A server {__version__} on {base_url}, acting as {participant} with {skill_set}",
          file=sys.stderr)
    print(f"Agent Card: {base_url}.well-known/agent-card.json", file=sys.stderr)
    print(f"Profiles enabled: {', '.join(PROFILES)}.", file=sys.stderr)
    print(f"Persisting to {db}" if db else
          "State is in memory only: the workspaces are lost when this process exits.", file=sys.stderr)
    if args.host not in _LOOPBACK:
        print(f"WARNING: binding to {args.host} exposes an unauthenticated coordinator to the "
              "network. Use only on a trusted network.", file=sys.stderr)

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
