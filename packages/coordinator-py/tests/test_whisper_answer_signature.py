"""
Regression (#154): whisper.answer must record the envelope as received. It used
to write the whisper's task_id into params after the signature was verified, so
the recorded copy no longer verified under its own signature.
"""
from __future__ import annotations

import base64
import copy

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

from chap_coordinator import Coordinator, CoordinatorOptions, canonicalize


def _b64url_nopad(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _sign(sk, envelope: dict, kid: str) -> dict:
    stripped = copy.deepcopy(envelope)
    stripped.pop("sig", None)
    sig = sk.sign(canonicalize(stripped))
    envelope["sig"] = f"ed25519:{kid}:{base64.b64encode(sig).decode('ascii')}"
    return envelope


def _verifies(pub: bytes, envelope: dict) -> bool:
    stripped = copy.deepcopy(envelope)
    sig = stripped.pop("sig")
    sig_bytes = base64.b64decode(sig.split(":", 2)[2])
    try:
        Ed25519PublicKey.from_public_bytes(pub).verify(sig_bytes, canonicalize(stripped))
        return True
    except Exception:
        return False


def test_recorded_whisper_answer_verifies_under_its_own_signature():
    c = Coordinator(CoordinatorOptions(
        require_signatures=True, deterministic_ids=True, deterministic_clock=True,
        default_profiles=["core/1.0", "whisper/1.0", "audit-scitt/1.0"]))

    sk_a = Ed25519PrivateKey.generate()
    sk_b = Ed25519PrivateKey.generate()
    pub_a = sk_a.public_key().public_bytes_raw()
    jwk_a = {"kty": "OKP", "crv": "Ed25519", "kid": "k-a", "x": _b64url_nopad(pub_a)}
    jwk_b = {"kty": "OKP", "crv": "Ed25519", "kid": "k-b",
             "x": _b64url_nopad(sk_b.public_key().public_bytes_raw())}

    c.dispatch({"jsonrpc": "2.0", "id": "1", "method": "workspace.create",
                "params": {"workspace": "w",
                           "profiles": ["core/1.0", "whisper/1.0", "audit-scitt/1.0"]}})
    c.dispatch({"jsonrpc": "2.0", "id": "2", "method": "participant.join",
                "params": {"workspace": "w", "from": "human:a", "type": "human",
                           "role": "owner", "jwks": {"keys": [jwk_a]}}})
    c.dispatch({"jsonrpc": "2.0", "id": "3", "method": "participant.join",
                "params": {"workspace": "w", "from": "agent:b", "type": "agent",
                           "role": "drafter", "jwks": {"keys": [jwk_b]}}})

    tid = c.dispatch(_sign(sk_b, {"jsonrpc": "2.0", "id": "4", "method": "task.create",
        "params": {"workspace": "w", "from": "agent:b", "kind": "k", "input": {},
                   "assignee": "agent:b"}}, "k-b"))["result"]["task_id"]
    wid = c.dispatch(_sign(sk_b, {"jsonrpc": "2.0", "id": "5", "method": "whisper.ask",
        "params": {"workspace": "w", "from": "agent:b", "to": "human:a",
                   "task_id": tid, "question": "ok?", "deadline_ms": 60000,
                   "default_if_lapsed": "yes"}}, "k-b"))["result"]["whisper_id"]
    ans = _sign(sk_a, {"jsonrpc": "2.0", "id": "6", "method": "whisper.answer",
        "params": {"workspace": "w", "from": "human:a", "whisper_id": wid,
                   "answer": "yes"}}, "k-a")
    r = c.dispatch(ans)
    assert "result" in r, r

    recorded = next(e.envelope for e in c.get_workspace("w").audit
                    if e.envelope.get("method") == "whisper.answer")
    assert "task_id" not in recorded["params"], "envelope was mutated after signing"
    assert _verifies(pub_a, recorded), "recorded whisper.answer does not verify"


def test_answer_still_findable_by_task_filter():
    c = Coordinator(CoordinatorOptions(
        default_profiles=["core/1.0", "whisper/1.0", "audit-scitt/1.0"]))

    def s(m, **p):
        return c.dispatch({"jsonrpc": "2.0", "id": m, "method": m, "params": p})

    s("workspace.create", workspace="w", profiles=["core/1.0", "whisper/1.0", "audit-scitt/1.0"])
    s("participant.join", workspace="w", **{"from": "agent:b"}, type="agent")
    s("participant.join", workspace="w", **{"from": "human:a"}, type="human")
    tid = s("task.create", workspace="w", **{"from": "agent:b"}, kind="k",
            input={}, assignee="agent:b")["result"]["task_id"]
    wid = s("whisper.ask", workspace="w", **{"from": "agent:b"}, to="human:a",
            task_id=tid, question="ok?", deadline_ms=60000, default_if_lapsed="yes")["result"]["whisper_id"]
    s("whisper.answer", workspace="w", **{"from": "human:a"}, whisper_id=wid, answer="yes")

    entries = s("audit.read", workspace="w", **{"from": "human:a"},
                filter={"task_id": tid})["result"]["entries"]
    methods = [e["envelope"]["method"] for e in entries]
    assert "whisper.answer" in methods
