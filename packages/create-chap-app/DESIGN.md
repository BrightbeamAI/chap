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

One page, the same in every template, served by the process that owns the
store, with no build step and no dependency: `desk/index.html`, a stylesheet
and its modules. It has three views.

- **Review.** The queue lists what waits for the reviewer, what is in
  progress and what was decided. The artefact is shown by its shape: a code
  change as a diff, file by file, with line numbers; a message as a letter;
  anything else as JSON. The reviewer approves as written, requests changes
  with a note (`decide.reject` with `request_revision`), rejects, or edits
  the artefact and approves the edit (`decide.override`, with the RFC 6902
  operations computed in the browser). A code change is edited a file at a
  time, whole, and the desk writes that file's part of the patch again in
  the form `git apply` takes.
- **Activity.** The chain as `audit.read` returns it, a page at a time, with
  what each call did, filters and the refusals on their own.
- **Insights.** What the workspace's tasks say, counted in the browser: how
  often work is accepted as written, edited, sent back and rejected; the
  time to a first decision; agents, models and reviewers; for code changes,
  the files reviewers edit or send back; and the reviewers' own words. It
  links the pages `chap-analytics` writes, below.

Keyboard shortcuts, light and dark, text that grows with the window from a
phone to a large monitor, two buttons that make it smaller or larger for
each reviewer, and a layout that folds on a narrow screen. The task lists
come from the owning process, which reads its own workspace state at
`GET /api/reviews` and `GET /api/tasks`, with each task's decisions across
review rounds read from the chain. The lists the desk polls are brief,
without whole-file contents or a branch's patches, and the task on show is
read whole from `GET /api/tasks/<id>`. The chain records the calls, and the
coordinator answers the caller who asked; which reviews a person is shown
is the deployment's decision, as SPECIFICATION §15.1 says.

Under `security-signed/1.0` the desk signs each call in the browser with an
Ed25519 key it generates and keeps in the browser's storage. The public key
is registered at `participant.join`. The private key never leaves the
browser. Every decision carries `approved_artefact_digest`, the hash of the
artefact the desk showed, so the coordinator refuses a decision on an
artefact the reviewer did not see, and `round`, the hash of the submission
that opened the review round, which the coordinator records with the call. A
code change whose patch the desk cannot show as git would apply it, or that
lists a file twice, can be rejected or sent back and offers no approval;
characters a reader cannot see in a changed line are shown as markers.

A `human:` URI in the desk is a label: it says who is deciding and does not
authenticate them. The desk and its read API have no login of their own;
the deployment puts its login in front, and under `security-signed/1.0`
the signing key is what ties a decision to its holder. The process answers
under its own host names only, refuses a browser request from another
origin, takes JSON only on `POST /chap` and caps the request body, so a
page open elsewhere cannot decide as the reviewer.

## The agents

The agents a template ships share one shape, in TypeScript and in Python.
Each row of the template's CSV becomes one task, created with an
idempotency key made from the row, so a restarted agent finds the tasks it
opened before and opens no duplicates. On each pass the agent reads the
file again and moves every open task one step: it drafts, waits on
`workspace.describe` until a reviewer is a member, since a review with
nobody to address it to is refused and the refusal recorded, submits with
`task.complete`, and reads `GET /api/tasks/<id>` until the task is decided.
A rejection that asks for a revision returns the task to `in_progress` with
the reviewer's comment on record, and the agent drafts again with that note.
Only an approved or overridden artefact, as decided, is written out. Under
`control/1.0` a paused agent sees one refused `task.create`, then waits on
a read until it is resumed.

## The templates

