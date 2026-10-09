# __PROJECT_NAME__

A gate on the commits a coding agent makes. The agent works in a git
repository as it does today; every change it wants to commit is proposed as
a patch, reviewed as a diff in the desk, decided and signed by a person, and
only then committed, by hooks that refuse anything else. The commit carries
the decision as trailers and the evidence as a git note: the artefact as
proposed and as approved, the reviewer's signed decision as the chain holds
it, and the chain head. A verifier checks any range of commits from the
repository alone, or against the running gate, and a workflow runs it on
every pull request. The gate runs the production profile set: signed calls,
the chain on, trial mode so every task requires review whatever the agent
asks for, and a pause for an agent that needs stopping.

It works with Claude Code, Cursor and any agent that can run a command,
through `propose.mjs`; with anything that can POST JSON, through
`POST /chap`; and with the built-in agent, which drafts with the model the
environment names or with no model at all.

## Run it

```
npm install
npm run keys          the agent's Ed25519 key, written to keys/
npm start             the gate: the desk, POST /chap and /mcp on port 8791
```

Open <http://127.0.0.1:8791/>. The desk joins you as `__HUMAN_URI__` with a
key it generates in the browser. In a second terminal:

```
npm run demo-repo     a small repository at ./demo-repo, under the gate's hooks
npm run agent         the built-in agent takes tasks.csv through the gate
```

The agent drafts the first task, submits the patch, and waits. The diff
appears in the desk. Approve it as written, and the agent commits it on
`agent/<date>` in the demo repository with the trailers and the note; the
second task you can reject with a note and "ask for a revision" ticked, and
the agent drafts again with your note; edit a draft's patch before approving
and the reviewer's version is what lands. Then:

```
cd demo-repo
git log --show-notes=chap                  the commits with their evidence
node ../verify.mjs main..agent/<date>      every commit checked from the repository alone
cd .. && npm run report                    what the gate recorded, as Markdown
```

`npm test` runs the whole path in-process, with no model and no network.

## Put your own repository under the gate

```
node install-hooks.mjs --repo /path/to/your/repo
```

sets `core.hooksPath` there to this project's `hooks/`. From then on
`git commit` in that repository runs the gate's three hooks:

- `pre-commit` refuses the commit unless the staged change is exactly an
  approved artefact: the approved patch applied to the commit's parent gives
  the tree being committed. The patch text is compared first, then the
  trees, so a patch git prints differently still matches when it makes the
  same change.
- `commit-msg` adds the `CHAP-*` trailers for that approval.
- `post-commit` writes the evidence note under `refs/notes/chap`.

`CHAP_GATE=off git commit` commits without an approval and says so; the
commit carries no trailers, and the verifier fails it. Removing the setting
is `node install-hooks.mjs --repo ... --remove`. A repository that already
runs hooks from somewhere else is reported and left alone unless `--force`
is given; chain the gate's hooks from yours in that case.

**Claude Code.** Copy `AGENT_INSTRUCTIONS.md` into the repository as
`CLAUDE.md`, or paste it into the project's instructions. It tells the
assistant never to commit itself and to run `propose.mjs` when a change is
ready, with `--wait --commit` so the approved change is committed through
the hooks in the same step. For the `chap.*` tools, which let it read its
tasks and the chain:

```
claude mcp add --transport http chap http://127.0.0.1:8791/mcp
```

**Cursor and other assistants.** The same file goes into the project rules.
Any assistant that can run a shell command can propose; one that cannot
leaves `propose.mjs` to you: edit with the assistant, then propose and
commit yourself.

**Your own agent.** `propose.mjs` is a thin command over `lib/gate.mjs`:
`describeChange` takes the patch of the working tree, `propose` opens the
task and submits it, `waitForDecision` polls the read API. An agent in
another language sends the same two calls to `POST /chap`: `task.create`
with `kind: "code_change"` and `review_required: true`, then
`task.complete` with the artefact, `{ summary, repo, branch, base, files,
patch, drafted_by }`, and reads `GET /api/tasks/<id>` until the state is
no longer `review_requested`. Under `security-signed/1.0` the calls are
signed with the agent's key; `keys/` holds it and `desk/chap-client.mjs`
shows the signing.

## What a governed commit carries

The message ends with trailers:

```
CHAP-Workspace: __WORKSPACE__
CHAP-Task: tsk_...
CHAP-Agent: __AGENT_URI__
CHAP-Reviewer: __HUMAN_URI__
CHAP-Decision: approve
CHAP-Artefact: sha256:...
CHAP-Coordinator: http://127.0.0.1:8791/chap
CHAP-Chain-Head: sha256:...
```

and the note under `refs/notes/chap` holds the artefact as proposed and as
approved, the decision envelope with the reviewer's signature, the
reviewer's public keys as the workspace records them, and the chain head
at the decision. `git log --show-notes=chap` prints it. Notes travel
separately from commits: `git push origin refs/notes/chap` sends them, and
`git fetch origin refs/notes/chap:refs/notes/chap` brings them down, which
the workflow below does. The post-commit hook prints the push command each
time it writes a note.

## Verify

```
node verify.mjs main..HEAD
```

