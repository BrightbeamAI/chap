# CHAP Core Reference: Weekend Build

This is the minimal reference implementation of [CHAP Core](../../core/SPEC.md).
It implements all 7 Core methods, an in-memory state model, and the JSON-RPC 2.0
wire format. **No crypto, no profiles, no external dependencies at runtime.**

It is written to be **read first, then used**. The goal is to make Core
implementable in a weekend in any language; this is the worked TypeScript
example.

| File              | What it is                                           |
|-------------------|------------------------------------------------------|
| `server.ts`       | The full Core server (~300 lines).                   |
| `client.ts`       | A demo client that walks every Core method.          |
| `package.json`    | Scripts: `start:demo`, `demo:client`, `demo`.        |
| `tsconfig.json`   | Strict TS, ES2022 target, Node 18+ built-ins only.   |

For Core + the Review profile (override capture, JSON Patch
application, override-analysis tooling), see
[`../core-plus-review/`](../core-plus-review/).

---

## Quickstart

```bash
npm install
npm run start:demo   # server on http://localhost:8080/chap
# in another terminal:
npm run demo:client
```

Or in one command:

```bash
npm run demo
```

You should see output like:

```
CHAP Core demo against http://localhost:8080/chap
============================================================

→ participant.join
  params: {"workspace":"wsp_demo","from":"human:alice@example.org",…
  ✓ result: {"joined":true,"as":"human:alice@example.org","role":"reviewer"}

→ participant.join
  ✓ result: {"joined":true,"as":"agent:triage-bot","role":"drafter"}

→ workspace.describe
  ✓ result: {"id":"wsp_demo","created":"2026-05-17T…","state":"active",…

→ task.create
  ✓ result: {"task_id":"tsk_…","state":"created"}

→ task.update
  ✓ result: {"state":"in_progress"}

→ task.complete
  ✓ result: {"state":"completed"}

→ audit.read
  ✓ result: {"entries":[…],"next_seq":6}

→ participant.leave
  ✓ result: {"left":true}
```

That's CHAP Core, end to end.

---

## How this maps to the spec

| Spec section ([core/SPEC.md](../../core/SPEC.md)) | Code in `server.ts`              |
|---------------------------------------------------|----------------------------------|
| §2 Envelope                                       | `Envelope` interface; `dispatch()`. |
| §3 State model                                    | `Workspace`, `Member`, `Task` types; `workspaces` map. |
| §3.1 Task states                                  | `legal` table inside `task.update`. |
| §3.2 Audit log                                    | `AuditEntry`; `recordAudit()`.   |
| §4.1 `workspace.describe`                         | Handler in `handlers`.           |
| §4.2 `participant.join`                           | Handler in `handlers`.           |
| §4.3 `participant.leave`                          | Handler in `handlers`.           |
| §4.4 `task.create`                                | Handler in `handlers`.           |
| §4.5 `task.update`                                | Handler in `handlers`; state-machine check. |
| §4.6 `task.complete`                              | Handler in `handlers`.           |
| §4.7 `audit.read`                                 | Handler in `handlers`; filters.  |
| §5 Error codes                                    | `E` constants and `err()` helper. |
| §2.4 Transport                                    | `createServer()`, `POST /chap`.   |

The full server is one file of ~300 lines. Read it top to bottom; it's
intentionally shaped like a tutorial.

---

## What this reference does NOT do

By design:

- **No signatures.** See [`profiles/security-signed`](../../profiles/security-signed.md).
- **No identity binding.** See [`profiles/identity-oidc`](../../profiles/identity-oidc.md) or [`identity-vc`](../../profiles/identity-vc.md).
- **No review/override workflow.** See [`profiles/review`](../../profiles/review.md). *This is the highest-value extension; consider it next.*
- **No persistence.** All state lives in memory; restart loses everything.
- **No authentication.** Every request is accepted. Trusted-network only.
- **No transport beyond HTTP+JSON.** WebSocket/SSE/Kafka all map cleanly; pick whichever your operational stack already supports.
- **No SCITT audit.** See [`profiles/audit-scitt`](../../profiles/audit-scitt.md).

These are deliberate omissions to keep the surface tiny. Add each one
through its profile when (and only when) you actually need it.

---

## The 8-hour build sequence

If you're implementing CHAP Core in a different language, follow the
sequence in [`core/SPEC.md §9`](../../core/SPEC.md#9-implementing-core-in-a-weekend):

1. **Hour 1**: HTTP POST `/chap`, JSON-RPC 2.0 parse + dispatch.
2. **Hour 2**: `workspace.describe`, in-memory workspace state.
3. **Hour 3**: `participant.join`, `participant.leave`.
4. **Hour 4**: `task.create`, `task.update`, `task.complete` with state machine.
5. **Hour 5**: `audit.read` with filters.
6. **Hour 6**: JSON-RPC error codes (-32700, -32600, -32601, -32602, -32603).
7. **Hour 7**: Demo client end-to-end.
8. **Hour 8**: Conformance vectors ([../../conformance/test-vectors.md](../../conformance/test-vectors.md)).

This TypeScript reference covers hours 1-7. Hour 8 is shared with every
Core implementation.

---

## Going to production

Three things to change before deploying this code anywhere real:

1. **Swap the in-memory store for a database.** SQLite or Postgres. The
   audit log is the most important table; add an index on
   `(workspace, seq)`.
2. **Add authentication.** Bearer tokens are the simplest option (Core's
   §2.5). For cross-trust deployments, add the [`security-signed`](../../profiles/security-signed.md) profile.
3. **Pick your profiles.** A workspace stays at Core indefinitely if all
   it needs is task delegation + audit. Add `review` the moment humans
   approve agent output.

The Core + Review reference at [`../core-plus-review/`](../core-plus-review/)
shows what adding one profile looks like, about a hundred more lines
plus an override-analysis tool. Use it as the next step after Core,
not the starting point.
