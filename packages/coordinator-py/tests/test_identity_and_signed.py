"""Tests for security-signed/1.0 and identity-oidc/1.0 / identity-vc/1.0."""
from __future__ import annotations

import base64
import copy
import json

import pytest

from chap_coordinator import (
    Coordinator,
    CoordinatorOptions,
    canonicalize,
)


def _b64url_nopad(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


# ============================================================
#   security-signed/1.0
# ============================================================

def _gen_keypair():
    """Generate an Ed25519 keypair for a test participant."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )
    sk = Ed25519PrivateKey.generate()
    pub = sk.public_key().public_bytes_raw()
    return sk, pub


def _sign_envelope(sk, envelope: dict, kid: str) -> dict:
    """Sign the JCS of envelope-minus-sig and attach as top-level `sig`."""
    stripped = copy.deepcopy(envelope)
    stripped.pop("sig", None)
    canonical = canonicalize(stripped)
    sig_bytes = sk.sign(canonical)
    envelope["sig"] = f"ed25519:{kid}:{base64.b64encode(sig_bytes).decode('ascii')}"
    return envelope


def test_signed_envelope_verifies():
    """A correctly-signed envelope passes through dispatch."""
    coord = Coordinator(CoordinatorOptions(
        require_signatures=True,
        deterministic_ids=True, deterministic_clock=True,
    ))
    sk, pub = _gen_keypair()
    kid = "k-test-1"
    jwk = {"kty": "OKP", "crv": "Ed25519", "kid": kid, "x": _b64url_nopad(pub)}

    # workspace.create and participant.join happen without signatures
    # (per spec S4: first announce is trust-on-first-use).
    coord.dispatch({
        "jsonrpc": "2.0", "id": "1",
        "method": "workspace.create",
        "params": {"workspace": "wsp_signed"},
    })
    coord.dispatch({
        "jsonrpc": "2.0", "id": "2",
        "method": "participant.join",
        "params": {"workspace": "wsp_signed",
                   "from": "human:alice@x", "type": "human", "role": "owner",
                   "jwks": {"keys": [jwk]}},
    })
    # Now create another participant for the assignee role
    sk2, pub2 = _gen_keypair()
    jwk2 = {"kty": "OKP", "crv": "Ed25519", "kid": "k-bot",
            "x": _b64url_nopad(pub2)}
    coord.dispatch({
        "jsonrpc": "2.0", "id": "3",
        "method": "participant.join",
        "params": {"workspace": "wsp_signed",
                   "from": "agent:bot", "type": "agent", "role": "drafter",
                   "jwks": {"keys": [jwk2]}},
    })

    # Subsequent signed envelope must verify
    env = {
        "jsonrpc": "2.0", "id": "4",
        "method": "task.create",
        "params": {"workspace": "wsp_signed",
                   "from": "human:alice@x", "kind": "draft", "input": {},
                   "assignee": "agent:bot"},
    }
    _sign_envelope(sk, env, kid)
    r = coord.dispatch(env)
    assert "result" in r


def test_signed_envelope_missing_sig_rejected():
    coord = Coordinator(CoordinatorOptions(
        require_signatures=True,
        deterministic_ids=True, deterministic_clock=True,
    ))
    coord.dispatch({
        "jsonrpc": "2.0", "id": "1",
        "method": "workspace.create",
        "params": {"workspace": "wsp_x"},
    })
    coord.dispatch({
        "jsonrpc": "2.0", "id": "2",
        "method": "participant.join",
        "params": {"workspace": "wsp_x",
                   "from": "human:alice@x", "type": "human", "role": "r"},
    })
    coord.dispatch({
        "jsonrpc": "2.0", "id": "3",
        "method": "participant.join",
        "params": {"workspace": "wsp_x",
                   "from": "agent:bot", "type": "agent", "role": "d"},
    })
    # No sig field
    r = coord.dispatch({
        "jsonrpc": "2.0", "id": "4",
        "method": "task.create",
        "params": {"workspace": "wsp_x", "from": "human:alice@x",
                   "kind": "k", "input": {}, "assignee": "agent:bot"},
    })
    assert r["error"]["code"] == -32070  # SIG_VERIFY_FAILED


def test_signed_envelope_tampered_rejected():
    coord = Coordinator(CoordinatorOptions(
        require_signatures=True,
        deterministic_ids=True, deterministic_clock=True,
    ))
    sk, pub = _gen_keypair()
    kid = "k-1"
    jwk = {"kty": "OKP", "crv": "Ed25519", "kid": kid, "x": _b64url_nopad(pub)}
    coord.dispatch({"jsonrpc": "2.0", "id": "1",
                    "method": "workspace.create",
                    "params": {"workspace": "wsp_t"}})
    coord.dispatch({"jsonrpc": "2.0", "id": "2",
                    "method": "participant.join",
                    "params": {"workspace": "wsp_t",
                               "from": "human:alice@x", "type": "human",
                               "role": "r", "jwks": {"keys": [jwk]}}})
    coord.dispatch({"jsonrpc": "2.0", "id": "3",
                    "method": "participant.join",
                    "params": {"workspace": "wsp_t",
                               "from": "agent:bot", "type": "agent",
                               "role": "d"}})

    env = {
        "jsonrpc": "2.0", "id": "4",
        "method": "task.create",
        "params": {"workspace": "wsp_t", "from": "human:alice@x",
                   "kind": "k", "input": {"orig": True}, "assignee": "agent:bot"},
    }
    _sign_envelope(sk, env, kid)
    # Tamper with the payload after signing
    env["params"]["input"]["tampered"] = True
    r = coord.dispatch(env)
    assert r["error"]["code"] == -32070


def test_participant_rotate_key():
    coord = Coordinator(CoordinatorOptions(
        deterministic_ids=True, deterministic_clock=True,
    ))
    coord.dispatch({"jsonrpc": "2.0", "id": "1",
                    "method": "workspace.create",
                    "params": {"workspace": "wsp_r"}})
    sk_old, pub_old = _gen_keypair()
    jwk_old = {"kty": "OKP", "crv": "Ed25519", "kid": "k-old",
               "x": _b64url_nopad(pub_old)}
    coord.dispatch({"jsonrpc": "2.0", "id": "2",
                    "method": "participant.join",
                    "params": {"workspace": "wsp_r",
                               "from": "human:alice@x", "type": "human",
                               "role": "r", "jwks": {"keys": [jwk_old]}}})

    sk_new, pub_new = _gen_keypair()
    jwk_new = {"kty": "OKP", "crv": "Ed25519", "kid": "k-new",
               "x": _b64url_nopad(pub_new)}
    r = coord.dispatch({"jsonrpc": "2.0", "id": "3",
                        "method": "participant.rotate_key",
                        "params": {"workspace": "wsp_r",
                                   "from": "human:alice@x",
                                   "old_kid": "k-old",
                                   "new_jwk": jwk_new}})
    assert r["result"]["rotated"] is True
    # Old key should have a valid_until; new key has valid_from
    member = coord.workspaces["wsp_r"].members["human:alice@x"]
    old = next(k for k in member.keys if k.kid == "k-old")
    new = next(k for k in member.keys if k.kid == "k-new")
    assert old.valid_until is not None
    assert new.valid_from is not None


def test_participant_revoke_key():
    coord = Coordinator(CoordinatorOptions(
        deterministic_ids=True, deterministic_clock=True,
    ))
    sk, pub = _gen_keypair()
    jwk = {"kty": "OKP", "crv": "Ed25519", "kid": "k-1",
           "x": _b64url_nopad(pub)}
    coord.dispatch({"jsonrpc": "2.0", "id": "1",
                    "method": "workspace.create",
                    "params": {"workspace": "wsp_rv"}})
    coord.dispatch({"jsonrpc": "2.0", "id": "2",
                    "method": "participant.join",
                    "params": {"workspace": "wsp_rv",
                               "from": "human:alice@x", "type": "human",
                               "role": "r", "jwks": {"keys": [jwk]}}})
    r = coord.dispatch({"jsonrpc": "2.0", "id": "3",
                        "method": "participant.revoke_key",
                        "params": {"workspace": "wsp_rv",
                                   "from": "human:admin@x",
                                   "target_uri": "human:alice@x",
                                   "kid": "k-1",
                                   "reason": "test"}})
    assert r["result"]["revoked"] is True


# ============================================================
#   identity-oidc/1.0
# ============================================================

def test_oidc_token_binds_cnf_jwk():
    captured: list[str] = []

    def verify_token(token: str) -> dict | None:
        captured.append(token)
        if token == "valid-token":
            return {
                "sub": "user-123",
                "auth_time": 1747476000,
                "cnf": {"jwk": {"kty": "OKP", "crv": "Ed25519",
                                "kid": "oidc-key", "x": "AA"}},
            }
        return None

    coord = Coordinator(CoordinatorOptions(
        verify_oidc_token=verify_token,
        deterministic_ids=True, deterministic_clock=True,
    ))
    r = coord.dispatch({
        "jsonrpc": "2.0", "id": "1",
        "method": "participant.join",
        "params": {"workspace": "wsp_o", "from": "human:alice@x",
                   "type": "human", "role": "r",
                   "oidc_token": "valid-token"},
    })
    assert r["result"]["joined"] is True
    member = coord.workspaces["wsp_o"].members["human:alice@x"]
    assert member.oidc_sub == "user-123"
    assert member.oidc_auth_time == 1747476000
    # The cnf.jwk was pinned
    assert any(k.kid == "oidc-key" for k in member.keys)


def test_oidc_token_invalid_rejected():
    def verify_token(token: str) -> dict | None:
        return None  # All tokens reject

    coord = Coordinator(CoordinatorOptions(
        verify_oidc_token=verify_token,
        deterministic_ids=True, deterministic_clock=True,
    ))
    r = coord.dispatch({
        "jsonrpc": "2.0", "id": "1",
        "method": "participant.join",
        "params": {"workspace": "wsp_o", "from": "human:alice@x",
                   "type": "human", "role": "r",
                   "oidc_token": "bad-token"},
    })
    assert r["error"]["code"] == -32403  # OIDC_TOKEN_INVALID


# ============================================================
#   identity-vc/1.0
# ============================================================

def test_vc_presentation_binds_holder():
    def verify_vc(presentation: dict) -> dict | None:
        if presentation.get("type") == "VerifiablePresentation":
            return {"holder": "did:example:alice",
                    "cnf_jwk": {"kty": "OKP", "crv": "Ed25519",
                                "kid": "vc-key", "x": "BB"}}
        return None

    coord = Coordinator(CoordinatorOptions(
        verify_vc=verify_vc,
        deterministic_ids=True, deterministic_clock=True,
    ))
    r = coord.dispatch({
        "jsonrpc": "2.0", "id": "1",
        "method": "participant.join",
        "params": {"workspace": "wsp_v", "from": "human:alice@x",
                   "type": "human", "role": "r",
                   "vc_presentation": {"type": "VerifiablePresentation"}},
    })
    assert r["result"]["joined"] is True
    member = coord.workspaces["wsp_v"].members["human:alice@x"]
    assert member.vc_holder == "did:example:alice"
    assert any(k.kid == "vc-key" for k in member.keys)


def test_vc_presentation_invalid_rejected():
    def verify_vc(presentation: dict) -> dict | None:
        return None

    coord = Coordinator(CoordinatorOptions(
        verify_vc=verify_vc,
        deterministic_ids=True, deterministic_clock=True,
    ))
    r = coord.dispatch({
        "jsonrpc": "2.0", "id": "1",
        "method": "participant.join",
        "params": {"workspace": "wsp_v", "from": "human:alice@x",
                   "type": "human", "role": "r",
                   "vc_presentation": {"type": "Bogus"}},
    })
    assert r["error"]["code"] == -32410  # VC_VP_INVALID
