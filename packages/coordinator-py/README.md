# chap-coordinator (Python)

Python reference implementation of the Collaborative Human-Agent Protocol (CHAP).

Covers Core plus every profile, 39 method handlers in total. Spec-aligned
field names, error codes, and response shapes against `profiles/*.md`.

## Install

```bash
pip install chap-coordinator
```

For the `security-signed/1.0` profile (Ed25519 signing) and the OIDC
binding hook:

```bash
pip install "chap-coordinator[crypto]"
```

For the MCP server transport (drive a Coordinator from any MCP client):

```bash
pip install "chap-coordinator[mcp]"
```

For the A2A server transport (expose a Coordinator as an A2A agent):

```bash
pip install "chap-coordinator[a2a]"
```

## Companion modules

The base package is the protocol core. Two transport adapters ship in
the same wheel under optional extras:

- `chap_coordinator.transports.mcp_server` wraps a Coordinator as an
  MCP server. Every CHAP method becomes an MCP tool named
  `chap.<method>`. Reference stdio server at
  `reference/mcp-server-py/`. Spec target: MCP 2025-11-25.
- `chap_coordinator.transports.a2a_server` wraps a Coordinator as an
  A2A agent. Every CHAP method becomes an `AgentSkill` on the
  Agent Card. Reference FastAPI server at `reference/a2a-server-py/`.
  Spec target: A2A 1.0 (with v0.3 compatibility).

Inward citation helpers (`wrap_mcp_tool_call`,
`wrap_a2a_message_exchange`, `content_hash`) live in
`chap_coordinator.transports.wrap`. They take a completed external
event and emit the matching CHAP audit entries with input/output
hashes.

## Quick start

```python
from chap_coordinator import Coordinator, CoordinatorOptions

coord = Coordinator(CoordinatorOptions(default_profiles=[
    "core/1.0", "review/1.0", "whisper/1.0",
]))

coord.dispatch({"jsonrpc": "2.0", "id": "1",
    "method": "workspace.create",
    "params": {"workspace": "wsp_demo"}})

coord.dispatch({"jsonrpc": "2.0", "id": "2",
    "method": "participant.join",
    "params": {"workspace": "wsp_demo",
               "from": "human:me@local",
               "type": "human", "role": "reviewer"}})

coord.dispatch({"jsonrpc": "2.0", "id": "3",
    "method": "participant.join",
    "params": {"workspace": "wsp_demo",
               "from": "agent:bot",
               "type": "agent", "role": "drafter"}})

resp = coord.dispatch({"jsonrpc": "2.0", "id": "4",
    "method": "task.create",
    "params": {"workspace": "wsp_demo",
               "from": "human:me@local",
               "kind": "draft_response",
               "input": {"ticket_id": "INC-1"},
               "assignee": "agent:bot"}})

print(resp["result"])  # {"task_id": "tsk_...", "state": "created"}
```

## What is implemented

This package implements **CHAP Core plus every profile**:

| Profile               | Methods (per profile spec)                                                  |
|-----------------------|------------------------------------------------------------------------------|
| `core/1.0`            | `workspace.create`, `workspace.describe`, `workspace.set_profiles`, `participant.join`, `participant.leave`, `task.create`, `task.update`, `task.complete`, `audit.read` |
| `review/1.0`          | `review.request`, `decide.approve`, `decide.reject`, `decide.override`, `abstain.declare`, `escalate.raise` |
| `whisper/1.0`         | `whisper.ask`, `whisper.answer`; lapse hook via `coord.check_whisper_lapses(workspace_id, now)` |
| `deliberation/1.0`    | `deliberate.open`, `deliberate.comment`, `deliberate.vote`, `deliberate.close` |
| `handoff/1.0`         | `handoff.propose`, `handoff.accept`, `handoff.decline`; carries multiple tasks; recipient may be a URI or `group:` |
| `control/1.0`         | `control.pause`, `control.resume`, `control.cancel`, `control.snapshot`, `control.rollback`, `control.supersede`, `control.set_mode_ceiling`; pause/resume take `scope: task/participant/workspace` |
| `routing/1.0`         | `task.route`, `review.depth`, `escalate.auto`; each emits a `route_decision` artefact |
| `modes/1.0`           | Mode handling at `task.create`; `trial` mode forces `review_required`; `control.set_mode_ceiling` shared with control/1.0 |
| `security-signed/1.0` | `participant.rotate_key`, `participant.revoke_key`; top-level `sig` field verified at dispatch when `options.require_signatures=True` |
| `audit-scitt/1.0`     | `audit.submit_to_scitt`, `audit.verify_receipt`, `audit.verify_chain`; SCITT statements assembled and passed to a deployment-supplied submitter; local prev-hash chain linkage retained |
| `identity-oidc/1.0`   | `participant.join` binding via `options.verify_oidc_token`; `cnf.jwk` pinning, step-up auth via `enforce_step_up=True` |
| `identity-vc/1.0`     | `participant.join` binding via `options.verify_vc`; holder-key pinning |

