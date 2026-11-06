# chap-coordinator (Python)

Python reference implementation of the Collaborative Human-Agent Protocol (CHAP).

Covers Core plus every profile, 40 method handlers in total. Spec-aligned
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

The base package is the protocol core, with what runs it for people and
agents in more than one process, standard library only:

- `chap_coordinator.server`, the desk server: the review desk, `POST /chap`
  and a read API, from the one process that owns the store; and the same
  routes as an ASGI app for FastAPI or Starlette.
- `chap_coordinator.client`, the HTTP client the other processes use.
- `chap-coordinator`, the command that runs the desk server and the two
  transports below (`python -m chap_coordinator` is the same command).

Two transport adapters ship in the same wheel under optional extras:

- `chap_coordinator.transports.mcp_server` wraps a Coordinator as an
  MCP server. Every CHAP method becomes an MCP tool named
  `chap.<method>`. Reference stdio server at
  `reference/mcp-server-py/`. Spec target: MCP 2026-07-28, serving
  2025-11-25 clients as well.
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

## The server, the client and the command

A coordinator runs in one process, which owns its store, and the people
and agents it serves reach it from others. `chap_coordinator.server` is
that process: the review desk where a person decides, `POST /chap` for CHAP
calls as JSON-RPC, and a read API under `/api/`. A `chap.config.json`
describes it:

```json
{
  "workspace": "wsp_support",
  "profiles": ["core/1.0", "review/1.0"],
  "store": "./data/chap.db",
  "port": 8787,
  "humans": [{ "uri": "human:ana@example.org", "display_name": "Ana", "role": "reviewer" }],
  "agent": { "uri": "agent:drafter", "display_name": "The drafter" },
  "require_signatures": "humans",
  "chain": true
}
```

```bash
pip install "chap-coordinator[crypto]"
chap-coordinator serve --config chap.config.json
```

The desk is then at http://127.0.0.1:8787/.

- `store` is read against the config file's directory, wherever the server
  starts, and a leading `~` is the home directory; `":memory:"` keeps
  everything in the process.
