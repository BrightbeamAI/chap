"""
chap_coordinator.transports.mcp_cli
====================================

The CHAP MCP server on stdio, as a command::

    python -m chap_coordinator.transports.mcp_cli --url http://127.0.0.1:8792/chap --as agent:assistant

It serves CHAP to one MCP client as one participant: every call is made as
that member, by default the agent ``agent:assistant``, as the mcp-gate
template names its agent, with the tools an agent's own work needs. A person
decides in a desk, so it never acts as a ``human:`` participant. With
``--url`` it forwards every call to a running CHAP server's JSON-RPC
endpoint, such as a desk server's ``POST /chap``, so the assistant's work
waits for the person at that desk. Without it, it runs its own coordinator,
in memory or on a SQLite file.

Options, each with the environment variable that stands in for it (an empty
one counts as unset):

``--as <uri>`` (``CHAP_PARTICIPANT``)
    The participant every call is made as. A ``human:`` URI is refused.
``--tools agent|all``
    ``agent``, the default, offers ``AGENT_MCP_TOOLS``; ``all`` offers every
    method, still as the one participant.
``--url <endpoint>``
    Forward every call to this JSON-RPC endpoint, whose server keeps the
    workspaces, its store and its profiles.
``--db <path>`` (``CHAP_DB_PATH``)
    Keep the workspaces in this SQLite file. A leading ``~`` is the home
    directory.
``--profiles <list>`` (``CHAP_PROFILES``)
    Comma-separated profiles for new workspaces.

``main(argv)`` returns the exit code: 0 when the client closes the
connection, 2 for a usage error, 1 when the store cannot be opened.

Mirrors ``chap-mcp-server`` in ``packages/coordinator-mcp/src/cli.ts``.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Any, Optional
from urllib.parse import urlsplit

import anyio
from mcp.server.stdio import stdio_server

from chap_coordinator import __version__
from chap_coordinator.coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.storage.sqlite import SqliteStore

from .mcp_server import AGENT_MCP_TOOLS, make_chap_mcp_server
from .remote import RemoteDispatcher

__all__ = ["main", "DEFAULT_PARTICIPANT", "DEFAULT_PROFILES"]

DEFAULT_PARTICIPANT = "agent:assistant"

#: Every profile, audit-scitt/1.0 among them, so the chain is on from a
#: workspace's first entry. The same set as the TypeScript command.
DEFAULT_PROFILES = [
    "core/1.0", "review/1.0", "whisper/1.0", "deliberation/1.0", "handoff/1.0",
    "control/1.0", "routing/1.0", "modes/1.0", "audit-scitt/1.0",
]

#: A participant URI: a scheme, a colon, then printable characters with no
#: quote, backslash or space.
_URI = re.compile(r"[a-z][a-z0-9+.-]*:[\x21\x23-\x5b\x5d-\x7e]+")


class _UsageError(Exception):
    pass


def _env(name: str) -> Optional[str]:
    """An environment variable's value, or None when it is unset or blank."""
    value = (os.environ.get(name) or "").strip()
    return value or None


def _parser(prog: str = "python -m chap_coordinator.transports.mcp_cli") -> argparse.ArgumentParser:
    agent_tools = "\n".join(f"  {name}" for name in sorted(AGENT_MCP_TOOLS))
    profiles = "\n".join(f"  {p}" for p in DEFAULT_PROFILES)
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Serves CHAP to one MCP client on stdio, making every call as one participant.",
        epilog=f"The agent tools:\n{agent_tools}\n\nThe default profiles:\n{profiles}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    parser.add_argument("--as", dest="participant", metavar="URI",
                        help=f"the participant every call is made as (CHAP_PARTICIPANT, default "
                             f"{DEFAULT_PARTICIPANT}). A human: URI is refused: a person decides "
                             "in a desk, never through the assistant")
    parser.add_argument("--tools", choices=("agent", "all"), default="agent",
                        help="agent, the default: the tools an agent's own work needs, listed "
                             "below; all: every CHAP method")
    parser.add_argument("--url", metavar="ENDPOINT",
                        help="forward every call to a running CHAP server's JSON-RPC endpoint, such "
                             "as http://127.0.0.1:8792/chap, which keeps the workspaces")
    parser.add_argument("--db", metavar="PATH",
                        help="keep the workspaces in this SQLite file (CHAP_DB_PATH); a leading ~ is "
                             "the home directory. Without --db or --url, state is in memory only")
    parser.add_argument("--profiles", metavar="LIST",
                        help="comma-separated profiles for new workspaces (CHAP_PROFILES, default "
                             "those listed below)")
    return parser


