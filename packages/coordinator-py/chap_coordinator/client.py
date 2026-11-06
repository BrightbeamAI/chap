"""
chap_coordinator.client
=======================

A CHAP client over HTTP, standard library only.

One process owns a coordinator and its store, and every other process
reaches it over HTTP. :mod:`chap_coordinator.server` answers CHAP calls at
``POST /chap`` and each task's state at ``GET /api/tasks/<id>``; this module
is the other end:

``HttpCoordinator(url)``
    The server, through its JSON-RPC endpoint. ``dispatch(envelope)``
    returns the JSON-RPC response as a dict, as an in-process Coordinator
    does, so code written against one runs over HTTP unchanged.
``Participant(client, workspace, uri, signer=None)``
    One participant's calls to one workspace. ``call`` returns the result
    or raises :class:`ChapError`. Every call carries the participant's
    ``from``, and with a :class:`Signer` every call is signed, reads
    included, so it works in a workspace that requires signatures.
``Signer``
    One Ed25519 key for one participant, in a file only its owner reads,
    for security-signed/1.0. It needs the ``crypto`` extra.
``wait_for_decision(client, task_id)``
    Waits until a decision moves a task out of ``review_requested``.

When no JSON-RPC answer comes at all, from a server that cannot be reached
or one that answers with something else, :class:`ChapEndpointError` is
raised. A ``human:`` or ``agent:`` URI labels a participant; it
authenticates nobody. Under security-signed/1.0 the coordinator checks each
signature against the key the participant registered at participant.join.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional, Protocol, Union
from urllib.parse import quote
from uuid import uuid4

from .canonical import canonicalize

__all__ = [
    "BOOTSTRAP_METHODS",
    "ChapEndpointError",
    "ChapError",
    "HttpCoordinator",
    "Participant",
    "Signer",
    "wait_for_decision",
]

#: The calls a participant makes before its key is registered, so never signed.
BOOTSTRAP_METHODS = ("workspace.create", "participant.join")


class ChapError(RuntimeError):
    """The coordinator refused a call. ``code`` is the JSON-RPC error code."""

    def __init__(self, code: int, message: str, data: Any = None, method: Optional[str] = None):
        prefix = f"{method}: " if method else ""
        super().__init__(f"{prefix}{message} ({code})")
        self.code = code
        self.message = message
        self.data = data
        self.method = method


class ChapEndpointError(Exception):
    """No JSON-RPC answer: the server could not be reached, or answered with
    something else. The message says which, in words to show a person or an
    assistant."""


def _said(body: Any) -> str:
    """The error a server's JSON body names, as the end of a sentence."""
    return f": {body['error']}" if isinstance(body, dict) and isinstance(body.get("error"), str) else ""


def _is_response(body: Any) -> bool:
    return (isinstance(body, dict) and body.get("jsonrpc") == "2.0"
            and ("result" in body or isinstance(body.get("error"), dict)))


class HttpCoordinator:
    """A CHAP server through its JSON-RPC endpoint ``url``, by default a desk
    server's ``POST /chap`` on this machine.

    ``dispatch(envelope)`` returns the JSON-RPC response, a refusal included,
    which is what an in-process Coordinator returns, so a framework bridge
    takes this object as its coordinator. ``workspaces`` answers the two questions
    the bridges ask after a refused setup call, whether a workspace exists
    and whether a URI is a member, with an unsigned workspace.describe; in a
    workspace that requires signatures, hand the bridge a :class:`Participant`.

    ``get`` and ``task`` read the desk server's API beside the endpoint.
    """

    def __init__(self, url: str = "http://127.0.0.1:8787/chap", *, timeout: float = 30.0) -> None:
        self.url = url
        self.base = url[:-len("/chap")] if url.endswith("/chap") else url.rsplit("/", 1)[0]
        self.timeout = timeout
        self.workspaces = _RemoteWorkspaces(self.describe)

    def dispatch(self, envelope: dict) -> dict:
        """POST one envelope; the JSON-RPC response, a refusal included."""
        request = urllib.request.Request(
            self.url, data=json.dumps(envelope, ensure_ascii=False).encode("utf-8"), method="POST",
            headers={"content-type": "application/json", "accept": "application/json"})
        status, text = self._open(request)
        try:
            body = json.loads(text)
        except ValueError:
            raise ChapEndpointError(
                f"The CHAP server at {self.url} answered HTTP {status} with a body that is not JSON.") from None
        if _is_response(body):
            return body
        raise ChapEndpointError(f"The CHAP server at {self.url} refused the call with HTTP {status}{_said(body)}")

    def get(self, path: str) -> Any:
        """A JSON GET from the server's API, ``path`` from its base, such as
        ``/api/health``; None for a 404."""
        url = self.base + path
        status, text = self._open(urllib.request.Request(url, headers={"accept": "application/json"}))
        if status == 404:
            return None
        try:
            body = json.loads(text)
        except ValueError:
            raise ChapEndpointError(f"GET {url} answered HTTP {status} with a body that is not JSON.") from None
        if status != 200:
            raise ChapEndpointError(f"GET {url} answered HTTP {status}{_said(body)}")
        return body

    def task(self, task_id: str) -> Optional[dict]:
        """``GET /api/tasks/<id>``: the task's state, output, review and
        history; None for a task the server does not know."""
        return self.get("/api/tasks/" + quote(task_id, safe=""))

    def describe(self, workspace: str) -> Optional[dict]:
        """workspace.describe, unsigned and from no one; None when refused."""
        r = self.dispatch({"jsonrpc": "2.0", "id": f"describe-{uuid4().hex}",
                           "method": "workspace.describe", "params": {"workspace": workspace}})
        return r.get("result") if "error" not in r else None

    def _open(self, request: urllib.request.Request) -> tuple[int, str]:
        """The status and the body of one request; an HTTP error is an answer too."""
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - the caller's own server
                return response.status, response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as err:
            return err.code, err.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError) as err:
            reason = getattr(err, "reason", err)
            raise ChapEndpointError(
                f"The CHAP server at {self.url} could not be reached ({reason}). Start it, or check its address."
            ) from None