- `require_signatures` is `true` (every call is signed), `"humans"` (a
  person's calls are signed, and the desk signs in the browser) or
  `false`. Signing needs the `crypto` extra.
- `chain` links each entry on the log to the one before it by its hash.
- PORT, CHAP_HOST, CHAP_DB_PATH, CHAP_REQUIRE_SIGNATURES, CHAP_CHAIN and
  CHAP_ALLOWED_HOSTS override the file.

The server answers only under its own host names, and a proxy's name goes
in `allowed_hosts`. It refuses a browser request from another origin,
takes JSON only on `POST /chap`, and its Content-Security-Policy lets no
other page frame the desk. The pages chap-analytics writes to `analytics/`
beside the config are served under `/analytics/`, in a sandbox that keeps
them out of the desk's origin. There is no login: a deployment reached from
other machines puts one in front.

Every other process reaches the server with `chap_coordinator.client`:

```python
from chap_coordinator.client import HttpCoordinator, Participant, Signer

desk = HttpCoordinator("http://127.0.0.1:8787/chap")
agent = Participant(desk, "wsp_support", "agent:drafter",
                    Signer.load_or_create("agent:drafter", "keys"))
agent.join("agent", "drafter")
task = agent.call("task.create", kind="draft_reply", assignee=agent.uri,
                  input={"ticket": "T-1"}, review_required=True)
agent.call("task.complete", task_id=task["task_id"], output={"body": "Hello"})
decided = agent.wait_for_decision(task["task_id"], timeout=3600)
print(decided["state"], decided["output"])
```

`call` raises `ChapError`, with the coordinator's code, when a call is
refused, and `ChapEndpointError` when no JSON-RPC answer comes at all. With
a `Signer`, every call the participant makes is signed with its own key,
which `load_or_create` keeps in `keys/`, readable by its owner alone, and
names by its RFC 7638 thumbprint; without one, its calls go unsigned.
`HttpCoordinator.dispatch(envelope)` answers as `Coordinator.dispatch`
does, and a `Participant` stands in for a coordinator as well, signing what
it sends, so a framework bridge runs against the server unchanged.

`chap-coordinator` has three commands, each with `--help`:

- `serve`: the desk server, from a chap.config.json (`--config`, `--host`,
  `--port`).
- `mcp`: an MCP server on stdio that acts as one agent, `--as
  agent:assistant@local` by default. With `--url
  http://127.0.0.1:8787/chap` it forwards every call to a running desk
  server, so the assistant's work waits for the person at that desk. Needs
  the `mcp` extra.
- `a2a`: an A2A server that acts as one agent. Needs the `a2a` extra.

## Running it for real

**The store.** `CoordinatorOptions(store=SqliteStore(path))` keeps every
workspace in a SQLite file: the coordinator loads it when it starts and
writes each call that adds to a workspace's log before the call returns.
Without a store the state lives in memory and goes when the process exits.
A call whose write fails is answered with `-32603` and
`data: {"reason": "store"}`, and the workspace goes back to what the store
holds; `store_errors="log"` keeps the earlier behaviour, where the call is
answered as it was and the next write carries its change. The desk server
opens the store `chap.config.json` names, from the config file's directory.

**Several processes, one store.** Each coordinator holds its workspaces in
memory. Before a call on a workspace it asks the store whether another
process has written since, and reloads if so; a write another process got
to first runs the call once more on what the store holds. Two processes
over one SQLite file, such as a server and a queue worker, therefore cannot
contradict each other's decisions. A store of your own that implements
`save` alone has no such check, and still needs one process
([SPECIFICATION.md §10.3](../../SPECIFICATION.md#103-writers)).

**FastAPI.** Mount the desk in your app after your own routes, and run one
worker: the desk's pages read the coordinator's memory, which a call
handled by another worker does not update until this worker's next call.

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI

from chap_coordinator.server import ChapDesk, load_config, make_coordinator

config = load_config("chap.config.json")
coord = make_coordinator(config)
desk = ChapDesk(config, coord)


@asynccontextmanager
async def lifespan(app):
    yield
    coord.options.store.close()


app = FastAPI(lifespan=lifespan)


@app.get("/healthz")
def healthz():
    with desk.lock:
        return {"tasks": len(coord.get_workspace(config["workspace"]).tasks)}


app.mount("/", desk)
```

```bash
uvicorn app:app --workers 1
```

The desk's pages name their files from `/`, so the desk is mounted at the
root; `POST /chap` and `/api/` also work below a prefix. An app that needs
more workers runs `chap-coordinator serve` beside them, and each worker
calls it through `HttpCoordinator`.

**A job queue.** Queue workers (Celery, RQ, arq) are processes of their
own, so each reaches the server over HTTP. One job opens the review and
ends; a later one reads the decision, so no worker waits on a person:

```python
from chap_coordinator.client import HttpCoordinator, Participant, Signer

desk = HttpCoordinator("http://127.0.0.1:8787/chap")
agent = Participant(desk, "wsp_support", "agent:drafter",
                    Signer.load_or_create("agent:drafter", "/srv/chap/keys"))


def write_draft(ticket):  # the agent's model call
    return {"to": ticket["customer"], "body": "Thank you for writing to us."}


def send(reply):  # what happens to an approved reply
    print("sending", reply)


def draft_reply(ticket):  # enqueued for each new ticket
    task = agent.call("task.create", kind="draft_reply", assignee=agent.uri, input=ticket,
                      review_required=True, idempotency_key=f"ticket-{ticket['id']}")
    if task["state"] in ("created", "in_progress"):
        agent.call("task.complete", task_id=task["task_id"], output=write_draft(ticket))
    return task["task_id"]


def send_when_decided(task_id):  # run on a schedule; None while the review is open
    view = agent.task(task_id)
    if view["state"] == "review_requested":
        return None
    if view["state"] == "completed":
        send(view["output"])
    return view["state"]
```

The agent joins once, with `agent.join("agent", "drafter")`, and every
worker loads the same key file. The idempotency key lets a retried job find
the task it opened before, and a job that finds its draft already submitted
leaves it. A rejection that asks for a revision leaves the task
`in_progress`, and running `draft_reply` again submits a new draft.

**Backups and chain heads.** With WAL, the SQLite store's default, copy
the file while the server runs with SQLite's online backup:
`sqlite3 data/chap.db ".backup backup.db"`, or
`sqlite3.Connection.backup`. The chain head stays in that file, where
whoever runs the server can rewrite it. To show later that an entry
existed, keep the head where they cannot change it: publish the
`chain_head` that `audit.verify_chain` returns, or hand the entries to a
transparency service with `audit.submit_to_scitt` and keep its receipts.

## What is implemented

This package implements **CHAP Core plus every profile**:

| Profile               | Methods (per profile spec)                                                  |
|-----------------------|------------------------------------------------------------------------------|
| `core/1.0`            | `workspace.create`, `workspace.describe`, `workspace.set_profiles`, `participant.join`, `participant.leave`, `task.create`, `task.update`, `task.complete`, `audit.read`; a task created with `review_required` completes only on a reviewer decision |
| `review/1.0`          | `review.request`, `review.acknowledge`, `decide.approve`, `decide.reject`, `decide.override`, `abstain.declare`, `escalate.raise` |
| `whisper/1.0`         | `whisper.ask`, `whisper.answer`; lapse hook via `coord.check_whisper_lapses(workspace_id, now)` |
| `deliberation/1.0`    | `deliberate.open`, `deliberate.comment`, `deliberate.vote`, `deliberate.close` |
| `handoff/1.0`         | `handoff.propose`, `handoff.accept`, `handoff.decline`; carries multiple tasks; recipient may be a URI or `group:` |
| `control/1.0`         | `control.pause`, `control.resume`, `control.cancel`, `control.snapshot`, `control.rollback`, `control.supersede`, `control.set_mode_ceiling`; pause/resume take `scope: task/participant/workspace` |
| `routing/1.0`         | `task.route`, `review.depth`, `escalate.auto`; each emits a `route_decision` artefact |
| `modes/1.0`           | Mode handling at `task.create`; `trial` mode forces `review_required`; `control.set_mode_ceiling` shared with control/1.0 |
| `security-signed/1.0` | `participant.rotate_key`, `participant.revoke_key`; top-level `sig` field verified at dispatch where the workspace advertises the profile or `options.require_signatures=True`, under the workspace's `signature_policy` |
| `audit-scitt/1.0`     | `audit.submit_to_scitt`, `audit.verify_receipt`, `audit.verify_chain`; SCITT statements assembled and passed to a deployment-supplied submitter; local prev-hash chain linkage retained |
| `identity-oidc/1.0`   | `participant.join` binding via `options.verify_oidc_token`; `cnf.jwk` pinning, step-up auth via `enforce_step_up=True` |
| `identity-vc/1.0`     | `participant.join` binding via `options.verify_vc`; holder-key pinning |

**40 method handlers in total.**

**The review gate.** A task carrying `review_required` does not complete on
`task.complete`. The call opens a review, holding the submitted output as the
artefact under review, and only a `decide.*` reaches `completed`. `task.update`
cannot complete such a task either, and answers `-32602` naming the route
through. Where no reviewer was named, the set is the human members other than
the completer and the assignee; a workspace with no eligible human refuses the
completion rather than opening a review nobody can decide. Under `modes/1.0` a
trial-mode task has `review_required` set for it.

## Architecture

The coordinator is **transport-agnostic, persistence-agnostic, and
UI-agnostic**. It exposes a single entry point:

```python
response = coord.dispatch(envelope)
```

Where `envelope` is a JSON-RPC 2.0 request and `response` is the
JSON-RPC 2.0 response. Bind whichever HTTP, WebSocket, or in-process
transport you like. State is held in memory, and kept in a store when
one is set: see [Running it for real](#running-it-for-real).

`dispatch` holds a lock, so calls from several threads are handled one at
a time. With a `store` option (`SqliteStore`, or a store of your own),
each call's changes are written before `dispatch` returns, and the
listeners hear of an entry once it is written. A call whose write fails is
answered with `-32603` and `data: {"reason": "store"}`, and its workspace
goes back to what the store holds; `store_errors="log"` answers it as it
was, and the next write carries its change. Before each call on a
workspace the coordinator asks the store how many entries it holds, and
reloads a workspace another instance has written; a write another instance
got to first runs the call once more on what the store holds
([SPECIFICATION.md §10.3](../../SPECIFICATION.md#103-writers)).

`chap_coordinator.server` binds HTTP with the desk included. A minimal
stdlib HTTP server is at
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
key is added to the participant's key history. A token with no
`cnf.jwk` binds the key in the join's `jwks` whose RFC 7638 thumbprint is
the token's `nonce`. Where a hook is configured and the workspace
advertises its profile, a `human` joins with the credential it checks or
is refused (`-32403`, `-32410`), and the log records the credential as
its SHA-256 digest, never the credential itself.

## Signed envelopes (security-signed/1.0)

To require signatures on every envelope:

```python
coord = Coordinator(CoordinatorOptions(require_signatures=True))
```

A workspace also requires signatures when it advertises
`security-signed/1.0`, and cannot stop advertising it once it does. Its
`signature_policy`, a `workspace.create` param that falls back to the
`signature_policy` option, is `"all"` by default; under `"humans"` an
unsigned call is taken from a member that is not human, so an agent on the
server's own transport works unsigned, and every person's decision is
still signed.

Each participant registers one or more JWKs at `participant.join` via
`jwks: {keys: [...]}`. Senders sign the JCS canonicalisation of the
envelope **with the top-level `sig` field removed**, then attach the
signature as a top-level `sig: "ed25519:<kid>:<base64>"` field. The
Coordinator looks up the verifying key by `(from, kid, ts)`, so
historical envelopes verify across key rotation.

A key held by a browser's authenticator, a P-256 JWK with a `webauthn`
member naming its `rp_id`, signs with a WebAuthn assertion whose challenge
is the SHA-256 of those same bytes, tagged
`webauthn:<kid>:<authenticatorData>.<clientDataJSON>.<signature>`
([profiles/security-signed.md](../../profiles/security-signed.md) §3).
Both kinds are verified through the `cryptography` package, the `crypto`
extra; without it a signed call is refused with `-32070` and a message
that names the extra.

A signed request takes effect once. The same signed call sent again, its
`sig` re-encoded or not, is refused with `-32075` and
`data.accepted_at_seq`, and a copy of a recorded refusal is answered with
that refusal; neither is evaluated again.

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
`assignee` to match the selected URI. The caller must be a workspace
member. The default policy takes the first candidate that is a member
and is not paused; a `routing_policy` selection that is not a member
is refused with `-32510`, and one that is paused with `-32063`.

## Audit and SCITT submission (audit-scitt/1.0)

Audit entries are hash-linked when the profile is active.
`audit.verify_chain` replays the chain locally and confirms
`prev_hash` continuity. The verdict names the range it covered:
`status` is `verified` with `ok: true` only when every entry was
checked, and `not_evaluated` with `ok: false` when part of the log
lies outside the chain, which happens when the profile is added to a
workspace that already has entries. A broken chain is an error. Read
`status`, or read `ok` and fail closed.

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

The suite covers the core library, the desk server, the client and the
command, the MCP and A2A transports, and the wrap helpers.

## Spec fidelity

This implementation was reviewed against every profile spec under
`profiles/` and aligned with the documented field names, error codes,
and response shapes. The full audit notes are in the parent repo's
[`CHANGELOG.md`](https://github.com/BrightbeamAI/chap/blob/main/CHANGELOG.md), with the 0.2.1 entry covering
the original Python implementation and 0.2.3 / 0.2.4 covering the
MCP and A2A transport additions.

## Licence

Apache 2.0. This package ships the licence in its own [LICENSE](./LICENSE) file.

## Specification

Tracks the spec at
[github.com/BrightbeamAI/chap/SPECIFICATION.md](https://github.com/BrightbeamAI/chap/blob/main/SPECIFICATION.md).
