"""
chap_coordinator.cli
====================

The ``chap-coordinator`` command::

    chap-coordinator serve [--config PATH] [--host ADDRESS] [--port NUMBER]
    chap-coordinator mcp [options]
    chap-coordinator a2a [options]

``serve`` runs the desk server, :mod:`chap_coordinator.server`, from a
chap.config.json: the review desk, ``POST /chap`` and the read API, in the
one process that owns the store. ``mcp`` serves MCP on stdio as one agent,
alone or forwarding to a running server with ``--url``, and ``a2a`` serves
A2A as one agent; they are :func:`chap_coordinator.transports.mcp_cli.main`
and :func:`chap_coordinator.transports.a2a_cli.main`, and take the same
options. Each command takes ``--help``. ``mcp`` needs the ``mcp`` extra and
``a2a`` the ``a2a`` extra.

``python -m chap_coordinator`` is the same command. ``main(argv)`` returns
the exit code: 0 when a server stops, 1 when it cannot start, and 2 for a
usage error.
"""
from __future__ import annotations

import argparse
import sys
from typing import Optional

from . import __version__

__all__ = ["main"]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chap-coordinator",
        description="The CHAP coordinator: a desk server for the people who decide, and MCP and A2A "
                    "servers that act as one agent.",
        allow_abbrev=False,
    )
    parser.add_argument("--version", action="version", version=f"chap-coordinator {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    serve = commands.add_parser(
        "serve", allow_abbrev=False,
        help="the desk server: the review desk, POST /chap and the read API, from one chap.config.json",
        description="Runs the desk server from a chap.config.json, in the one process that owns its store. "
                    "PORT, CHAP_HOST, CHAP_DB_PATH, CHAP_REQUIRE_SIGNATURES, CHAP_DESK_SIGNING, CHAP_CHAIN and "
                    "CHAP_ALLOWED_HOSTS override the file; a store path is read against the file's directory.")
    serve.add_argument("--config", metavar="PATH",
                       help="the config file (CHAP_CONFIG, default chap.config.json in the current directory)")
    serve.add_argument("--host", metavar="ADDRESS", help="the address to listen on, over the file and CHAP_HOST")
    serve.add_argument("--port", metavar="NUMBER", type=int,
                       help="the port, over the file and PORT; 0 picks a free one")
    commands.add_parser("mcp", add_help=False,
                        help="MCP on stdio, acting as one agent (needs the mcp extra; mcp --help for its options)")
    commands.add_parser("a2a", add_help=False,
                        help="an A2A server, acting as one agent (needs the a2a extra; a2a --help for its options)")
    return parser


def _transport(name: str, argv: list[str]) -> int:
    """Run ``mcp`` or ``a2a``, or say which extra it needs."""
    try:
        if name == "mcp":
            from .transports.mcp_cli import main as run
        else:
            from .transports.a2a_cli import main as run
    except ModuleNotFoundError as err:
        missing = (err.name or "").split(".")[0]
        if not missing or missing == "chap_coordinator":
            raise
        print(f"chap-coordinator {name} needs the {name} extra, and {missing} is not installed. "
              f'Install it with: pip install "chap-coordinator[{name}]"', file=sys.stderr)
        return 1
    return run(argv, prog=f"chap-coordinator {name}")


def _serve(args: argparse.Namespace) -> int:
    from .server import load_config, serve

    try:
        config = load_config(args.config)
    except FileNotFoundError as err:
        print(f"No config file at {err.filename}. Give its path with --config, or set CHAP_CONFIG; it names "
              "the workspace, its profiles and the people in it.", file=sys.stderr)
        return 2
    except (OSError, ValueError) as err:
        print(err, file=sys.stderr)
        return 2
    if args.host:
        config["host"] = args.host
    if args.port is not None:
        if not 0 <= args.port < 65536:
            print(f"--port takes a port number; {args.port} is not one", file=sys.stderr)
            return 2
        config["port"] = args.port
    return serve(config)


def main(argv: Optional[list[str]] = None) -> int:
    """Run the command; return the exit code."""
    args = sys.argv[1:] if argv is None else list(argv)
    if args and args[0] in ("mcp", "a2a"):
        return _transport(args[0], args[1:])
    parser = _parser()
    try:
        parsed = parser.parse_args(args)
    except SystemExit as exit_:  # --help, --version, or argparse's own usage error
        return int(exit_.code or 0)
    if parsed.command != "serve":
        parser.print_help(sys.stderr)
        return 2
    return _serve(parsed)


if __name__ == "__main__":
    sys.exit(main())