class _RemoteWorkspace:
    """What a bridge reads of a workspace: its id, state, mode, profiles and members."""

    def __init__(self, described: dict) -> None:
        self.id = described.get("id")
        self.state = described.get("state")
        self.mode = described.get("mode")
        self.profiles = list(described.get("profiles") or [])
        self.members = {m["uri"]: m for m in described.get("members") or [] if "uri" in m}


class _RemoteWorkspaces:
    """``workspaces`` for code written against a Coordinator: ``in`` and
    ``get``, each read with workspace.describe."""

    def __init__(self, describe: Callable[[str], Optional[dict]]) -> None:
        self._describe = describe

    def get(self, workspace: str, default: Any = None) -> Any:
        if not isinstance(workspace, str):
            return default
        described = self._describe(workspace)
        return _RemoteWorkspace(described) if described else default

    def __contains__(self, workspace: object) -> bool:
        return self.get(workspace) is not None  # type: ignore[arg-type]


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _thumbprint(x: str) -> str:
    """The RFC 7638 thumbprint of an Ed25519 public key: SHA-256 over its
    required members in order, base64url without padding."""
    members = json.dumps({"crv": "Ed25519", "kty": "OKP", "x": x}, separators=(",", ":"), sort_keys=True)
    return _b64url_encode(hashlib.sha256(members.encode("ascii")).digest())


class Signer:
    """One Ed25519 key for one participant, for security-signed/1.0.

    :meth:`load_or_create` keeps the private key in
    ``<directory>/<uri slug>.jwk.json``, readable by its owner alone,
    generates it on first use and never overwrites it. The key id is the
    key's RFC 7638 thumbprint, so no two keys share one; a key file written
    by an earlier release keeps the id in it, which is the id it was
    registered under. Nothing here derives a key from the URI: a key anyone
    could derive would let anyone sign as the participant.
    """

    def __init__(self, uri: str, key: Any, kid: Optional[str] = None) -> None:
        from . import crypto
        self._crypto = crypto
        self.uri = uri
        self.key = key
        x = _b64url_encode(key.public_key().public_bytes_raw())
        self.kid = kid or _thumbprint(x)
        self.public_jwk = {"kty": "OKP", "crv": "Ed25519", "kid": self.kid, "use": "sig", "alg": "EdDSA", "x": x}

    @staticmethod
    def key_path(uri: str, directory: Union[str, os.PathLike] = "keys") -> Path:
        slug = re.sub(r"[^A-Za-z0-9._-]+", "_", uri)
        return Path(directory) / f"{slug}.jwk.json"

    @classmethod
    def load_or_create(cls, uri: str, directory: Union[str, os.PathLike] = "keys") -> "Signer":
        """The participant's signer, from its key file in ``directory``,
        generating the key on first use. A directory this makes is readable
        by its owner alone."""
        from .crypto import _require_cryptography
        Ed25519PrivateKey, _ = _require_cryptography()
        path = cls.key_path(uri, directory)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("kty") != "OKP" or data.get("crv") != "Ed25519" or "d" not in data:
                raise ValueError(f"{path} is not an Ed25519 private JWK")
            return cls(uri, Ed25519PrivateKey.from_private_bytes(_b64url_decode(data["d"])), data.get("kid"))
        key = Ed25519PrivateKey.generate()
        signer = cls(uri, key)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({**signer.public_jwk, "d": _b64url_encode(key.private_bytes_raw())}, handle, indent=2)
        return signer

    def sign(self, envelope: dict) -> dict:
        """The envelope with its ``sig`` set, over the JCS form of the rest.
        The input is not changed."""
        unsigned = {k: v for k, v in envelope.items() if k != "sig"}
        return {**unsigned, "sig": self._crypto.sign(canonicalize(unsigned), self.key, self.kid)}


