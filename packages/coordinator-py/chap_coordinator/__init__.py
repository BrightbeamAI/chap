"""
chap_coordinator

Python reference implementation of the Collaborative Human-Agent
Protocol (CHAP). See https://github.com/BrightbeamAI/chap for the
specification, schemas, and TypeScript reference.

Quick start::

    from chap_coordinator import Coordinator, CoordinatorOptions

    coord = Coordinator(CoordinatorOptions(default_profiles=[
        "core/1.0", "review/1.0", "whisper/1.0",
    ]))

    response = coord.dispatch({
        "jsonrpc": "2.0", "id": "1",
        "method": "workspace.create",
        "params": {"workspace": "wsp_demo"},
    })

This module owns the protocol semantics. Transport (HTTP, WebSocket,
stdio), persistence, and identity integration are deployment
concerns. See ``reference/python/server.py`` for a minimal HTTP
binding.
"""

from .canonical import ZERO_HASH, canonicalize, content_hash, sha256_hex
from .coordinator import Coordinator, CoordinatorOptions
from .crypto import Keyring, derive_private_key, public_jwk, sign, verify
from .ids import IdFactory
from .jsonrpc import E, is_valid_envelope, make_response, rpc_error
from .patch import PatchError, apply_json_patch
from .types import (
    AuditEntry,
    Deliberation,
    Handoff,
    HandoffTask,
    KeyRecord,
    Member,
    OverrideArtefact,
    ReviewState,
    RouteDecisionArtefact,
    SnapshotArtefact,
    Task,
    TaskHistoryEntry,
    WhisperPrompt,
    Workspace,
)

try:
    from importlib.metadata import version as _pkg_version, PackageNotFoundError
    __version__ = _pkg_version("chap-coordinator")
except PackageNotFoundError:  # running from a source checkout, not installed
    __version__ = "0.0.0+source"

__all__ = [
    # Core class & options
    "Coordinator",
    "CoordinatorOptions",
    # Types
    "AuditEntry",
    "Deliberation",
    "Handoff",
    "HandoffTask",
    "KeyRecord",
    "Member",
    "OverrideArtefact",
    "ReviewState",
    "RouteDecisionArtefact",
    "SnapshotArtefact",
    "Task",
    "TaskHistoryEntry",
    "WhisperPrompt",
    "Workspace",
    # Cryptography & canonicalisation
    "canonicalize",
    "content_hash",
    "sha256_hex",
    "ZERO_HASH",
    "Keyring",
    "derive_private_key",
    "public_jwk",
    "sign",
    "verify",
    # Helpers
    "IdFactory",
    "E",
    "make_response",
    "rpc_error",
    "is_valid_envelope",
    "PatchError",
    "apply_json_patch",
    # Metadata
    "__version__",
]
