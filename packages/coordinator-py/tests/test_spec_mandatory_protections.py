"""
SPECIFICATION 15.1 is checked against the coordinator, the way 8.1 is.

The section listed eight flat obligations on the Coordinator, and three of them
were not that: signature verification and step-up are turned on by a profile,
TLS and delivery filtering belong to the deployment, and a scope check is
declared per method and enforced by neither reference. A requirement the
references do not meet is worse than none, because the references are what
conformance is measured against.

This holds each remaining unconditional claim to the code, and holds the
descriptor schema to what the coordinator sends. The mirror is
packages/coordinator/tests/spec_mandatory_protections.test.ts.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from chap_coordinator import Coordinator, CoordinatorOptions

ROOT = Path(__file__).resolve().parents[3]
SPEC = (ROOT / "SPECIFICATION.md").read_text(encoding="utf-8")
WORKSPACE_SCHEMA = json.loads(
    (ROOT / "schemas/core/chap-workspace.schema.json").read_text(encoding="utf-8"))

PROFILES = ["core/1.0", "review/1.0", "modes/1.0", "control/1.0"]


def _ready(**options):
    coord = Coordinator(CoordinatorOptions(
        deterministic_ids=True, deterministic_clock=True,
        default_profiles=PROFILES, **options))

    def send(method, sender="human:a", **params):
        return coord.dispatch({"jsonrpc": "2.0", "id": method, "method": method,
                               "params": {"workspace": "w", "from": sender, **params}})

    send("workspace.create", profiles=PROFILES)
    send("participant.join", sender="human:a", type="human", role="admin")
    send("participant.join", sender="agent:b", type="agent", role="drafter")
    return coord, send


def _section(heading: str, until: str) -> str:
    """The section with its line wrapping collapsed, so a phrase can be sought."""
    return " ".join(SPEC[SPEC.index(heading):SPEC.index(until)].split())


# ------------------------------------------------ the section says what it says

def test_the_section_separates_who_has_to_do_the_work():
    # A flat list of MUSTs on the Coordinator is what let three requirements
    # sit there unmet. The grouping is the fix, so it is held in place.
    body = _section("### 15.1 Mandatory protections", "### 15.2")
    assert "A conformant Coordinator MUST" in body
    assert "A profile turns these on" in body
    assert "The deployment MUST" in body


def test_the_section_claims_no_scope_enforcement():
    # Declared per method in the catalogue, enforced by neither reference.
    body = _section("### 15.1 Mandatory protections", "### 15.2")
    assert "not yet enforced by either reference" in body


# --------------------------------------------- the unconditional ones are true

def test_the_chain_is_ordered_by_acceptance_not_by_the_sender_clock():
    coord, send = _ready(enable_chain=True)
    for backwards in ("2030-01-01T00:00:00.000Z", "2020-01-01T00:00:00.000Z"):
        send("task.create", kind="k", input={}, assignee="agent:b", ts=backwards)
    audit = coord.get_workspace("w").audit
    assert [e.seq for e in audit] == sorted(e.seq for e in audit)
    assert all(a.arrived <= b.arrived for a, b in zip(audit, audit[1:]))


def test_every_accepted_operation_is_recorded_and_the_reads_are_not():
    coord, send = _ready()
    ws = coord.get_workspace("w")
    before = len(ws.audit)
    send("task.create", kind="k", input={}, assignee="agent:b")
    assert len(ws.audit) == before + 1
    for read in ("workspace.describe", "audit.read"):
        send(read)
    assert len(ws.audit) == before + 1


def test_the_mode_ceiling_is_enforced_and_the_change_is_recorded():
    coord, send = _ready()
    ws = coord.get_workspace("w")
    before = len(ws.audit)
    assert "error" not in send("control.set_mode_ceiling", new_ceiling="trial")
    assert len(ws.audit) == before + 1, "a mode change is a first-class entry"

    refused = send("task.create", kind="k", input={}, assignee="agent:b", mode="production")
    assert refused["error"]["code"] == -32040


def test_a_role_check_the_method_defines_is_enforced():
    coord, send = _ready()
    assert "error" not in send("workspace.set_profiles", profiles=PROFILES)
    refused = send("workspace.set_profiles", sender="agent:b", profiles=PROFILES)
    assert "error" in refused and "admin" in refused["error"]["message"]


def test_ids_are_random_outside_test_mode():
    live = Coordinator(CoordinatorOptions())
    minted = {live.ids.task_id() for _ in range(64)}
    assert len(minted) == 64
    assert all(re.fullmatch(r"tsk_[0-9A-HJKMNP-TV-Z]{26}", t) for t in minted)


# ------------------------------------------- the conditional ones are optional

def test_signature_verification_is_what_the_profile_turns_on():
    _, send = _ready()
    assert "error" not in send("task.create", kind="k", input={}, assignee="agent:b"), \
        "an unsigned envelope is accepted where the profile is not in force"

    signed = Coordinator(CoordinatorOptions(require_signatures=True))
    signed.dispatch({"jsonrpc": "2.0", "id": "c", "method": "workspace.create",
                     "params": {"workspace": "w", "profiles": ["core/1.0"]}})
    # 6.5: enforcement on adds the profile, so the descriptor cannot understate.
    assert "security-signed/1.0" in signed.get_workspace("w").profiles


# ------------------------------------------ the descriptor matches its schema

def test_the_descriptor_carries_what_its_schema_requires():
    for chained in (False, True):
        coord, send = _ready(enable_chain=chained)
        descriptor = send("workspace.describe")["result"]
        for field in WORKSPACE_SCHEMA["required"]:
            assert field in descriptor, f"{field} is required and not sent (chain={chained})"
        for field in descriptor:
            assert field in WORKSPACE_SCHEMA["properties"], \
                f"{field} is sent and not declared (chain={chained})"
    # The head exists only where there is a chain, which is why it is optional.
    assert "evidence_head" not in WORKSPACE_SCHEMA["required"]