class _TaskReader(Protocol):
    def task(self, task_id: str) -> Optional[dict]: ...


def wait_for_decision(client: _TaskReader, task_id: str, poll_seconds: float = 2.0,
                      timeout: Optional[float] = None) -> dict:
    """Read a task until its state is no longer ``review_requested``, and
    return its view; call it once the task is submitted for review.

    ``client`` is anything whose ``task(task_id)`` returns the task's view,
    or None for a task it does not know: an HttpCoordinator or a
    Participant. Raises TimeoutError after ``timeout`` seconds, and KeyError
    when the server does not know the task.
    """
    started = time.monotonic()
    while True:
        view = client.task(task_id)
        if view is None:
            raise KeyError(f"Unknown task: {task_id}")
        if view["state"] != "review_requested":
            return view
        if timeout is not None and time.monotonic() - started >= timeout:
            raise TimeoutError(f"No decision on {task_id} after {timeout} seconds")
        time.sleep(poll_seconds)


class Participant:
    """One participant's calls to one workspace, through an HttpCoordinator.

    Every call carries the participant's URI as ``from``. With a signer,
    every call is signed with its key, reads included: in a workspace that
    requires signatures an unsigned workspace.describe is refused, and an
    agent that took the refusal for a workspace with no reviewer waited for
    ever. workspace.create and participant.join are not signed, since they
    come before the key is registered.

    It also stands in for a coordinator, for code written against one, such
    as a framework bridge: ``dispatch`` signs an envelope whose ``from`` is
    this participant, and ``workspaces`` reads as this participant.
    """

    def __init__(self, client: HttpCoordinator, workspace: str, uri: str, signer: Optional[Signer] = None) -> None:
        if signer is not None and signer.uri != uri:
            raise ValueError(f"The signer holds the key of {signer.uri}, and this participant is {uri}")
        self.client = client
        self.workspace = workspace
        self.uri = uri
        self.signer = signer
        self.workspaces = _RemoteWorkspaces(self._described)

    def envelope(self, method: str, params: dict) -> dict:
        envelope = {"jsonrpc": "2.0", "id": f"{self.uri}-{uuid4().hex}", "method": method,
                    "params": {"workspace": self.workspace, "from": self.uri, **params}}
        return self._signed(envelope)

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

    def join(self, type: str, role: str, display_name: Optional[str] = None) -> Any:
        """participant.join, registering this participant's key when it signs."""
        params: dict[str, Any] = {"type": type, "role": role}
        if display_name:
            params["display_name"] = display_name
        if self.signer:
            params["jwks"] = {"keys": [self.signer.public_jwk]}
        return self.call("participant.join", **params)

    def describe(self) -> dict:
        """The workspace, read with workspace.describe as this participant."""
        return self.call("workspace.describe")

    def members(self) -> list[dict]:
        """The workspace's members."""
        return list(self.describe().get("members") or [])

    def is_paused(self) -> bool:
        """Whether this participant is paused."""
        return any(m.get("uri") == self.uri and bool(m.get("paused")) for m in self.members())

    def reviewer_present(self) -> bool:
        """Whether a human other than this participant is a member.

        A review opened on task.complete is addressed to the human members
        other than the completer, and the completion is refused, and the
        refusal recorded, when there are none. An agent that checks this
        first waits on a read instead.
        """
        return any(m.get("type") == "human" and m.get("uri") != self.uri for m in self.members())

    def task(self, task_id: str) -> Optional[dict]:
        """``GET /api/tasks/<id>``: the task's state, output, review and history."""
        return self.client.task(task_id)

    def wait_for_decision(self, task_id: str, poll_seconds: float = 2.0, timeout: Optional[float] = None) -> dict:
        """:func:`wait_for_decision` on this participant's server."""
        return wait_for_decision(self, task_id, poll_seconds, timeout)

    def dispatch(self, envelope: dict) -> dict:
        """Send an envelope built elsewhere, as by a bridge. One whose ``from``
        is this participant is signed when it holds a key; any other is sent
        as it came, for the coordinator to decide."""
        params = envelope.get("params")
        if isinstance(params, dict) and params.get("from") == self.uri:
            envelope = self._signed(envelope)
        return self.client.dispatch(envelope)

    def _signed(self, envelope: dict) -> dict:
        if self.signer and envelope.get("method") not in BOOTSTRAP_METHODS:
            return self.signer.sign(envelope)
        return envelope

    def _described(self, workspace: str) -> Optional[dict]:
        """A workspace read as this participant, for ``workspaces``; None when refused."""
        r = self.client.dispatch(self._signed({
            "jsonrpc": "2.0", "id": f"{self.uri}-{uuid4().hex}", "method": "workspace.describe",
            "params": {"workspace": workspace, "from": self.uri}}))
        return r.get("result") if "error" not in r else None
