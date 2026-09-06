"""
CHAP reference server (Python).

Exposes the Coordinator over an HTTP POST /chap endpoint, matching
the wire shape of the TypeScript reference server at
reference/core-plus-review/server.ts.

Usage:

    pip install -e ../../packages/coordinator-py
    python server.py                          # listens on :8080
    python server.py --port 9000              # custom port
    python server.py --core-only              # core + review only

Then run the conformance harness against it:

    cd ../../conformance/harness && npx tsx harness.ts
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from chap_coordinator import Coordinator, CoordinatorOptions

MAX_BODY_BYTES = 1_000_000
MAX_JSON_DEPTH = 64


def _within_depth(value: object, limit: int) -> bool:
    """Iteratively check nesting depth. dispatch/deepcopy/canonicalize
    recurse, so an over-deep envelope is rejected before it reaches them."""
    stack = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > limit:
            return False
        if isinstance(node, dict):
            for v in node.values():
                stack.append((v, depth + 1))
        elif isinstance(node, list):
            for v in node:
                stack.append((v, depth + 1))
    return True


def _parse_error(message: str) -> dict:
    return {"jsonrpc": "2.0", "id": None,
            "error": {"code": -32700, "message": message}}


def make_handler(coord: Coordinator):
    # The Coordinator is a single-writer, lock-free library, but this server
    # runs it under ThreadingHTTPServer. Serialise dispatch so concurrent
    # requests cannot interleave the audit chain's read-link-append.
    dispatch_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def _cors(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            if self.path not in ("/chap", "/"):
                self.send_response(404)
                self._cors()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"POST /chap"}')
                return

            header = self.headers.get("Content-Length")
            try:
                length = int(header) if header is not None else 0
            except ValueError:
                self._write(400, _parse_error("Invalid Content-Length header"))
                return
            if length < 0 or length > MAX_BODY_BYTES:
                self._write(400, _parse_error("Content-Length out of range"))
                return
            try:
                raw = self.rfile.read(length).decode("utf-8")
                envelope = json.loads(raw)
            except (ValueError, UnicodeDecodeError, RecursionError):
                self._write(400, _parse_error("Malformed JSON"))
                return
            if not _within_depth(envelope, MAX_JSON_DEPTH):
                self._write(400, _parse_error("Envelope nesting too deep"))
                return

            with dispatch_lock:
                response = coord.dispatch(envelope)
            status = 400 if "error" in response else 200
            self._write(status, response)

        def _write(self, status: int, body: dict) -> None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):  # quieter logs
            sys.stderr.write(f"  {self.address_string()} - {format % args}\n")

    return Handler


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="CHAP reference HTTP server (Python)")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--core-only", action="store_true",
                   help="Advertise core+review only (smaller default profile set)")
    p.add_argument("--require-signatures", action="store_true",
                   help="Reject envelopes without a valid signature")
    args = p.parse_args(argv)

    if args.core_only:
        profiles = ["core/1.0", "review/1.0"]
    else:
        profiles = [
            "core/1.0", "review/1.0", "whisper/1.0",
            "deliberation/1.0", "handoff/1.0", "control/1.0",
            "routing/1.0", "modes/1.0",
        ]

    coord = Coordinator(CoordinatorOptions(
        default_profiles=profiles,
        require_signatures=args.require_signatures,
    ))
    handler = make_handler(coord)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"CHAP reference (Python) on http://{args.host}:{args.port}/chap")
    print(f"Profiles: {', '.join(profiles)}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