**39 method handlers in total.**

## Architecture

The coordinator is **transport-agnostic, persistence-agnostic, and
UI-agnostic**. It exposes a single entry point:

```python
response = coord.dispatch(envelope)
```

Where `envelope` is a JSON-RPC 2.0 request and `response` is the
JSON-RPC 2.0 response. Bind whichever HTTP, WebSocket, or in-process
transport you like. State is held in memory; persist by subscribing
to the audit listener.

A minimal stdlib HTTP server is at
[`reference/python/server.py`](https://github.com/BrightbeamAI/chap/tree/main/reference/python) in the parent repo.

## Determinism for tests

Set `deterministic_ids=True` and `deterministic_clock=True` on
`CoordinatorOptions` for replayable demos and golden-file tests:

```python
coord = Coordinator(CoordinatorOptions(
    deterministic_ids=True,
    deterministic_clock=True,
))
```

## Identity binding (identity-oidc/1.0, identity-vc/1.0)

These profiles bind a participant's claimed identity to a verifiable
external authority. The coordinator does not implement OIDC or VC
verification itself; you supply callbacks:

```python
def verify_oidc(token: str) -> dict | None:
    # Validate the token against your IdP; return claims dict or None
    # Include "cnf": {"jwk": {...}} to bind a signing key
    ...

def verify_vc(presentation: dict) -> dict | None:
    # Verify the VP; return subject claims dict or None
    # Include "cnf_jwk": {...} to bind a holder key
    ...

coord = Coordinator(CoordinatorOptions(
    verify_oidc_token=verify_oidc,
    verify_vc=verify_vc,
    enforce_step_up=True,  # require fresh auth_time for privileged methods
))
```

If a `participant.join` envelope includes `oidc_token` or
`vc_presentation` parameters, the relevant hook is called and the bound
key is added to the participant's key history.

## Signed envelopes (security-signed/1.0)

To require signatures on every envelope:

```python
coord = Coordinator(CoordinatorOptions(require_signatures=True))
```

Each participant registers one or more JWKs at `participant.join` via
`jwks: {keys: [...]}`. Senders sign the JCS canonicalisation of the
envelope **with the top-level `sig` field removed**, then attach the
signature as a top-level `sig: "ed25519:<kid>:<base64>"` field. The
Coordinator looks up the verifying key by `(from, kid, ts)`, so
historical envelopes verify across key rotation.

Key lifecycle:

- `participant.rotate_key` is signed with the old key; the Coordinator
  marks `valid_until` on the old key and `valid_from` on the new one.
- `participant.revoke_key` is an admin operation; subsequent envelopes
  signed with the revoked key are rejected.

## Routing policies (routing/1.0)

A default policy ships out of the box. Override it with operator hooks:

```python
def route(task, candidates):
    # Return {"selected": "...", "rationale": {...}}
    return {"selected": candidates[0], "rationale": {
        "policy_id": "round-robin",
        "summary": "first eligible",
    }}

coord = Coordinator(CoordinatorOptions(routing_policy=route))
```

Every call to `task.route`, `review.depth`, and `escalate.auto`
emits a `route_decision` artefact recording the inputs, the policy
id, and the rationale. `task.route` also updates the task's
`assignee` to match the selected URI.

## Audit and SCITT submission (audit-scitt/1.0)

Audit entries are hash-linked when the profile is active.
`audit.verify_chain` replays the chain locally and confirms
`prev_hash` continuity.

`audit.submit_to_scitt` builds a SCITT-shaped signed statement for
each audit entry in the requested range and passes it to a
deployment-supplied submitter:

```python
def submitter(statement):
    # Submit to your SCITT transparency service; return the receipt
    ...

coord = Coordinator(CoordinatorOptions(scitt_submitter=submitter))
```

Without a submitter configured, `audit.submit_to_scitt` returns the
statements so the caller can submit out-of-band.

## Testing

```bash
pip install -e ".[dev]"
pytest
```

90 tests as of this release: 63 core library, 7 MCP integration,
10 A2A integration, 10 wrap-helper.

## Spec fidelity

This implementation was reviewed against every profile spec under
`profiles/` and aligned with the documented field names, error codes,
and response shapes. The full audit notes are in the parent repo's
[`CHANGELOG.md`](https://github.com/BrightbeamAI/chap/blob/main/CHANGELOG.md), with the 0.2.1 entry covering
the original Python implementation and 0.2.3 / 0.2.4 covering the
MCP and A2A transport additions.

## License

Apache 2.0. See the parent repository's [LICENSE](./LICENSE).

## Specification

Tracks the spec at
[github.com/BrightbeamAI/chap/SPECIFICATION.md](https://github.com/BrightbeamAI/chap/blob/main/SPECIFICATION.md).