def _options(args: argparse.Namespace) -> dict[str, Any]:
    """The parsed options, checked as the TypeScript command checks them."""
    if args.participant is not None:
        participant, participant_from = args.participant, "--as"
    elif _env("CHAP_PARTICIPANT") is not None:
        participant, participant_from = _env("CHAP_PARTICIPANT"), "CHAP_PARTICIPANT"
    else:
        participant, participant_from = DEFAULT_PARTICIPANT, "--as"
    if not _URI.fullmatch(participant):
        raise _UsageError(f"{participant_from} {participant} is not a participant URI. "
                          "Use the form agent:name@example.org.")
    if participant.startswith("human:"):
        raise _UsageError(f"{participant_from} {participant} names a person. This server acts for an "
                          "assistant, and a person decides in a desk; give the assistant's own URI, "
                          "such as agent:assistant.")

    url = None
    if args.url is not None:
        try:
            parts = urlsplit(args.url)
            parts.port  # noqa: B018 - raises on a port or an IPv6 address that does not parse
        except ValueError:
            parts = None
        if parts is None or parts.scheme not in ("http", "https") or not parts.netloc:
            raise _UsageError("--url takes the http or https address of a CHAP JSON-RPC endpoint, "
                              f"such as http://127.0.0.1:8792/chap; {args.url} is not one")
        url = args.url

    db, db_from = (args.db, "--db") if args.db is not None else (_env("CHAP_DB_PATH"), "CHAP_DB_PATH")
    raw_profiles, profiles_from = ((args.profiles, "--profiles") if args.profiles is not None
                                   else (_env("CHAP_PROFILES"), "CHAP_PROFILES"))
    if url and db is not None:
        raise _UsageError(f"--url forwards to a server that keeps its own store, so {db_from} "
                          "does not apply. Set one or the other.")
    if url and raw_profiles is not None:
        raise _UsageError(f"--url forwards to a server that sets its own profiles, so {profiles_from} "
                          "does not apply. Set one or the other.")

    profiles = DEFAULT_PROFILES
    if raw_profiles is not None:
        profiles = [p.strip() for p in raw_profiles.split(",") if p.strip()]
        if not profiles:
            raise _UsageError(f"{profiles_from} names no profiles. Leave it unset for the defaults.")

    return {
        "participant": participant,
        "tools": args.tools,
        "url": url,
        "db": os.path.abspath(os.path.expanduser(db)) if db is not None else None,
        "profiles": profiles,
    }


def main(argv: Optional[list[str]] = None, prog: Optional[str] = None) -> int:
    """Run the server on stdio until the client closes it; return the exit code."""
    try:
        args = (_parser(prog) if prog else _parser()).parse_args(argv)
    except SystemExit as exit_:  # --help, or argparse's own usage error
        return int(exit_.code or 0)
    try:
        options = _options(args)
    except _UsageError as err:
        print(f"{err}\nRun with --help for the options.", file=sys.stderr)
        return 2

    tool_set = "the agent tools" if options["tools"] == "agent" else "every CHAP tool"
    # stdout carries the MCP protocol stream; anything written there corrupts it.
    print(f"CHAP MCP server {__version__} on stdio, acting as {options['participant']} with {tool_set}.",
          file=sys.stderr)

    if options["url"]:
        dispatcher: Any = RemoteDispatcher(options["url"])
        print(f"Forwarding every call to {options['url']}, whose coordinator keeps the workspaces.",
              file=sys.stderr)
    else:
        store = None
        if options["db"]:
            try:
                os.makedirs(os.path.dirname(options["db"]), exist_ok=True)
                store = SqliteStore(options["db"])
            except Exception as err:  # noqa: BLE001 - reported, then the command stops
                print(f"The SQLite store at {options['db']} could not be opened: {err}", file=sys.stderr)
                return 1
        dispatcher = Coordinator(CoordinatorOptions(default_profiles=options["profiles"], store=store))
        print(f"Profiles: {', '.join(options['profiles'])}", file=sys.stderr)
        print(f"Persisting to {options['db']}" if options["db"] else
              "State is in memory only: the workspaces are lost when this process exits. "
              "Set --db or CHAP_DB_PATH to keep them.", file=sys.stderr)
        if options["tools"] == "agent" and not dispatcher.workspaces:
            print("No workspace exists yet, and the agent tools cannot create one: run once with "
                  "--tools all, or use --url to reach a server that has one.", file=sys.stderr)

    server = make_chap_mcp_server(
        dispatcher,
        name="chap",
        version=__version__,
        participant=options["participant"],
        tool_filter=(lambda name: name in AGENT_MCP_TOOLS) if options["tools"] == "agent" else None,
    )

    async def serve() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(serve)
    return 0


if __name__ == "__main__":
    sys.exit(main())
