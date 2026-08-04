"""
Regression: step-up must fail closed for OIDC actors. A human (or any member with
an OIDC binding) invoking a privileged method under enforce_step_up must present a
fresh auth_time and, when the workspace sets min_acr, a matching acr. Agents and
services without an OIDC binding authenticate out of band and stay exempt.
"""
from __future__ import annotations

import time

from chap_coordinator import Coordinator, CoordinatorOptions


def _privileged(c, sender):
    return c.dispatch({"jsonrpc": "2.0", "id": "p",
                       "method": "workspace.set_profiles",
                       "params": {"workspace": "w", "from": sender,
                                  "profiles": ["core/1.0", "review/1.0"]}})


def test_step_up_denies_human_without_fresh_auth():
    c = Coordinator(CoordinatorOptions(enforce_step_up=True))
    c.dispatch({"jsonrpc": "2.0", "id": "1", "method": "workspace.create",
                "params": {"workspace": "w"}})
    c.dispatch({"jsonrpc": "2.0", "id": "2", "method": "participant.join",
                "params": {"workspace": "w", "from": "human:alice", "type": "human"}})
    r = _privileged(c, "human:alice")
    assert r["error"]["code"] == -32402


def test_step_up_exempts_agent_without_oidc():
    c = Coordinator(CoordinatorOptions(enforce_step_up=True))
    c.dispatch({"jsonrpc": "2.0", "id": "1", "method": "workspace.create",
                "params": {"workspace": "w"}})
    c.dispatch({"jsonrpc": "2.0", "id": "2", "method": "participant.join",
                "params": {"workspace": "w", "from": "agent:bot", "type": "agent"}})
    r = _privileged(c, "agent:bot")
    assert "error" not in r or r["error"]["code"] != -32402


def test_step_up_enforces_min_acr():
    fresh = int(time.time())

    def verify(token):
        return {"sub": "u", "auth_time": fresh, "acr": token}

    c = Coordinator(CoordinatorOptions(enforce_step_up=True, verify_oidc_token=verify))
    c.dispatch({"jsonrpc": "2.0", "id": "1", "method": "workspace.create",
                "params": {"workspace": "w", "min_acr": "mfa"}})
    c.dispatch({"jsonrpc": "2.0", "id": "2", "method": "participant.join",
                "params": {"workspace": "w", "from": "human:alice", "type": "human",
                           "oidc_token": "pwd"}})
    c.dispatch({"jsonrpc": "2.0", "id": "3", "method": "participant.join",
                "params": {"workspace": "w", "from": "human:bob", "type": "human",
                           "oidc_token": "mfa"}})

    below = _privileged(c, "human:alice")
    assert below["error"]["code"] == -32402

    ok = _privileged(c, "human:bob")
    assert "error" not in ok or ok["error"]["code"] != -32402
