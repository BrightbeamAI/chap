# HAP Core + Review Reference

A reference implementation of [HAP Core](../../core/SPEC.md) plus the
[`review` profile](../../profiles/review.md), in approximately 500
lines of TypeScript. No external dependencies at runtime; Node 18+
built-ins only.

This is the implementation that demonstrates HAP's killer feature:
**every human override of an agent's output becomes structured data
in the audit log, queryable for free.**

| File                      | What it is                                                            |
|---------------------------|-----------------------------------------------------------------------|
| `server.ts`               | Core + Review server. All 13 methods, in-memory state, JSON-RPC 2.0.  |
| `client.ts`               | Demo: workspace setup → draft → review request → override.            |
| `analyze-overrides.ts`    | Reads the audit log and produces the learning-data report.            |
| `package.json`            | npm scripts.                                                          |
| `tsconfig.json`           | Strict TS, ES2022, Node 18+.                                          |

---

## Quickstart

```bash
npm install
npm run start:demo                 # server on http://localhost:8080/hap
# in another terminal:
npm run demo:client                # walks the override-capture flow
npm run analyze                    # produces the learning-data report
```

Or run all three in one shot:

```bash
npm run demo
```

---

## What the demo shows

The client script tells one story end-to-end:

1. **Workspace setup.** Alice (a senior support agent, human) and
   Triage Bot (an agent) join the `wsp_support_triage` workspace.
2. **Task delegation.** Alice creates a `draft_response` task for
   the bot.
3. **Drafting.** The bot updates state to `in_progress`, then
   produces a draft and requests review.
4. **Override.** Alice reviews. The bot's draft is over-apologetic
   for what is just a tracking question. She submits an override —
   a JSON Patch + rationale + tags + policy references — that
   softens the tone and downgrades the severity.
5. **Audit query.** The override is now structured data. A
   filtered `audit.read` returns it.

That's HAP's unique contribution: the override isn't a chat message
or a comment thread — it's a typed artefact with a diff, rationale,
tags, and policy references, all queryable from the audit log
without any extra instrumentation.

---

## What the analyser shows

`analyze-overrides.ts` queries the audit log for every
`decide.override` envelope and produces:

- **Tag frequency.** Which kinds of edits dominate? `tone-softened`,
  `severity-downgraded`, `factual-fix`, etc.
- **By reviewer.** Which humans override most often, and what for?
- **Policies cited.** Which guidelines are actually driving overrides?
- **Most-edited fields.** Which paths in the draft get patched most?
- **Interpretation.** A short narrative of what the dominant pattern
  suggests as a tuning action.

After a week of overrides in a real workspace, this report tells you
exactly where to spend the next hour of agent improvement work. It
is the structured-override dividend made tangible.

---

## Implementing the review profile yourself

The 6 review-profile methods, in implementation order:

1. **`review.request`** — opens a review on a task; takes the
   draft artefact, the rule (`any_one_approves` / `all_approve`),
   and the deadline.
2. **`decide.approve`** — terminal acceptance. Sets task state to
   `completed`.
3. **`decide.reject`** — terminal rejection, or revision-request
   (state → `in_progress`) if `request_revision: true`.
4. **`decide.override`** — the differentiator. Accepts an RFC 6902
   JSON Patch and applies it deterministically to the base artefact.
   Stores the patch, rationale, tags, and policy refs as an
   override artefact.
5. **`abstain.declare`** — typed "I shouldn't decide this." Records
   a categorised abstention.
6. **`escalate.raise`** — creates a successor task referencing the
   original via `supersedes`.

The non-trivial part is the JSON Patch implementation. The
reference includes a tiny one (~30 lines) supporting `add`,
`replace`, and `remove`. Production implementations should use a
proper library — `fast-json-patch` for JavaScript, `jsonpatch` for
Go, `python-json-patch` for Python.

---

## Beyond this reference

This reference covers Core + Review. The other profiles
(`security-signed`, `audit-scitt`, `identity-oidc`, `identity-vc`,
`whisper`, `deliberation`, `modes`, `handoff`, `control`) follow
the same pattern: add methods, add state, add a profile id to the
`workspace.describe` response.

See [`../../profiles/PROFILES.md`](../../profiles/PROFILES.md) for
the full catalogue and [`../../HANDBOOK.md`](../../HANDBOOK.md) for
the operator's guide to profile selection.
