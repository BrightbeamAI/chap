"""
chap_coordinator.crypto

Ed25519 signing for CHAP envelopes (RFC 8032), with deterministic
demo keys derived from participant URIs, and verification of WebAuthn
assertions, the signatures a browser's authenticator makes with a P-256
key. Production deployments supply real keys via the Keyring interface.

This module is required only for the ``security-signed/1.0`` profile.
Core can run without it; the rest of the package is import-safe even
when the ``cryptography`` library is not installed (we import lazily
inside functions and surface a clear error if a caller actually
needs signing).
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Any, NamedTuple

from .canonical import canonicalize


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def strict_base64(s: str) -> bytes | None:
    """The bytes a standard base64 string encodes, or None where it is not
    the one canonical encoding of them: standard alphabet, padded, and
    nothing else, so a signature has one spelling in every language."""
    try:
        data = base64.b64decode(s, validate=True)
    except ValueError:
        return None
    return data if _b64(data) == s else None


def _b64url_nopad(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


#: What a signature check without the ``cryptography`` package answers.
CRYPTOGRAPHY_MISSING = ("Signature verification needs the cryptography package: "
                        "pip install chap-coordinator[crypto]")


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


def jwk_thumbprint(jwk: Any) -> str | None:
    """The RFC 7638 thumbprint of a public JWK: SHA-256 over the JSON of its
    required members in lexicographic order, base64url without padding.

    The members are {crv, kty, x} for an OKP key and {crv, kty, x, y} for an
    EC key, and for any other key, or one whose members are not strings,
    there is none.
    """
    if not isinstance(jwk, dict):
        return None
    kty = jwk.get("kty")
    names = ("crv", "kty", "x") if kty == "OKP" else ("crv", "kty", "x", "y") if kty == "EC" else None
    if names is None or any(not isinstance(jwk.get(n), str) for n in names):
        return None
    members = {n: jwk[n] for n in names}
    return _b64url_nopad(hashlib.sha256(canonicalize(members)).digest())


class WebAuthnAssertion(NamedTuple):
    """A WebAuthn assertion, as the last part of a ``webauthn:`` tag carries it."""

    authenticator_data: bytes
    client_data_json: bytes
    signature: bytes


# Unpadded base64url. A length of 1 more than a multiple of 4 encodes no whole
# byte, and is refused here, since Node and Python decode it apart.
_B64URL = re.compile(r"[A-Za-z0-9_-]+")
# A P-256 coordinate: 32 bytes, unpadded base64url.
_COORDINATE = re.compile(r"[A-Za-z0-9_-]{43}")


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def parse_webauthn_assertion(part: str) -> WebAuthnAssertion | None:
    """The assertion in ``<authenticatorData>.<clientDataJSON>.<signature>``,
    each part unpadded base64url, or None where the part is not that."""
    pieces = part.split(".")
    if len(pieces) != 3 or any(not _B64URL.fullmatch(p) or len(p) % 4 == 1 for p in pieces):
        return None
    return WebAuthnAssertion(*(_b64url_decode(p) for p in pieces))


def is_ed25519_key(jwk: Any) -> bool:
    """Whether a JWK is an Ed25519 public key: OKP on Ed25519 with a 32-byte
    x."""
    return (isinstance(jwk, dict) and jwk.get("kty") == "OKP" and jwk.get("crv") == "Ed25519"
            and isinstance(jwk.get("x"), str) and _COORDINATE.fullmatch(jwk["x"]) is not None)


def is_webauthn_key(jwk: Any) -> bool:
    """Whether a JWK is a WebAuthn key: a P-256 key for ES256 with a
    ``webauthn`` member naming its ``rp_id``, and, where it limits them, its
    ``origins``."""
    if not isinstance(jwk, dict):
        return False
    w = jwk.get("webauthn")
    origins = w.get("origins") if isinstance(w, dict) else None
    return (jwk.get("kty") == "EC" and jwk.get("crv") == "P-256"
            and isinstance(jwk.get("x"), str) and _COORDINATE.fullmatch(jwk["x"]) is not None
            and isinstance(jwk.get("y"), str) and _COORDINATE.fullmatch(jwk["y"]) is not None
            and ("alg" not in jwk or jwk["alg"] == "ES256")
            and isinstance(w, dict) and isinstance(w.get("rp_id"), str) and w["rp_id"] != ""
            and ("origins" not in w
                 or (isinstance(origins, list) and all(isinstance(o, str) for o in origins))))


def _no_constant(name: str) -> Any:
    # JSON has no NaN or Infinity, and JavaScript's parser refuses them.
    raise ValueError(name)


#: How deeply a client data object may nest, as an envelope may (64 levels).
_MAX_CLIENT_DATA_NESTING = 64


def _json_depth(text: str) -> int:
    """How deeply JSON text nests, read from its brackets outside strings, so
    a text too deep for one language's parser is refused the same way by
    both."""
    depth = deepest = 0
    in_string = escaped = False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "{[":
            depth += 1
            deepest = max(deepest, depth)
        elif ch in "}]":
            depth -= 1
    return deepest


# The authenticator data's flags: user present, user verified, backup
# eligible, backed up, attested credential data and extension data.
_UP, _UV, _BE, _BS, _AT, _ED = 0x01, 0x04, 0x08, 0x10, 0x40, 0x80


def webauthn_failure(canonical: bytes, assertion: WebAuthnAssertion, jwk: dict) -> str | None:
    """Why a WebAuthn assertion does not sign a request, or None when it does.

    ``canonical`` is the JCS form of the request without its ``sig``, and
    ``jwk`` a key :func:`is_webauthn_key` accepts. The checks are the relying
    party's in the WebAuthn assertion procedure: the key names the origins it
    signs for; the client data says it is an assertion, over this request,
    from one of those origins, made in no other origin's frame; the
    authenticator data is for the key's relying party, with the user present
    and verified, its backup flags consistent, and nothing after its counter
    but the extensions its flags announce; and the ES256 signature covers the
    authenticator data and the hash of the client data.
    """
    w = jwk["webauthn"]
    origins = w.get("origins")
    if not isinstance(origins, list) or not origins:
        return "WebAuthn key lists no origin"
    try:
        text = assertion.client_data_json.decode("utf-8")
    except ValueError:
        text = None
    if text is not None and _json_depth(text) > _MAX_CLIENT_DATA_NESTING:
        return f"WebAuthn clientDataJSON nests deeper than {_MAX_CLIENT_DATA_NESTING} levels"
    try:
        client = None if text is None else json.loads(text, parse_constant=_no_constant)
    except ValueError:
        client = None
    if not isinstance(client, dict):
        return "WebAuthn clientDataJSON is not a JSON object"
    if client.get("type") != "webauthn.get":
        return "WebAuthn clientDataJSON type is not webauthn.get"
    if client.get("challenge") != _b64url_nopad(hashlib.sha256(canonical).digest()):
        return "WebAuthn challenge is not the digest of this request"
    if client.get("origin") not in origins:
        return "WebAuthn origin is not one the key allows"
    if client.get("crossOrigin") is True or "topOrigin" in client:
        return "WebAuthn assertion was made in a cross-origin frame"
    auth = assertion.authenticator_data
    if len(auth) < 37:
        return "WebAuthn authenticatorData is too short"
    if auth[:32] != hashlib.sha256(w["rp_id"].encode("utf-8")).digest():
        return "WebAuthn rpIdHash does not match the key's rp_id"
    flags = auth[32]
    if flags & (_UP | _UV) != _UP | _UV:
        return "WebAuthn assertion lacks user presence or verification"
    if flags & _BS and not flags & _BE:
        return "WebAuthn assertion is backed up but not backup eligible"
    # An assertion carries no attested credential data, and extension data
    # only where its flag says so.
    if flags & _AT:
        return "WebAuthn assertion carries attested credential data"
    if (len(auth) == 37) if flags & _ED else (len(auth) != 37):
        return "WebAuthn authenticatorData does not end where its flags say"
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
    except ImportError:
        return CRYPTOGRAPHY_MISSING
    try:
        key = ec.EllipticCurvePublicNumbers(
            int.from_bytes(_b64url_decode(jwk["x"]), "big"),
            int.from_bytes(_b64url_decode(jwk["y"]), "big"),
            ec.SECP256R1()).public_key()
        signed = auth + hashlib.sha256(assertion.client_data_json).digest()
        key.verify(assertion.signature, signed, ec.ECDSA(hashes.SHA256()))
        return None
    except (InvalidSignature, ValueError):
        return "Signature failed verification"


def sign(canonical_bytes: bytes, key: Any, kid: str) -> str:
    """Sign canonical bytes, returning ``ed25519:<kid>:<base64>``."""
    return "ed25519:" + kid + ":" + _b64(key.sign(canonical_bytes))


def verify(canonical_bytes: bytes, sig: str, public_key: Any) -> bool:
    """Verify a CHAP signature string against canonical bytes."""
    if not sig.startswith("ed25519:"):
        return False
    raw = sig[len("ed25519:"):]
    # Allow the optional ed25519:<kid>:<sig> form
    if raw.count(":") >= 1:
        raw = raw.split(":")[-1]
    signature = strict_base64(raw)
    if signature is None:
        return False
    try:
        public_key.verify(signature, canonical_bytes)
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
