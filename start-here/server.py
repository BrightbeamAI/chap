"""The local review desk: a small HTTP API and a static UI over one ReviewGate.

This API is a convenience for the demo. It is not the CHAP wire protocol, and
it is not an authentication system. Two capabilities keep the two roles apart
so the difference is visible on one machine:

  reviewer  printed once in the terminal, carried in the URL fragment
  agent     written to a 0600 file, so a separate process can propose work

The handler is deliberately single-threaded: one coordinator owns one SQLite
store, and the store contract is single-writer.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import traceback
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from chap_starter import ReviewGate, ChapError, ReviewPending, ReviewRejected, StorageError

ASSETS = Path(__file__).with_name("web")

# The demo's own agent proposes the built-in fixtures. Anything you paste into
# the browser is recorded under a different agent URI, because an audit log
# that attributes every draft to one identity cannot answer "which agent wrote
# this?", which is half the reason to keep the log.
DEMO_AGENT = "agent:demo"
PASTE_AGENT = "agent:your-app"
AGENTS = (DEMO_AGENT, PASTE_AGENT)

SCENARIOS = {
    "support": {
        "title": "Customer reply", "kind": "draft_response",
        "context": {"customer": "Where is my order?",
                    "tracking": "In transit; delivery date unknown"},
        "draft": {"text": "Your order is guaranteed to arrive tomorrow. Thanks for your patience!"},
        "hint": "The draft promises a date the tracking data does not support. "
                "Edit that promise, then approve your version.",
    },
    "code": {
        "title": "Code review", "kind": "code_review",
        "context": {"file": "app/settings.py",
                    "finding": "DEBUG is enabled in the production configuration"},
        "draft": {"severity": "warning", "file": "app/settings.py",
                  "recommendation": "Disable DEBUG before deploying."},
        "hint": "Keep the finding and change its severity, or reject it. "
                "Either way CHAP records the decision and your reason.",
    },
    "tool": {
        "title": "Tool arguments", "kind": "prepare_email",
        "context": {"next_step": "Prepare a local email object after human review",
                    "effect": "This demo sends no email"},
        "draft": {"to": "customer@example.com", "subject": "Your delivery",
                  "body": "Your order will arrive tomorrow."},
        "hint": "Review the arguments before your code calls the tool. "
                "The result contains your edited arguments, not the draft.",
    },
}
# The reviewer's guidance must not be selectable by whoever wrote the draft, so
# hints are held per task id on the server (ReviewServer.hints) and set only
# where the server itself created the task from one of the scenarios above.


class ReviewServer(HTTPServer):
    allow_reuse_address = True

    def __init__(self, address, gate):
        self.gate = gate
        self.reviewer_token = secrets.token_urlsafe(32)
        self.agent_token = secrets.token_urlsafe(32)
        self.hints = {}          # task_id -> hint, for server-created fixtures only
        super().__init__(address, Handler)
        self.base_url = f"http://127.0.0.1:{self.server_port}"


class Handler(BaseHTTPRequestHandler):
    server_version = "CHAPStarter/1"

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *_):
        pass  # Never log capability tokens or draft content.

    def _reply(self, status, body, content_type="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self'; style-src 'self'; "
                         "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
                         "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(data)

    def _auth(self, human=False):
        header = "X-CHAP-Reviewer" if human else "X-CHAP-Agent"
        expected = self.server.reviewer_token if human else self.server.agent_token
        if not hmac.compare_digest(self.headers.get(header, ""), expected):
            raise PermissionError("Open the reviewer link printed in the terminal" if human
                                  else "A current agent connection file is required")

    def _body(self):
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("Chunked requests are not supported")
        size = int(self.headers.get("Content-Length", "0"))
        if size < 1 or size > 128 * 1024:
            raise ValueError("Supply a JSON object smaller than 128 KiB")
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Use Content-Type: application/json")

        def bad_number(value):
            raise ValueError(f"{value} is not a JSON number")

        value = json.loads(self.rfile.read(size), parse_constant=bad_number)
        if not isinstance(value, dict):
            raise ValueError("The request must be a JSON object")
        return value

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    # -- payloads ----------------------------------------------------------

    def _desk(self, selected):
        """The reviewer's view. Bounded: the timeline covers one task, not the log.

        ``verification`` is always present and always explicit. A UI that
        renders it must fail closed on anything other than "verified"; there is
        no shape of this response in which a missing verdict means the chain is
        intact.
        """
        gate = self.server.gate
        summaries, detail = [], None
        for view in gate.tasks():
            summaries.append({k: view[k] for k in
                              ("task_id", "kind", "state", "author", "allowed")}
                             | {"decision_kind": (view["decision"] or {}).get("kind")})
            if view["task_id"] == selected:
                detail = view
        if detail is None and summaries:
            detail = gate.inspect(summaries[-1]["task_id"])
        entries = gate.audit()
        opening = [entry for entry in entries
                   if entry["envelope"]["method"] in ("workspace.create", "participant.join")]
        focus = ([entry for entry in entries
                  if entry["envelope"]["params"].get("task_id") == (detail or {}).get("task_id")]
                 if detail else [])
        return {
            "workspace": gate.workspace, "reviewer": gate.reviewer,
            "tasks": summaries, "task": detail,
            "entries": opening + focus,
            "verification": gate.verdict(),
            "hint": self.server.hints.get((detail or {}).get("task_id")),
            "scenarios": {name: {"title": s["title"]} for name, s in SCENARIOS.items()},
        }

    # -- routing -----------------------------------------------------------

    def _route(self, method):
        try:
            base = self.server.base_url
            if self.headers.get("Host") != urlsplit(base).netloc:
                raise PermissionError("Use the exact 127.0.0.1 link printed by the starter")
            if self.headers.get("Origin") not in (None, base):
                raise PermissionError("Cross-origin requests are disabled")
            split = urlsplit(self.path)
            path = split.path
            files = {"/": ("index.html", "text/html"),
                     "/app.js": ("app.js", "text/javascript"),
                     "/render.mjs": ("render.mjs", "text/javascript"),
                     "/style.css": ("style.css", "text/css")}
            if method == "GET" and path in files:
                name, mime = files[path]
                return self._reply(200, (ASSETS / name).read_bytes(), mime + "; charset=utf-8")

            gate = self.server.gate
            if method == "GET" and path == "/api/desk":
                self._auth(human=True)
                selected = (parse_qs(split.query).get("task") or [None])[0]
                return self._reply(200, self._desk(selected))
            if method == "GET" and path == "/api/evidence":
                self._auth(human=True)
                return self._reply(200, {
                    "format": "chap-starter-evidence/1", "workspace": gate.workspace,
                    "verification": gate.verdict(), "entries": gate.audit(),
                    "snapshot": asdict(gate.coordinator.get_workspace(gate.workspace)),
                })
            if method == "POST" and path.startswith("/api/examples/"):
                self._auth(human=True)
                self._body()
                name = path.rsplit("/", 1)[-1]
                if name not in SCENARIOS:
                    raise KeyError("Unknown example")
                scenario = SCENARIOS[name]
                task_id = gate.propose(scenario["draft"], kind=scenario["kind"],
                                       context=scenario["context"], agent=DEMO_AGENT)
                self.server.hints[task_id] = scenario["hint"]
                return self._reply(201, gate.inspect(task_id))
            if method == "POST" and path in ("/api/proposals", "/api/drafts"):
                # /api/proposals is the agent capability; /api/drafts is the
                # reviewer pasting their own JSON. Both are agent-authored work,
                # under different agent URIs.
                agent = DEMO_AGENT if path == "/api/proposals" else PASTE_AGENT
                self._auth(human=path == "/api/drafts")
                body = self._body()
                if set(body) - {"draft", "kind", "context"}:
                    raise ValueError("Only draft, kind, and context are accepted; "
                                     "the server assigns actors")
                task_id = gate.propose(body.get("draft"),
                                       kind=body.get("kind", "draft_response"),
                                       context=body.get("context"), agent=agent)
                return self._reply(201, gate.inspect(task_id))
            if method == "GET" and path.startswith("/api/tasks/"):
                self._auth()
                parts = path.strip("/").split("/")
                if len(parts) not in (3, 4) or (len(parts) == 4 and parts[3] != "result"):
                    raise KeyError("Unknown endpoint")
                task_id = parts[2]
                if len(parts) == 4:
                    return self._reply(200, {"task_id": task_id, "output": gate.result(task_id)})
                return self._reply(200, gate.inspect(task_id))
            if method == "POST" and path.startswith("/api/reviews/") and path.endswith("/decision"):
                self._auth(human=True)
                parts = path.strip("/").split("/")
                if len(parts) != 4:
                    raise KeyError("Unknown endpoint")
                body = self._body()
                allowed = {"action", "edited", "rationale", "tags",
                           "expected_digest", "intent_preserved"}
                if set(body) - allowed:
                    raise ValueError("Unknown decision field")
                if not isinstance(body.get("expected_digest"), str):
                    raise ValueError("Include the digest of the draft you reviewed")
                action = body.pop("action", None)
                return self._reply(200, gate.decide(parts[2], action, **body))
            raise KeyError("Unknown endpoint")
        except PermissionError as exc:
            self._safe_reply(403, {"error": str(exc)})
        except KeyError as exc:
            self._safe_reply(404, {"error": str(exc).strip("'")})
        except (ReviewPending, ReviewRejected) as exc:
            self._safe_reply(409, {"error": str(exc), "allowed": False})
        except (ValueError, TypeError) as exc:
            self._safe_reply(400, {"error": str(exc)})
        except (ChapError, StorageError) as exc:
            self._safe_reply(503, {"error": str(exc), "allowed": False})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception:
            # A demo that drops the connection teaches the reader nothing. Answer
            # with a status, keep the traceback on this machine, and say nothing
            # about paths or drafts on the wire.
            traceback.print_exc()
            self._safe_reply(500, {"error": "The review desk hit an unexpected error. "
                                            "See the terminal.", "allowed": False})

    def _safe_reply(self, status, body):
        try:
            self._reply(status, body)
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            pass


def write_connection(server, path):
    """Write the agent capability only. The reviewer capability is never written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        json.dump({"base_url": server.base_url, "agent_token": server.agent_token}, stream)
        stream.write("\n")
    if os.name != "nt":
        path.chmod(0o600)
    return path
