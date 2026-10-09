# Templates: design

A developer with an agent should be able to put CHAP in front of it, decide
on real work in a browser, and see what each profile changes, without reading
the specification first. This document sets out how the templates do that
and the rules that keep them true to the coordinators.

## Where templates sit

[`scenarios/README.md`](../../scenarios/README.md) gives each kind of example
one home. Templates are a fifth: a generated project a developer runs
against their own agent and their own data. The starter stays the first run,
the examples stay one walkthrough per method, the bridges keep one demo each,
and the scenarios stay domain stories on Core. A template takes the pieces
those homes show one at a time and assembles them into a project that runs.

## The generator

```
npx create-chap-app my-gate
```

Three questions: the template, the profiles, and the project name. Each
question has a default, and `--template`, `--profiles`, `--yes` and a name on
the command line answer them without a prompt. The generator copies the
template, fills in the name, the workspace id, the participant URIs and the
profile list, and prints how to run the project. It has no dependencies, and
it never installs anything itself: the project's README says what to
install, and the project pins the published `chap-coordinator` packages.

## The template contract

Every template has:

- `README.md`: what the project does, how to run it, how to attach the
  developer's own agent and data, what each chosen profile changes in this
  project's own chain, and what the deployment supplies.
- `chap.config.json`: the workspace id, the participant URIs, the profile
  list and the store path, in one place, so a profile change is one edit.
- A desk: one process owns the SQLite store, serves the review desk, and
  answers CHAP calls over HTTP at `POST /chap`. The agent is a separate
  process, as it is in a deployment.
- An agent: the developer's own, attached through an MCP client, a framework
  bridge or the HTTP endpoint. Where a template drafts text, it uses the
  model named by the environment and a scripted agent when none is named.
- Tests that run with no model and no network, using the scripted agent.
- `diff-profiles`: runs the template's own workload under two profile sets
  and prints the two chains side by side, with the code of each refusal.

The decision is made by the developer, in the desk. A template never ships a
scripted decision outside its tests and `diff-profiles`, and it says so where
those run.

## The desk

One HTML page, the same in every template, served by the process that owns
the store. It lists the reviews addressed to the reviewer, shows the artefact
under review, and sends `decide.approve`, `decide.reject` or
`decide.override` as CHAP calls to `POST /chap`. The patch for an override is
computed in the browser as RFC 6902 operations. The page also shows the chain
as `audit.read` returns it.

The list of open reviews comes from the owning process, which reads its own
workspace state at `GET /api/reviews`. The chain records the calls, and the
coordinator answers the caller who asked; which reviews a person is shown is
the deployment's decision, as SPECIFICATION §15.1 says.

Under `security-signed/1.0` the desk signs each call in the browser with an
Ed25519 key it generates and keeps in the browser's storage. The public key
is registered at `participant.join`. The private key never leaves the
browser.

## The four templates

**MCP gate** (TypeScript). One process serves the desk, `POST /chap`, and an
MCP server over streamable HTTP at `/mcp`. Claude Desktop, Cursor or Claude
Code connects to it, and every tool call the assistant makes is recorded. A
task the assistant completes with `review_required` opens a review, and the
assistant cannot approve its own work. The developer decides in the desk. No
API key is needed.

**Support desk** (Python). Tickets come from a CSV, with three sample rows
and a column to point at the developer's own export. The agent drafts a reply
to each, the draft waits for a decision, and the approved reply is written to
`replies/`. A rejected draft is written nowhere.

**Outbound approval** (Python). Drafts from the agent are held until the
named approver decides, and only an approved message reaches `outbox/`. The
template runs under `modes/1.0`, so a trial-mode task requires review
whatever the agent says, and `control/1.0`, so a pause stops the agent's
tasks until a person resumes them. This is the gate most teams run first.

**Production** (TypeScript, Docker Compose). The Handbook's production set:
`core/1.0`, `review/1.0`, `modes/1.0`, `identity-oidc/1.0` and
`security-signed/1.0`, with the chain on. The coordinator runs as one
service with SQLite on a volume and refuses unsigned calls. The agent runs as
a second service with its own key. The desk signs in the browser. OIDC
verification checks a token against the issuer's keys when an issuer is
configured. `doctor` checks that the store persists, the chain verifies,
signatures are required, and the workspace advertises what the coordinator
enforces.

## Model providers

A template that drafts text reads `CHAP_MODEL_PROVIDER`, or chooses from the
keys present: `ANTHROPIC_API_KEY` for the Anthropic Messages API,
`OPENAI_API_KEY` for the OpenAI chat completions API, and `OLLAMA_URL` for a
local model. With none of these set, a scripted agent drafts from the
input, and the project says so on its console. The playground uses the same
rule. No vendor SDK is installed; each provider is one HTTP call.

## Profiles and `diff-profiles`

The profile list in `chap.config.json` is the one the workspace advertises.
`diff-profiles` runs the template's workload twice, under the configured set
and under a second set given on the command line, and prints each call with
its outcome under both. The rows that differ are the ones a profile changes:

- `review/1.0`: a draft waits for a decision, and the agent cannot approve
  its own work (`-32011`).
- `modes/1.0`: a trial-mode task requires review whatever the caller says.
  The mode ceiling is enforced on every workspace (`-32040`); the profile
  adds the trial rule.
- `control/1.0`: a paused task cannot be completed (`-32602`), a paused
  participant is assigned nothing (`-32063`), and a resumed task returns to
  the state it held.
- `whisper/1.0`: a question nobody answers applies its default at the
  deadline.
- `deliberation/1.0`: a decision needs the quorum before the task completes.
- `security-signed/1.0`: an unsigned call is refused (`-32070`).
- `audit-scitt/1.0`: the chain is on and `audit.verify_chain` can check it.

`docs/profile-explorer.md` collects these rows. It is generated by
`npm run build:explorer` from a TypeScript template and a Python template,
which must answer every row the same way, and CI fails when the file would
change, so it stays in step with the coordinators.

## What the deployment supplies

Each README has a section with this heading. It lists what the template does
not do and the deployment has to: deliver notifications to the people named
on a review (SPECIFICATION §15.1), run more than one coordinator process,
obtain OIDC tokens through its own login, and keep the chain head somewhere
the coordinator's operator cannot change (SECURITY.md).

## CI

A workflow generates every template, installs the published packages the
template pins, runs its tests and `diff-profiles`, and rebuilds the explorer.
A template that claims something the coordinators do not do fails here. The
templates ship with the release they were generated against and are
regenerated when the coordinators change.
