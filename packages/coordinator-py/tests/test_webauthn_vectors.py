"""
conformance/webauthn-vectors.json: a WebAuthn assertion signs a request under
security-signed/1.0, and each check the relying party makes refuses an
assertion that fails it (6 November review, item 3).

Both coordinators verified ed25519: tags alone, so a person whose key sits in
a browser's authenticator could not sign at all. Without the cryptography
package the check is refused with -32070 and a message naming the crypto
extra. Mirrors webauthn_vectors.test.ts.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.canonical import canonicalize
from chap_coordinator.crypto import (
    CRYPTOGRAPHY_MISSING, derive_private_key, is_webauthn_key, parse_webauthn_assertion, public_jwk,
    sign,
)

VECTORS = json.loads((Path(__file__).resolve().parents[3]
                      / "conformance" / "webauthn-vectors.json").read_text("utf-8"))


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _ready():
    c = Coordinator(CoordinatorOptions(deterministic_ids=True, deterministic_clock=True))
    for env in VECTORS["setup"]:
        r = c.dispatch(env)
        assert "error" not in r, r
    return c


def test_the_vectors_assertion_signs_its_request():
    r = _ready().dispatch(VECTORS["accepted"]["envelope"])
    assert "error" not in r, r
    assert r["result"]["state"] == "created"


def test_the_vectors_parts_are_what_its_tag_carries():
    accepted = VECTORS["accepted"]
    unsigned = {k: v for k, v in accepted["envelope"].items() if k != "sig"}
    assert _b64u(hashlib.sha256(canonicalize(unsigned)).digest()) == accepted["challenge"]
    assertion = parse_webauthn_assertion(accepted["envelope"]["sig"].split(":")[2])
    assert assertion.authenticator_data.hex() == accepted["authenticator_data_hex"]
    assert assertion.client_data_json.decode("utf-8") == accepted["client_data_json"]
    assert assertion.signature.hex() == accepted["signature_hex"]
    assert json.loads(accepted["client_data_json"])["challenge"] == accepted["challenge"]
    assert is_webauthn_key(VECTORS["jwk"])


@pytest.mark.parametrize("vector", VECTORS["refused"], ids=lambda v: v["name"])
def test_refused(vector):
    c = _ready()
    before = len(c.workspaces["wsp_webauthn"].audit)
    r = c.dispatch(vector["envelope"])
    assert r["error"] == vector["error"]
    assert len(c.workspaces["wsp_webauthn"].audit) == before


def test_a_key_without_a_webauthn_relying_party_or_of_another_curve_is_not_a_webauthn_key():
    jwk = VECTORS["jwk"]
    assert not is_webauthn_key({k: v for k, v in jwk.items() if k != "webauthn"})
    assert not is_webauthn_key({**jwk, "crv": "P-384"})
    assert not is_webauthn_key({**jwk, "alg": "ES384"})
    assert not is_webauthn_key({**jwk, "webauthn": {**jwk["webauthn"],
                                                    "origins": "https://desk.example.org"}})
    assert not is_webauthn_key({**jwk, "x": jwk["x"] + "="})


def test_a_key_that_lists_no_origins_takes_an_assertion_from_any():
    # Sign afresh with the vector's private key, for a key that names no origins.
    jwk = {**VECTORS["jwk"], "kid": "any-origin", "webauthn": {"rp_id": "example.org"}}
    key = ec.derive_private_key(int(VECTORS["private_scalar_hex"], 16), ec.SECP256R1())
    c = Coordinator(CoordinatorOptions(deterministic_ids=True, deterministic_clock=True))
    c.dispatch(dict(VECTORS["setup"][0]))
    c.dispatch({"jsonrpc": "2.0", "id": "j", "method": "participant.join",
                "params": {"workspace": "wsp_webauthn", "from": "human:carol@example.org",
                           "type": "human", "jwks": {"keys": [jwk]}}})
    request = {"jsonrpc": "2.0", "id": "r", "method": "task.create",
               "params": {"workspace": "wsp_webauthn", "from": "human:carol@example.org",
                          "kind": "k", "input": {}, "assignee": "human:carol@example.org"}}
    client_data = json.dumps({"type": "webauthn.get",
                              "challenge": _b64u(hashlib.sha256(canonicalize(request)).digest()),
                              "origin": "https://anywhere.example"}).encode()
    auth = hashlib.sha256(b"example.org").digest() + bytes([0x05, 0, 0, 0, 7])
    signature = key.sign(auth + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
    sig = "webauthn:any-origin:" + ".".join(_b64u(b) for b in (auth, client_data, signature))
    r = c.dispatch({**request, "sig": sig})
    assert "error" not in r, r


def test_a_tag_with_more_than_three_parts_is_malformed():
    envelope = VECTORS["accepted"]["envelope"]
    r = _ready().dispatch({**envelope, "sig": envelope["sig"] + ":x"})
    assert r["error"] == {"code": -32070, "message": "Malformed signature tag"}


def test_without_the_cryptography_package_a_signature_is_refused_naming_the_extra(monkeypatch):
    bob = "human:bob@example.org"
    request = {"jsonrpc": "2.0", "id": "b", "method": "task.create",
               "params": {"workspace": "wsp_webauthn", "from": bob, "kind": "k", "input": {},
                          "assignee": bob}}
    signed = {**request, "sig": sign(canonicalize(request), derive_private_key(bob),
                                     public_jwk(bob)["kid"])}
    c = _ready()
    # As if the package were not installed: every import of it fails.
    for name in [m for m in sys.modules if m == "cryptography" or m.startswith("cryptography.")]:
        monkeypatch.setitem(sys.modules, name, None)
    assert c.dispatch(VECTORS["accepted"]["envelope"])["error"] == {
        "code": -32070, "message": CRYPTOGRAPHY_MISSING}
    assert c.dispatch(signed)["error"] == {"code": -32070, "message": CRYPTOGRAPHY_MISSING}
