"""
chap_coordinator.crypto

Ed25519 signing for CHAP envelopes (RFC 8032), with deterministic
demo keys derived from participant URIs. Production deployments
supply real keys via the Keyring interface.

This module is required only for the ``security-signed/1.0`` profile.
Core can run without it; the rest of the package is import-safe even
when the ``cryptography`` library is not installed (we import lazily
inside functions and surface a clear error if a caller actually
needs signing).
"""
from __future__ import annotations

import base64
import hashlib
from typing import Any


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64url_nopad(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _require_cryptography():
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
            Ed25519PublicKey,
        )
        return Ed25519PrivateKey, Ed25519PublicKey
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "The 'cryptography' package is required for security-signed/1.0. "
            "Install with: pip install chap-coordinator[crypto]"
        ) from exc


def derive_private_key(uri: str) -> Any:
    """Deterministically derive a private key from a participant URI.

    Demo / test use. Production deployments supply real keys.
    """
    Ed25519PrivateKey, _ = _require_cryptography()
    seed = hashlib.sha256(("chap:" + uri).encode("utf-8")).digest()
    return Ed25519PrivateKey.from_private_bytes(seed)


def public_jwk(uri: str, key: Any | None = None) -> dict:
    """Return the RFC 7517 JWK for a participant's public key."""
    key = key or derive_private_key(uri)
    raw = key.public_key().public_bytes_raw()
    return {
        "kty": "OKP",
        "crv": "Ed25519",
        "kid": hashlib.sha256(uri.encode("utf-8")).hexdigest()[:16],
        "use": "sig",
        "alg": "EdDSA",
        "x": _b64url_nopad(raw),
    }


def sign(canonical_bytes: bytes, key: Any) -> str:
    """Sign canonical bytes, returning ``ed25519:<base64>``."""
    return "ed25519:" + _b64(key.sign(canonical_bytes))


def verify(canonical_bytes: bytes, sig: str, public_key: Any) -> bool:
    """Verify a CHAP signature string against canonical bytes."""
    if not sig.startswith("ed25519:"):
        return False
    raw = sig[len("ed25519:"):]
    # Allow the optional ed25519:<kid>:<sig> form
    if raw.count(":") >= 1:
        raw = raw.split(":")[-1]
    try:
        public_key.verify(base64.b64decode(raw), canonical_bytes)
        return True
    except Exception:
        return False


class Keyring:
    """Holds participant signing keys.

    Deterministic by URI unless keys are injected explicitly.
    Used by the ``security-signed/1.0`` profile.
    """

    def __init__(self) -> None:
        self._keys: dict[str, Any] = {}

    def key_for(self, uri: str) -> Any:
        if uri not in self._keys:
            self._keys[uri] = derive_private_key(uri)
        return self._keys[uri]

    def add(self, uri: str, key: Any) -> None:
        self._keys[uri] = key

    def jwk(self, uri: str) -> dict:
        return public_jwk(uri, self.key_for(uri))

    def public_key(self, uri: str) -> Any:
        return self.key_for(uri).public_key()
