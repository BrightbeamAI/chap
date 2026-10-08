"""Conformance test vectors from conformance/test-vectors.md.

These verify the cryptographic foundations: Ed25519 signing,
JCS canonicalisation, and chain linkage. An implementation passing
these is conformant on the crypto core.
"""
from __future__ import annotations

import pytest

from chap_coordinator import canonicalize, sha256_hex


def test_jcs_keys_sorted():
    """JCS canonical form has lexicographically sorted keys at every level."""
    envelope = {
        "chap": "0.2",
        "id": "01HZ9YWQ7K3X8M2V4N6P8R0T2A",
        "ts": "2026-05-17T09:00:00.000Z",
        "workspace": "wsp_test",
        "from": "human:alice@example.org",
        "to": "service:coordinator@example.org",
        "type": "notification",
        "method": "participant.heartbeat",
        "params": {"load": "0.42", "status": "ready"},
        "evidence": {"prev_hash": "sha256:" + "0" * 64},
    }
    canon = canonicalize(envelope)
    # First key alphabetically is "chap"
    assert canon.startswith(b'{"chap":')
    # No whitespace
    assert b" " not in canon
    # params keys sorted: load before status
    assert b'"params":{"load":' in canon


def test_jcs_no_whitespace():
    assert canonicalize({"a": 1, "b": 2}) == b'{"a":1,"b":2}'


def test_jcs_bool_handled_before_int():
    """bool is a subclass of int in Python; must be checked first."""
    assert canonicalize({"x": True}) == b'{"x":true}'
    assert canonicalize({"x": False}) == b'{"x":false}'
    assert canonicalize({"x": 1}) == b'{"x":1}'


def test_jcs_null():
    assert canonicalize({"x": None}) == b'{"x":null}'


def test_jcs_integer_floats():
    """Floats that are integers should serialise as integers."""
    assert canonicalize({"x": 1.0}) == b'{"x":1}'


def test_jcs_rejects_nonfinite():
    with pytest.raises(ValueError):
        canonicalize({"x": float("nan")})
    with pytest.raises(ValueError):
        canonicalize({"x": float("inf")})


CHAIN_RECORDS = [
    '{"id":"req-1","jsonrpc":"2.0","method":"workspace.create","params":{"from":"human:alice@example.org","profiles":["core/1.0","audit-scitt/1.0"],"to":"service:coordinator@example.org","ts":"2026-05-17T09:00:00.000Z","workspace":"wsp_test"}}',
    '{"id":"req-2","jsonrpc":"2.0","method":"participant.join","params":{"from":"human:alice@example.org","to":"service:coordinator@example.org","ts":"2026-05-17T09:00:01.000Z","type":"human","workspace":"wsp_test"}}',
    '{"id":"req-3","jsonrpc":"2.0","method":"participant.join","params":{"from":"agent:triage-bot","to":"service:coordinator@example.org","ts":"2026-05-17T09:00:02.000Z","type":"agent","workspace":"wsp_test"}}',
]
CHAIN_HEADS = [
    "sha256:110153f2ff7df935174bab8ddfe48a303920bec27deca0e55647cdacf3c0ea97",
    "sha256:77e0e38d2006c83645a1c8cb12abb401c4e109320fcf7b94a94753113e0cb19d",
    "sha256:5d9e95b79b484be957f462d23ea61febcfa124146f3acae2e194878c0a750699",
]


def test_chain_heads_from_test_vectors():
    """The three-entry chain in test-vectors.md S3, computed and recorded."""
    import json

    from chap_coordinator import Coordinator, CoordinatorOptions

    prev = "sha256:" + "0" * 64
    for record, expected in zip(CHAIN_RECORDS, CHAIN_HEADS):
        assert canonicalize(json.loads(record)).decode("utf-8") == record
        prev = sha256_hex((record + prev).encode("utf-8"))
        assert prev == expected

    coord = Coordinator(CoordinatorOptions())
    for record in CHAIN_RECORDS:
        assert "error" not in coord.dispatch(json.loads(record))
    described = coord.dispatch({"jsonrpc": "2.0", "id": "d", "method": "workspace.describe",
                                "params": {"workspace": "wsp_test"}})
    assert described["result"]["evidence_head"] == CHAIN_HEADS[-1]


def test_ed25519_rfc8032_vector1():
    """RFC 8032 test vector 1: Ed25519 signing with the canonical seed.

    The expected signature is the one test-vectors.md S1 publishes.
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError:
        pytest.skip("cryptography not installed")

    seed = bytes.fromhex(
        "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"
    )
    expected_pub = "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"

    sk = Ed25519PrivateKey.from_private_bytes(seed)
    assert sk.public_key().public_bytes_raw().hex() == expected_pub
    sig = sk.sign(b"")
    assert sig.hex() == (
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555"
        "fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
    )
    sk.public_key().verify(sig, b"")  # raises if invalid
