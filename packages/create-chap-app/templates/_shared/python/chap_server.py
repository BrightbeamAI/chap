"""The process that owns the store.

It runs the CHAP coordinator on SQLite and serves three things:

    GET  /              the review desk
    POST /chap          CHAP calls as JSON-RPC, for any agent or client
    GET  /api/...       what the desk needs that the protocol does not carry

Everything comes from chap.config.json next to this file, with a few
environment overrides noted in load_config. The project's desk.py imports
serve() from here.

One process owns one SQLite database: every call is answered under one lock,
because the coordinator is a single writer and the store contract is single
writer. A ``human:`` URI labels a participant; it does not authenticate a
person. Which reviews a person is shown is the deployment's decision, as
SPECIFICATION 15.1 says; this process shows a reviewer the open reviews
addressed to the URI it is asked about.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

if sys.version_info < (3, 10):
    raise SystemExit("CHAP needs Python 3.10 or newer.")

try:
    from chap_coordinator import Coordinator, CoordinatorOptions
    from chap_coordinator.storage.sqlite import SqliteStore
    from chap_coordinator.storage.store import MemoryStore
except ModuleNotFoundError as exc:  # pragma: no cover - import guard
    if exc.name != "chap_coordinator":
        raise
    raise SystemExit(
        "chap-coordinator is not installed. Run: pip install -r requirements.txt"
    ) from exc

HERE = Path(__file__).resolve().parent
DESK_DIR = HERE / "desk"


# -- configuration ------------------------------------------------------------

def env_flag(name: str, fallback: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return fallback
    return value == "1" or value.lower() == "true"


def load_config(path: str | os.PathLike | None = None) -> dict:
    """Read chap.config.json and apply the environment overrides.

    PORT, CHAP_HOST and CHAP_DB_PATH replace the port, host and store;
    CHAP_REQUIRE_SIGNATURES and CHAP_CHAIN (``1`` or ``true``) replace the
    two flags. The chain defaults to on when audit-scitt/1.0 is advertised.
    """
    config_path = Path(path) if path is not None else HERE / "chap.config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["port"] = int(os.environ.get("PORT") or config.get("port") or 8787)
    config["host"] = os.environ.get("CHAP_HOST") or config.get("host") or "127.0.0.1"
    config["store"] = os.environ.get("CHAP_DB_PATH") or config.get("store") or "./data/chap.db"
    config["require_signatures"] = env_flag(
        "CHAP_REQUIRE_SIGNATURES", bool(config.get("require_signatures", False)))
    # The coordinator turns the chain on for a workspace that advertises
    # audit-scitt/1.0 whatever the flag says, so the flag reports that too.
    config["chain"] = (env_flag("CHAP_CHAIN", bool(config.get("chain", False)))
                       or "audit-scitt/1.0" in config["profiles"])
    config.setdefault("humans", [])
    config.setdefault("agent", None)
    return config


def reconcile_signed_profile(config: dict) -> None:
    """security-signed/1.0 is enforced by require_signatures. The coordinator adds
    the profile where signatures are required and refuses a workspace that
    advertises it without them (SPECIFICATION 15.1, item 3). The list here is
    made to agree before the workspace is created, so the console, /api/config
    and the descriptor say the same thing."""
    advertised = any(p == "security-signed" or p.startswith("security-signed/") for p in config["profiles"])
    if config.get("require_signatures") and not advertised:
        config["profiles"] = [*config["profiles"], "security-signed/1.0"]
        print("security-signed/1.0 added to the profiles: signatures are required.")
    elif advertised and not config.get("require_signatures"):
        config["profiles"] = [p for p in config["profiles"] if not (p == "security-signed" or p.startswith("security-signed/"))]
        print("security-signed/1.0 left out of the profiles: signatures are not required. "
              "Set require_signatures in chap.config.json, or CHAP_REQUIRE_SIGNATURES=1, to refuse unsigned calls.")


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
    The desk does this for a human; agent.py does it for the agent.
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


def _as_dict(value: Any) -> Any:
    """A dataclass with to_dict, a dict, or None, as plain JSON data."""
    if value is None or isinstance(value, dict):
        return value
    to_dict = getattr(value, "to_dict", None)
    return to_dict() if callable(to_dict) else value


def open_reviews(coord: Coordinator, workspace: str, reviewer: str | None = None) -> list[dict]:
    """The open reviews a reviewer can act on, read from the owning process."""
    ws = coord.get_workspace(workspace)
    if ws is None:
        return []
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
        })
    return sorted(out, key=lambda r: r["requested_at"])


def task_view(coord: Coordinator, workspace: str, task_id: str) -> dict | None:
    """One task, for an agent waiting on a decision."""
    ws = coord.get_workspace(workspace)
    task = ws.tasks.get(task_id) if ws is not None else None
    if task is None:
        return None
    return {
        "task_id": task.id, "kind": task.kind, "state": task.state, "assignee": task.assignee,
        "output": task.output, "review": _as_dict(task.review),
        "history": [_as_dict(h) for h in task.history],
    }


def public_config(config: dict) -> dict:
    return {
        "workspace": config["workspace"],
        "profiles": config["profiles"],
        "humans": [{"uri": h.get("uri"), "display_name": h.get("display_name"), "role": h.get("role")}
                   for h in (config.get("humans") or [])],
        "agent": config.get("agent"),
        "require_signatures": bool(config.get("require_signatures")),
        "chain_enabled": bool(config.get("chain")),
        "oidc": None,
        "mcp": False,
    }


# -- HTTP ---------------------------------------------------------------------

class ChapServer(ThreadingHTTPServer):
    """The HTTP server, holding the coordinator and the lock that serialises it."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, config: dict, coord: Coordinator, desk: str, client_module: str):
        self.config = config
        self.coord = coord
        self.desk = desk
        self.client_module = client_module
        self.lock = threading.Lock()
        super().__init__(address, Handler)

    @property
    def base(self) -> str:
        return f"http://{self.config['host']}:{self.server_port}"


