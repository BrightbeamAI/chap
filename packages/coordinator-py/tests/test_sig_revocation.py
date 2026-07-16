"""
Regression test: a revoked signing key must not be usable by backdating the
envelope's self-asserted `ts` to before the revocation. Revocation is
checked against the coordinator's trusted clock. Guards the 0.2.7 fix.
"""
from __future__ import annotations

import base64

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.canonical import canonicalize
from chap_coordinator import crypto


def _setup():
    c = Coordinator(CoordinatorOptions(require_signatures=True))
    sender = "human:alice"
    priv = crypto.derive_private_key(sender)
    jwk = crypto.public_jwk(sender)
    kid = jwk["kid"]

    def signed(method, params, ts):
        env = {"jsonrpc": "2.0", "id": method, "method": method, "params": dict(params, ts=ts)}
        env["sig"] = f"ed25519:{kid}:" + base64.b64encode(priv.sign(canonicalize(env))).decode()
        return env

    c.dispatch({"jsonrpc": "2.0", "id": "j", "method": "participant.join",
                "params": {"workspace": "w", "from": sender, "type": "human",
                           "jwks": {"keys": [jwk]}, "profiles": ["core/1.0", "security-signed/1.0"]}})
    key = c.workspaces["w"].members[sender].keys[0]
    key.valid_from = "2026-01-01T00:00:00.000Z"
    return c, signed, key, sender


def test_revoked_key_cannot_be_used_with_backdated_ts():
    c, signed, key, sender = _setup()
    key.revoked_at = "2026-06-01T00:00:00.000Z"
    r = c.dispatch(signed("task.create",
                          {"workspace": "w", "from": sender, "kind": "x", "input": {}, "assignee": sender},
                          "2026-03-01T00:00:00.000Z"))  # backdated before revocation
    assert "error" in r and r["error"]["code"] == -32072  # SIG_KEY_REVOKED


def test_non_revoked_key_verifies_at_historical_ts():
    c, signed, key, sender = _setup()  # not revoked
    r = c.dispatch(signed("task.create",
                          {"workspace": "w", "from": sender, "kind": "x", "input": {}, "assignee": sender},
                          "2026-03-01T00:00:00.000Z"))
    assert "result" in r


def test_unverifiable_signature_is_rejected_not_skipped():
    # Fail-closed: a present-but-unverifiable signature (unknown workspace)
    # must be rejected under require_signatures, never silently accepted.
    c = Coordinator(CoordinatorOptions(require_signatures=True))
    r = c.dispatch({"jsonrpc": "2.0", "id": "1", "method": "task.create",
                    "params": {"workspace": "nope", "from": "human:mallory",
                               "kind": "x", "input": {}, "assignee": "human:mallory"},
                    "sig": "ed25519:garbage:xxx"})
    assert "error" in r and r["error"]["code"] == -32070  # SIG_VERIFY_FAILED
