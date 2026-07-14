# chap-google-adk

Adapter between [Google ADK](https://google.github.io/adk-docs/) and the
CHAP Coordinator. When an ADK run pauses for tool confirmation, the human's
decision -- approve, edit, or reject -- becomes a hash-linked, replayable
CHAP audit entry.

```
decision      CHAP envelope
--------      ---------------------------
approve       decide.approve
override      decide.override   (original args vs the edited value)
reject        decide.reject
```

The paused tool call (name + args) is the artefact under review; the
`ToolConfirmation` the human returns is the decision on it. An edit is
recorded as an RFC 6902 diff, so the chain captures *what changed and why*,
not just *approved/denied*.

## Install

```bash
pip install chap-google-adk
```

Depends on `chap-coordinator>=0.2.7`. Google ADK is **optional**: the adapter
reads the call and confirmation structurally, so the bridge and its tests
work without it installed. Install the extra to run a live agent:

```bash
pip install "chap-google-adk[google-adk]"
```

## Quick start

ADK gates a tool with `FunctionTool(fn, require_confirmation=True)` (or a
tool calling `tool_context.request_confirmation(...)`). The run pauses and
surfaces the paused call plus a `ToolConfirmation`; you resume by returning
a `ToolConfirmation`. Record the decision at that resolution point:

```python
from google.adk.tools.tool_confirmation import ToolConfirmation
from chap_coordinator import Coordinator
from chap_google_adk import ChapConfirmationBridge

bridge = ChapConfirmationBridge(
    Coordinator(),
    workspace="wsp_payments",
    agent="agent:assistant#v1",
    reviewer="human:alice@example.org",
)

# `tool_call` is the paused call (its .name / .args); `confirmation` is the
# ToolConfirmation the human returned.
bridge.record_decision(tool_call, confirmation)                    # confirmed -> approve
bridge.record_decision(tool_call, confirmation, decision="reject")  # or not confirmed
bridge.record_decision(                                             # an edit
    tool_call, confirmation,
    decision="override", returned={"amount": 50, "to": "acct-9"},
    approver="human:sam@example.org", rationale="over the desk limit",
)
```

## approve/reject derived, override explicit

`confirmation.confirmed` is a clean approve/reject signal, so those two are
derived from it (an explicit `decision` still wins). An **override is never
inferred**: ADK's `payload` is a separate, tool-defined object (a leave tool
asks for `approved_days`, not a modified `days`), so treating it as the
edited args would record a change the human never made. An edit is recorded
only when you pass it explicitly: the edited args as `returned`, or a
ready-made RFC 6902 `diff`. `intent_preserved` defaults to `true` on an
override; set it `false` for a substituting edit. A no-op edit records
`decide.approve`.

## Approver identity

CHAP has no ambient actor: the decider is whatever `from` the envelope
carries. The bridge uses its `reviewer` by default; pass a per-decision
`approver` (a `human:` URI) to override it. The participant type is taken
from the URI scheme and the approver is joined before recording. Each
decision is its own task whose review is addressed to that approver, so the
record satisfies the Coordinator's authorisation rules.

## What you get in the audit chain

One confirmed-with-an-edit call yields:

```
seq=3  task.create     agent:assistant#v1
seq=4  task.complete   agent:assistant#v1
seq=5  review.request  agent:assistant#v1   to=human:sam@example.org
seq=6  decide.override human:sam@example.org  diff=[{op:replace, path:/args/amount, value:50}]
```

Every entry carries `prev_hash`, so the chain verifies externally or anchors
to a SCITT transparency service with the `audit-scitt/1.0` profile.

## Example

`examples/01-approve-edit-reject.py` drives one confirmation-gated tool
through approve, a refining edit, a substituting edit, and a reject against
real `google-adk` (offline, no API key), and prints the resulting chain.

## Compatibility

- `chap-coordinator` 0.2.7
- `google-adk` >=1.29 (optional; tool-confirmation API present since 1.29, example verified on 2.3)
- Python 3.10, 3.11, 3.12, 3.13

## License

Apache 2.0. See [LICENSE](./LICENSE).