class Handler(BaseHTTPRequestHandler):
    server_version = "CHAPDesk/0.3"
    server: ChapServer

    def log_message(self, *_: Any) -> None:
        pass

    def _reply(self, status: int, body: Any, content_type: str = "application/json") -> None:
        if content_type == "application/json":
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        else:
            data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> str:
        size = int(self.headers.get("content-length") or 0)
        return self.rfile.read(size).decode("utf-8") if size > 0 else ""

    def _foreign_host(self) -> bool:
        """True when the Host header is not one of this server's own names.

        A page that resolves its own name to this address (DNS rebinding)
        arrives with that name as the Host, and is refused before any route.
        """
        config = self.server.config
        host = (self.headers.get("host") or "").lower()
        port = self.server.server_address[1]
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}", f"{config['host']}:{port}".lower()}
        allowed.update(h.lower() for h in (config.get("allowed_hosts") or []))
        return host not in allowed

    def _foreign_origin(self) -> bool:
        """True for a browser request from another origin.

        The desk is served from this process, so its requests carry this
        server's own origin or none. A page on another origin gets no
        cross-origin headers and its calls are refused here, so an open tab
        elsewhere cannot decide as the reviewer. A non-browser client sends
        no Origin header.
        """
        origin = self.headers.get("origin")
        if not origin:
            return False
        host = self.headers.get("host") or ""
        return origin.lower() != f"http://{host}".lower()

    def do_GET(self) -> None:
        self._route("GET")

    def do_POST(self) -> None:
        self._route("POST")

    def _route(self, method: str) -> None:
        srv = self.server
        config, coord = srv.config, srv.coord
        url = urlsplit(self.path)
        path = url.path
        try:
            if self._foreign_host():
                return self._reply(421, {"error": "unknown host; set allowed_hosts in chap.config.json to serve under another name"})
            if self._foreign_origin():
                return self._reply(403, {"error": "cross-origin requests are refused"})
            if path in ("/", "/desk", "/desk.html"):
                return self._reply(200, srv.desk, "text/html; charset=utf-8")
            if path == "/chap-client.mjs":
                return self._reply(200, srv.client_module, "text/javascript; charset=utf-8")
            if path == "/chap" and method == "POST":
                if not (self.headers.get("content-type") or "").lower().startswith("application/json"):
                    return self._reply(415, {"error": "POST /chap takes application/json"})
                try:
                    envelope = json.loads(self._body())
                except ValueError:
                    return self._reply(400, {"jsonrpc": "2.0", "id": None,
                                             "error": {"code": -32700, "message": "Parse error"}})
                with srv.lock:
                    return self._reply(200, coord.dispatch(envelope))
            if path == "/api/config":
                return self._reply(200, public_config(config))
            if path == "/api/reviews":
                reviewer = (parse_qs(url.query).get("reviewer") or [None])[0] or None
                with srv.lock:
                    return self._reply(200, {"reviews": open_reviews(coord, config["workspace"], reviewer)})
            if path.startswith("/api/tasks/"):
                task_id = unquote(path[len("/api/tasks/"):])
                with srv.lock:
                    view = task_view(coord, config["workspace"], task_id)
                return self._reply(200, view) if view else self._reply(404, {"error": "unknown task"})
            if path == "/api/health":
                with srv.lock:
                    ws = coord.get_workspace(config["workspace"])
                    return self._reply(200, {
                        "ok": True, "workspace": config["workspace"],
                        "members": len(ws.members) if ws else 0,
                        "tasks": len(ws.tasks) if ws else 0,
                        "audit": len(ws.audit) if ws else 0,
                    })
            self._reply(404, {"error": "not found"})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # the desk shows the message; the traceback stays here
            self._reply(500, {"error": str(exc)})


def make_server(config: dict, coord: Coordinator) -> ChapServer:
    """Bind the server. Port 0 picks a free port; read it from server.server_port."""
    try:
        desk = (DESK_DIR / "desk.html").read_text(encoding="utf-8")
        client_module = (DESK_DIR / "chap-client.mjs").read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SystemExit(f"The desk is missing: {exc.filename}. It belongs in {DESK_DIR}.") from exc
    return ChapServer((config["host"], int(config["port"])), config, coord, desk, client_module)


def serve(config: dict | None = None) -> None:
    """Run the server until interrupted. The entry point for desk.py."""
    config = config or load_config()
    coord = make_coordinator(config)
    server = make_server(config, coord)
    base = server.base
    flags = ""
    if config.get("chain"):
        flags += ", chain on"
    if config.get("require_signatures"):
        flags += ", signatures required"
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
        store = coord.options.store
        if store is not None:
            store.close()


if __name__ == "__main__":
    serve()
