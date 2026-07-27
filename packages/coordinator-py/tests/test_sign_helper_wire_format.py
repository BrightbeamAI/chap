"""
Regression: the public ``crypto.sign`` helper must emit the ``ed25519:<kid>:<b64>``
wire tag the coordinator verifies. A kid-less two-part tag is rejected as
malformed (-32070), so the helper's output has to round-trip through dispatch.
"""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.canonical import canonicalize
from chap_coordinator import crypto


def test_sign_helper_output_is_accepted_on_the_wire():
    c = Coordinator(CoordinatorOptions(require_signatures=True))
    sender = "human:alice"
    priv = crypto.derive_private_key(sender)
    jwk = crypto.public_jwk(sender)
    kid = jwk["kid"]
    c.dispatch({"jsonrpc": "2.0", "id": "j", "method": "participant.join",
                "params": {"workspace": "w", "from": sender, "type": "human",
                           "jwks": {"keys": [jwk]},
                           "profiles": ["core/1.0", "security-signed/1.0"]}})

    env = {"jsonrpc": "2.0", "id": "t", "method": "task.create",
           "params": {"workspace": "w", "from": sender, "kind": "x",
                      "input": {}, "assignee": sender}}
    env["sig"] = crypto.sign(canonicalize(env), priv, kid)

    assert env["sig"].count(":") == 2
    r = c.dispatch(env)
    assert "result" in r