**Code gate** (TypeScript). Every change a coding agent makes in a git
repository is proposed as a patch, reviewed as a diff in the desk, decided
and signed by a person, and committed only then. The repository's hooks
refuse a commit unless the staged tree is an approved change: approved
against the commit's parent, read by the desk as the change git makes with
it, unused by any earlier commit, and approved by reviewers a trust policy
names. The commit carries four trailers: the model that wrote the change
(`Drafted-by`), each approving reviewer by name and email (`Reviewed-by`,
from the trust policy), the committer (`Signed-off-by`), and
`CHAP-Approval`, the task whose evidence the note holds, which holds an
approval to one commit in history nobody can rewrite. It is signed as the
committer's own commits are, or with the agent's key where the team asks for
that; a note under `refs/notes/chap` carries the agent's signed proposal,
each reviewer's signed decision, the artefact as proposed and as approved,
and the chain head. The trust policy, `chap-trust.json` in the governed
repository, pins the reviewers' and agents' keys and the rule; `verify.mjs`
checks a range of commits against it from the repository alone, and a
workflow runs the base branch's verifier and policy on every pull request.
The hooks are the guardrail on a developer's machine, where they can be
switched off; the pull request check, required by branch protection, is the
boundary. It asks of a pull request that its commits be one line from where
they leave the base branch, each approved on the commit it sits on, with no
approval used in the base branch's history already. Every git command that
reads a change runs with the repository's attributes and configuration set
aside, so a `.gitattributes` in the change cannot alter what the reviewer is
shown, and the contents the desk shows beside a patch are checked against
the repository's blobs. Claude Code, Cursor or any agent that can run a
command proposes with `propose.mjs`; a built-in agent and `npm run demo`
show the whole path with nothing else installed. An agent that commits on
its own, in a sandbox or a loop, has its branch reviewed before it is pushed
(`review_at: push`): `propose-branch.mjs` proposes the commits as one
review, which the desk shows commit by commit, and on approval seals them,
writing each again with the same tree, author and message and the gate's
trailers. A pre-push hook lets only approved commits leave the machine. A
branch is decided as its commits stand, with no edit, and the evidence keeps
its artefact once, in the agent's signed submission. One command runs the
review loop for an agent: it starts the gate in the background when none
runs on the machine, brings the review to the reviewer (an open desk shows
it, and the browser opens otherwise), waits within the time an agent's tool
call may take, and turns each decision into a prompt the agent acts on: the
reviewer's note, their comments on lines with the code each is about, an
edit as a diff, and the command to run again. A revision carries what
changed since the reviewers last looked, and the agent's own context note
travels with every proposal.

**MCP gate** (TypeScript). One process serves the desk, `POST /chap`, and an
MCP server over streamable HTTP at `/mcp`. Claude Desktop, Cursor or Claude
Code connects to it, and every tool call the assistant makes is recorded. A
task the assistant completes with `review_required` opens a review, and the
assistant cannot approve its own work. The developer decides in the desk. No
API key is needed.

**Support desk** (Python). Tickets come from a CSV, with three sample rows,
and the developer points the agent at their own export. The agent drafts a reply
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

## Review rules and rounds

A review opened on `task.complete` is addressed to the human members other
than the producer under `any_one_approves`. A template that needs more than
one approval opens each round with `review.request` and the rule, so a round
starts with no decisions: in the coordinators, a resubmission through
`task.complete` after `request_revision` keeps the earlier round's
decisions, which would let a quorum be met across two versions. And because
`decide.override` settles a review whatever its rule, the desk offers no
edit under a multi-approval rule, and the code gate's checks refuse an
override there. The code gate applies its own rule, from the trust policy
and `chap.config.json`, to the approvals on record, whatever rule the review
ran under, so an agent that opens a one-approval review gains nothing, and
it counts an approval only in the round the approval names, so approvals
from two rounds of one change never add up to a quorum, whatever a
coordinator kept. Both coordinator behaviours are noted for the
coordinators.

## Analytics

`analytics.py`, shipped with every template, reads the project's store with
`chap-analytics` and writes three pages under `analytics/`: the package's
interactive report, the evaluation cases (each corrected task with the
agent's output and the reviewer's) and a refinement page with the
correction clusters ranked, the files reviewers edited for code changes,
the rejection notes and the briefs. The server serves them under
`/analytics/`, and the desk's Insights view links them. It reads a store
either coordinator wrote. The loop it serves: a correction that recurs is a
rule for the agent's instructions, and the next run shows whether it held.

## Model providers

A template that drafts text reads `CHAP_MODEL_PROVIDER`, or chooses from the
keys present: `ANTHROPIC_API_KEY` for the Anthropic Messages API,
`OPENAI_API_KEY` for the OpenAI Responses API, and `OLLAMA_URL` for a
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
- `deliberation/1.0`: a vote closes with an outcome under its rule, and
  one yea under `quorum:2` closes as rejected.
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
template pins, runs its tests and `diff-profiles`, runs the analytics script
against a store a template wrote, and rebuilds the explorer. A template that
claims something the coordinators do not do fails here. The templates ship
with the release they were generated against and are regenerated when the
coordinators change.
