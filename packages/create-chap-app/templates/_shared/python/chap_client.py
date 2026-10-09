"""A CHAP client over HTTP, for the agent process.

The desk process owns the store and answers CHAP calls at POST /chap. This
module gives the agent two ways in:

    HttpCoordinator(url)        looks like a Coordinator to code that calls
                                coord.dispatch(envelope), so a framework
                                bridge written against an in-process
                                coordinator runs unchanged over HTTP
    Participant(client, ...)    one participant's calls, with the error
                                raised as ChapError and a wait for the
                                decision on a task

Only the standard library is used. A ``human:`` or ``agent:`` URI labels a
participant; it does not authenticate anyone. Under security-signed/1.0 a
Signer signs each call with the participant's own key.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import quote
from uuid import uuid4

BOOTSTRAP_METHODS = ("workspace.create", "participant.join")


class ChapError(RuntimeError):
    """The coordinator refused a call. ``code`` is the JSON-RPC error code."""

    def __init__(self, code: int, message: str, data: Any = None, method: str | None = None):
        prefix = f"{method}: " if method else ""
        super().__init__(f"{prefix}{message} ({code})")
        self.code = code
        self.message = message
        self.data = data
        self.method = method


class HttpCoordinator:
    """The desk process, seen through POST /chap.

    ``dispatch(envelope)`` returns the JSON-RPC response as a dict, which is
    what an in-process Coordinator returns, so the framework bridges accept
    this object in place of one. ``workspaces`` answers the two questions the
    bridges ask after a refused setup call: whether a workspace exists and
    whether a URI is a member, both read with workspace.describe.
    """

    def __init__(self, url: str = "http://127.0.0.1:8787/chap", timeout: float = 30.0):
        self.url = url
        self.base = url[:-len("/chap")] if url.endswith("/chap") else url.rsplit("/", 1)[0]
        self.timeout = timeout
        self.workspaces = _RemoteWorkspaces(self)

    def dispatch(self, envelope: dict) -> dict:
        body = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.url, data=body, method="POST",
            headers={"content-type": "application/json", "accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict) and "error" in parsed:
                return parsed
            raise ChapError(-32603, f"POST {self.url} answered {exc.code}: {text[:200]}") from exc

    def get(self, path: str) -> Any:
        """A JSON GET from the desk process. A 404 is returned as None."""
        try:
            with urllib.request.urlopen(self.base + path, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise

    def describe(self, workspace: str) -> dict | None:
        r = self.dispatch({"jsonrpc": "2.0", "id": f"describe-{uuid4().hex}",
                           "method": "workspace.describe", "params": {"workspace": workspace}})
        return r.get("result") if "error" not in r else None


class _RemoteWorkspace:
    """What a bridge reads from a workspace: its id and who its members are."""

    def __init__(self, described: dict):
        self.id = described.get("id")
        self.state = described.get("state")
        self.mode = described.get("mode")
        self.profiles = list(described.get("profiles") or [])
        self.members = {m["uri"]: m for m in described.get("members") or [] if "uri" in m}


class _RemoteWorkspaces:
    def __init__(self, client: HttpCoordinator):
        self._client = client

    def get(self, workspace: str, default: Any = None) -> Any:
        if not isinstance(workspace, str):
            return default
        described = self._client.describe(workspace)
        return _RemoteWorkspace(described) if described else default

    def __contains__(self, workspace: object) -> bool:
        return self.get(workspace) is not None  # type: ignore[arg-type]


class Signer:
    """One Ed25519 key for one participant, for security-signed/1.0.

    The key is derived from the URI with the coordinator's demo helper, so a
    restart signs with the same key. A deployment supplies real keys.
    """

    def __init__(self, uri: str, key: Any = None):
        from chap_coordinator import crypto
        self._crypto = crypto
        self.uri = uri
        self.key = key or crypto.derive_private_key(uri)
        self.public_jwk = crypto.public_jwk(uri, self.key)
        self.kid = self.public_jwk["kid"]

    def sign(self, envelope: dict) -> dict:
        """The envelope with its ``sig`` set. The input is not changed."""
        from chap_coordinator.canonical import canonicalize
        unsigned = {k: v for k, v in envelope.items() if k != "sig"}
        sig = self._crypto.sign(canonicalize(unsigned), self.key, self.kid)
        return {**unsigned, "sig": sig}


class Participant:
    """One participant's calls to one workspace."""

    def __init__(self, client: HttpCoordinator, workspace: str, uri: str, signer: Signer | None = None):
        self.client = client
        self.workspace = workspace
        self.uri = uri
        self.signer = signer

    def envelope(self, method: str, params: dict) -> dict:
        env = {"jsonrpc": "2.0", "id": f"{self.uri}-{uuid4().hex}", "method": method,
               "params": {"workspace": self.workspace, "from": self.uri, **params}}
        if self.signer and method not in BOOTSTRAP_METHODS:
            env = self.signer.sign(env)
        return env

    def send(self, method: str, **params: Any) -> dict:
        """The raw JSON-RPC response, error and all."""
        return self.client.dispatch(self.envelope(method, params))

    def call(self, method: str, **params: Any) -> Any:
        """The result, or ChapError with the coordinator's code and message."""
        r = self.send(method, **params)
        if "error" in r:
            e = r["error"]
            raise ChapError(e.get("code", -32603), e.get("message", "refused"), e.get("data"), method)
        return r.get("result")

    def join(self, type: str, role: str, display_name: str | None = None) -> Any:
        """participant.join, registering this participant's key when it signs."""
        params: dict[str, Any] = {"type": type, "role": role}
        if display_name:
            params["display_name"] = display_name
        if self.signer:
            params["jwks"] = {"keys": [self.signer.public_jwk]}
        return self.call("participant.join", **params)

    def task(self, task_id: str) -> dict | None:
        """GET /api/tasks/<id>: the task's state, output, review and history."""
        return self.client.get("/api/tasks/" + quote(task_id, safe=""))

    def wait_for_decision(self, task_id: str, poll_seconds: float = 2.0,
                          timeout: float | None = None) -> dict:
        """Poll the task until its state is no longer review_requested.

        Returns the task view. Raises TimeoutError after ``timeout`` seconds
        and KeyError when the desk process does not know the task.
        """
        started = time.monotonic()
        while True:
            view = self.task(task_id)
            if view is None:
                raise KeyError(f"Unknown task: {task_id}")
            if view["state"] != "review_requested":
                return view
            if timeout is not None and time.monotonic() - started >= timeout:
                raise TimeoutError(f"No decision on {task_id} after {timeout} seconds")
            time.sleep(poll_seconds)


__all__ = ["ChapError", "HttpCoordinator", "Participant", "Signer"]