checks every commit in the range: the approved artefact's digest matches
the trailer; the decision envelope names the task and the reviewer and
signs the digest of what was proposed; an override's operations lead from
the proposed artefact to the approved one; the reviewer's signature
verifies against the key on record; and the approved patch applied to the
commit's parent gives the commit's tree. None of that needs the gate
running. `--coordinator http://127.0.0.1:8791/chap` also reads each task
there, which must be completed with that decision. A commit without
trailers fails. `--allow-git-signed` lets one through when git verifies the
commit's own signature, for people's commits on a shared branch, once the
verifying keys are configured where it runs. A merge commit is reported and
passed; its parents are what is checked.

`.github/workflows/chap-verify.yml` runs the verifier on every pull request
from the notes alone. Copy it into the governed repository, keep the gate
project in that repository (`tools/chap-gate` in the file) or point
`CHAP_GATE_DIR` at it, and protect the branch with the check. The
repository then takes no commit from an agent that was not approved through
the gate, whatever the agent did locally.

## The desk

The queue on the left lists the changes waiting for you; each opens as a
diff, file by file, with the task's summary, the agent, the model, the
branch and the base commit beside it. Approve as written, request changes
with a note (the agent drafts again on the same task), reject, or edit the
patch and approve your version, which the gate records as an override with
your rationale and applies before the commit. Every decision carries the
digest of the artefact you saw, so a draft that changed under you is
refused rather than approved. The activity view reads the chain; the
Insights view counts what happened and shows what reviewers change most,
and links the pages `npm run analytics` writes.

## Insights and analytics

`npm run report` prints, from the running gate, how many changes were
approved as written, approved with an edit, sent back and rejected, the
time to a decision, the agents and models behind the changes, and every
override rationale and rejection note. Those notes are the material for the
agent's instructions: a correction that recurs is a rule to add.

`npm run analytics` goes further with `chap-analytics`, the package that
reads a CHAP chain into documented tables: it reads the gate's store,
writes the interactive report with its filters and charts to
`analytics/report.html`, the evaluation cases (each corrected change with
the agent's version and the reviewer's) to `analytics/cases.jsonl`, and the
correction clusters most worth addressing to `analytics/refine.md`. The
desk serves them under Insights. It needs Python 3.10 or later and
`pip install chap-analytics`; `--watch` regenerates them every few minutes
while the gate runs.

## The built-in agent

`agent.mjs` takes each row of `tasks.csv` (`title`, `brief`, and a `files`
hint, semicolon separated) as one task, on one branch, one at a time: it
asks the model for the files to change, writes them into the working tree,
proposes the patch, waits, and commits an approved change through the
hooks. A rejection with a note has it draft again with the note; a
rejection takes the change back out. The model comes from `ANTHROPIC_API_KEY`,
`OPENAI_API_KEY` or `OLLAMA_URL`, with `CHAP_MODEL_PROVIDER` choosing when
more than one is set; with none, a scripted drafter handles the three sample
tasks and leaves a note in `NOTES.md` for any other, so the path runs with
nothing configured. A restarted agent finds its tasks by their idempotency
keys and commits what was approved and not yet committed.

It shows the whole path with nothing else installed. An assistant with its
own tools, like Claude Code, is the better agent for real work; the gate is
the same either way.

## What each profile changes here

- `core/1.0`: the workspace, its members, the tasks and the chain.
- `review/1.0`: a change waits for a decision; the agent cannot approve its
  own work (`-32011`); an edit at the desk is an override with its patch
  and rationale, and the committed tree is the reviewer's version.
- `modes/1.0`: the workspace runs in trial mode with a trial ceiling, so
  every task requires review whatever the agent passes, and a task asking
  for `production` is refused (`-32040`).
- `control/1.0`: a paused agent is assigned nothing (`-32063`) until it is
  resumed; `npm run report` and the desk show the pause.
- `security-signed/1.0`: every call after the join is signed with the
  sender's key or refused (`-32070`); the reviewer's signature on the
  decision is what the note carries and the verifier checks.
- `audit-scitt/1.0`: the chain is on, `audit.verify_chain` checks it, and
  the chain head at the decision is a trailer on the commit.

`npm run diff-profiles -- --against core/1.0` runs the same workload under
the configured profiles and under `core/1.0` alone and prints each call
with its outcome under both.

## What the deployment supplies

- TLS in front of the gate, and its own login in front of the desk. A
  `human:` URI in the desk is a label; the browser key under
  `security-signed/1.0` is what ties a decision to its holder, and OIDC at
  the join (`OIDC_ISSUER`, as in the production template) ties the key to a
  person. The gate answers under its own host names only; the name a proxy
  passes goes in `allowed_hosts` in `chap.config.json` or `CHAP_ALLOWED_HOSTS`.
- Branch protection that requires the verify check, and a push policy for
  `refs/notes/chap`, so the evidence reaches the server with the commits.
- Delivery: telling a reviewer a change waits. The desk polls; nothing here
  pushes a notification.
- A key per reviewer and a key per agent, kept where the gate's `keys/`
  directory is not: a secrets store, an HSM, or the deployment's identity.
- Keeping the chain head somewhere the gate's operator cannot change it, a
  SCITT receipt or a head published elsewhere (SECURITY.md), since whoever
  can write to the store can rewrite the log.

`tests/` and `diff-profiles.mjs` script the decisions, because nobody is at
the desk when they run; the project itself never does.
