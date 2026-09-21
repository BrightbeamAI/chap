"""
SPECIFICATION 15.4: a method whose owning profile the workspace does not
advertise is refused.

Before this, removing control/1.0 from a workspace advertised nothing and
changed nothing: the emergency brake stayed fully live. The refusal is -32601,
the answer a coordinator that never implemented the method would give, so a
deployment cannot tell from outside which of the two it is talking to.

The mirror of this file is packages/coordinator/tests/profile_gate.test.ts.
"""
from __future__ import annotations

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.catalogue import ALWAYS_AVAILABLE, OWNING_PROFILE

CORE = ["core/1.0", "review/1.0"]


def _ready(profiles=CORE, **options):
    coord = Coordinator(CoordinatorOptions(
        deterministic_ids=True, deterministic_clock=True,
        default_profiles=profiles, **options))

    def send(method, sender="human:a", **params):
        return coord.dispatch({"jsonrpc": "2.0", "id": method, "method": method,
                               "params": {"workspace": "w", "from": sender, **params}})

    send("workspace.create", profiles=profiles)
    for uri, kind in (("human:a", "human"), ("agent:b", "agent")):
        send("participant.join", sender=uri, type=kind)
    return coord, send


# ------------------------------------------------------------------ the gate

@pytest.mark.parametrize("method,owner", [
    ("control.pause", "control/1.0"),
    ("whisper.ask", "whisper/1.0"),
    ("deliberate.open", "deliberation/1.0"),
    ("handoff.propose", "handoff/1.0"),
    ("task.route", "routing/1.0"),
    ("audit.submit_to_scitt", "audit-scitt/1.0"),
])
def test_an_unadvertised_method_is_refused(method, owner):
    _, send = _ready()
    refused = send(method, task_id="t")

    assert refused["error"]["code"] == -32601
    assert refused["error"]["message"] == f"Unknown method: {method}"
    assert refused["error"]["data"] == {"profile": owner, "advertised": CORE}


def test_the_same_method_works_once_the_profile_is_advertised():
    coord, send = _ready([*CORE, "control/1.0"])
    tid = send("task.create", kind="k", input={}, assignee="agent:b")["result"]["task_id"]
    assert "error" not in send("control.pause", task_id=tid, reason="hold")
    assert coord.get_workspace("w").tasks[tid].state == "paused"


def test_a_refusal_writes_nothing_to_the_chain():
    coord, send = _ready()
    ws = coord.get_workspace("w")
    before = len(ws.audit)
    send("control.pause", task_id="t", reason="hold")
    assert len(ws.audit) == before


def test_a_later_minor_version_of_the_owning_profile_still_carries_its_methods():
    # A workspace on control/1.1 has the control methods. Gating on the exact
    # version string would refuse them and make every minor release a break.
    coord, send = _ready([*CORE, "control/1.1"])
    tid = send("task.create", kind="k", input={}, assignee="agent:b")["result"]["task_id"]
    assert "error" not in send("control.pause", task_id=tid, reason="hold")


# -------------------------------------------------------------- the carve-outs

@pytest.mark.parametrize("method", sorted(ALWAYS_AVAILABLE))
def test_a_read_is_never_gated(method):
    # A workspace must be able to ask what it is and to check its own chain.
    # audit.verify_chain belongs to audit-scitt/1.0 while chaining also turns
    # on through an option, so gating it would let a workspace write a chain it
    # is refused permission to verify.
    coord, send = _ready(enable_chain=True)
    result = send(method, **({"receipt": {}} if method == "audit.verify_receipt" else {}))
    error = result.get("error", {})
    assert error.get("code") != -32601, f"{method} was gated"


@pytest.mark.parametrize("method", ["participant.rotate_key", "participant.revoke_key"])
def test_the_key_lifecycle_is_never_gated(method):
    # The shipped MCP server advertises nine profiles and security-signed/1.0
    # is not among them. Gating these would remove an operator's response to a
    # compromised key from every default deployment, so the catalogue
    # attributes them to core/1.0.
    assert OWNING_PROFILE[method] == "core/1.0"
    _, send = _ready()
    refused = send(method, target_uri="human:a", kid="k-1")
    assert refused.get("error", {}).get("code") != -32601


def test_every_implemented_method_has_an_owner():
    # A method the catalogue does not name would pass the gate unexamined.
    coord, _ = _ready()
    for method in coord._handlers:
        assert method in OWNING_PROFILE, f"{method} is dispatchable and not in the catalogue"


# ------------------------------------- advertised against enforced, at create

def test_signing_on_adds_the_profile_it_enforces():
    coord = Coordinator(CoordinatorOptions(require_signatures=True))
    coord.dispatch({"jsonrpc": "2.0", "id": "c", "method": "workspace.create",
                    "params": {"workspace": "w", "profiles": ["core/1.0"]}})
    assert "security-signed/1.0" in coord.get_workspace("w").profiles


def test_advertising_a_security_profile_without_enforcing_it_is_refused():
    coord = Coordinator(CoordinatorOptions())
    refused = coord.dispatch({"jsonrpc": "2.0", "id": "c", "method": "workspace.create",
                              "params": {"workspace": "w",
                                         "profiles": ["core/1.0", "security-signed/1.0"]}})
    assert refused["error"]["code"] == -32602
    assert "require_signatures" in refused["error"]["message"]
    assert "w" not in coord.workspaces


def test_the_same_rule_holds_for_identity_oidc():
    enforcing = Coordinator(CoordinatorOptions(verify_oidc_token=lambda token: {}))
    enforcing.dispatch({"jsonrpc": "2.0", "id": "c", "method": "workspace.create",
                        "params": {"workspace": "w", "profiles": ["core/1.0"]}})
    assert "identity-oidc/1.0" in enforcing.get_workspace("w").profiles

    bare = Coordinator(CoordinatorOptions())
    refused = bare.dispatch({"jsonrpc": "2.0", "id": "c", "method": "workspace.create",
                             "params": {"workspace": "w",
                                        "profiles": ["core/1.0", "identity-oidc/1.0"]}})
    assert refused["error"]["code"] == -32602
