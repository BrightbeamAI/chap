# __PROJECT_NAME__

A gate on the commits coding agents make. The agent works in a git
repository as it does today. Every change it wants to commit is proposed as a
patch, reviewed as a diff in the desk, decided and signed by a person, and
only then committed, by hooks that refuse anything else. The commit carries
the decision as trailers and is signed with the agent's key; the evidence
travels beside it as a git note: the artefact as proposed and as approved,
each reviewer's signed decision as the chain holds it, and the chain head.
A verifier checks any range of commits from the repository alone, and a
workflow runs it on every pull request. Insights and `chap-analytics` turn
what reviewers corrected into what to change in the agent's instructions.

It works with Claude Code, Cursor and any agent that can run a command,
through `propose.mjs`; with anything that can POST JSON, through
`POST /chap`; and with the built-in agent, which drafts with the model the
environment names or with no model at all.

## See it work

```
npm install
npm run demo
```

The demo makes the agent's key, starts the gate on port 8791 with its store
under `data/`, makes a small repository under `demo-repo/` with the gate's
hooks in it, and runs the built-in agent on the three tasks in `tasks.csv`.
Open <http://127.0.0.1:8791/>. The desk joins you as `__HUMAN_URI__` with a
key it generates in the browser, and the agent's first change arrives as a
diff:

- **Approve** it, and the agent commits it on `agent/demo` with the trailers,
  the note and its signature.
- **Request changes** with a note on the second, and the agent drafts again
  with your note and submits the revision to the same task.
- **Edit** a file of a change in the desk and approve your version: the agent
  applies your edit before it commits, and the commit is recorded as
  approved with an edit, with your rationale.
- **Reject** the third, and the agent takes the change back out.

Then look at what was recorded:

```
cd demo-repo
git log --show-signature --show-notes=chap agent/demo
node ../verify.mjs main..agent/demo --require-signed-commit
cd .. && npm run report
```

`npm run demo -- --fresh` starts again from nothing. `npm test` runs the whole
path in-process with no model and no network.

## Put a repository under the gate

```
npm run keys                                     the agent's key, once
npm start                                        the gate
node install-hooks.mjs --repo /path/to/repo      the hooks
```

`install-hooks.mjs` sets `core.hooksPath` in that repository to this
project's `hooks/`, so every `git commit` there runs the gate's three hooks:

- `pre-commit` refuses the commit unless the staged change is exactly an
  approved artefact: the approved patch applied to the commit's parent gives
  the tree being committed. It then builds the evidence and checks it as the
  verifier will: the signatures, the digests and the review rule.
- `commit-msg` adds the `CHAP-*` trailers for that approval.
- `post-commit` writes the evidence note under `refs/notes/chap`.

It also points the repository's `gpg.ssh.allowedSignersFile` at
`keys/allowed_signers` when nothing is set there, so `git log
--show-signature` names the agent. `CHAP_GATE=off git commit` commits
without an approval and says so; the commit carries no trailers, and the
verifier fails it. `--remove` takes the hooks away; a repository that runs
hooks from somewhere else already is reported and left alone unless
`--force` is given.

**Claude Code.** Copy `AGENT_INSTRUCTIONS.md` into the repository as
`CLAUDE.md`, or into the project's instructions. It tells the assistant
never to commit itself, and to run `propose.mjs` with `--wait --commit` when
a change is ready, which proposes, waits for the decision and commits an
approved change through the hooks in one step. For the `chap.*` tools, which
let it read its tasks and the chain:

```
claude mcp add --transport http chap http://127.0.0.1:8791/mcp
```

**Cursor and other assistants.** The same file goes into the project rules.
An assistant that cannot run a command leaves `propose.mjs` to you: edit with
the assistant, then propose and commit yourself.

**Your own agent.** `propose.mjs` is a thin command over `lib/gate.mjs`:
`describeChange` takes the patch of the working tree, `propose` opens the task
and submits it, `waitForDecision` polls the read API. An agent in another
language sends the same calls to `POST /chap`: `task.create` with
`kind: "code_change"` and `review_required: true`, then `task.complete` with
the artefact, `{ summary, repo, branch, base, files, patch, drafted_by }`, and
reads `GET /api/tasks/<id>` until the state is no longer `review_requested`.
Under `security-signed/1.0` each call is signed with the agent's key;
`desk/chap-client.mjs` shows the signing.

## Rolling it out to a team

1. **One gate for the team.** Run the gate as a service: `npm start` behind
   TLS and the team's login, or the production template's Compose file with
   this project's files. Name the host the proxy passes in `allowed_hosts`
   or `CHAP_ALLOWED_HOSTS`, and set `OIDC_ISSUER` to tie each reviewer's
   browser key to a person, as the production template describes.
2. **The gate project in the repository.** Commit this project as
   `tools/chap-gate` in the governed repository, or install it beside it. Its
   `.gitignore` keeps `keys/`, `data/` and `node_modules/` out.
3. **One agent identity per developer.** Each developer sets `CHAP_URL` to the
   team's gate and `CHAP_AGENT_URI` to their agent's own URI, say
   `agent:claude-code@alice`, runs `node keys.mjs` once to make that agent's
   key, and runs `install-hooks.mjs` in their clone. The agent's public line
   from `keys.mjs` goes on the code host as a signing key for that identity,
   so the host shows the commits as verified.
4. **A review rule.** `"review": { "rule": "quorum:2" }` in
   `chap.config.json` makes every change wait for two reviewers. Under a rule
   that needs more than one approval, each revision opens a new round, so an
   approval of an earlier version does not count for the revised one, and
   the desk offers no edit, since an edit settles a review with one person's
   decision: reviewers request changes and the agent revises. `all_approve` with
   `"to": [...]` names the reviewers every change needs.
5. **The check on every pull request.** Copy
   `.github/workflows/chap-verify.yml` into the repository and make it a
   required check in the branch protection. Push the notes with the commits
   (`git push origin refs/notes/chap`); the workflow fetches them. The
   repository then takes no agent commit that was not approved through the
   gate, whatever happened on a laptop.

## What a governed commit carries

The message ends with trailers:

```
CHAP-Workspace: __WORKSPACE__
CHAP-Task: tsk_...
CHAP-Agent: __AGENT_URI__
CHAP-Reviewer: __HUMAN_URI__
CHAP-Decision: approve
CHAP-Rule: any_one_approves
CHAP-Artefact: sha256:...
CHAP-Coordinator: http://127.0.0.1:8791/chap
CHAP-Chain-Head: sha256:...
```

with one `CHAP-Reviewer` line per approving reviewer. The commit is signed
with the agent's key in git's SSH format, the same Ed25519 key it signs its
CHAP calls with. The note under `refs/notes/chap` holds the artefact as
proposed and as approved, the review rule and who the final round was
addressed to, every decision of that round with its signature, the public
keys of the reviewers and of the agent as the workspace records them, and the
chain head. `git log --show-notes=chap` prints it. Notes travel separately
from commits: `git push origin refs/notes/chap` sends them and
`git fetch origin refs/notes/chap:refs/notes/chap` brings them down.

## Verify

```
node verify.mjs main..HEAD
```

checks every commit in the range with nothing but the repository: the
approved artefact's digest matches the trailer; every decision of the final
round names the task, signs the digest of what was proposed, and verifies
against the reviewer's key; an override's operations lead from the proposed
artefact to the approved one; the review rule is met by that many distinct
reviewers; the approved patch applied to the commit's parent gives the
commit's tree; and the commit's signature, where it has one, verifies against
the agent's key. `--require-signed-commit` fails a commit the agent's key did
not sign. `--coordinator http://127.0.0.1:8791/chap` also reads each task at
the gate, which must be completed with the same artefact. A commit without
trailers fails; `--allow-git-signed` lets one through when git verifies the
commit's own signature, for people's commits on a shared branch. A merge
commit is reported and passed.

## The desk

The queue lists what waits for you, what is in progress and what was
decided. A code change opens as a diff, file by file, with the task, the
agent, the model, the branch and the base commit beside it. Approve as
written, request changes with a note, reject, or press Edit on a file,
change it whole, and approve your version with a rationale: the desk writes
the patch again from your edit. Every decision carries the digest of the
artefact you saw, so a decision on a draft that changed under you is
refused. Under a multi-approval rule the queue shows the approvals so far,
and a change you have approved leaves your queue until the next round.
Activity reads the chain, with what each call did. `j` and `k` move through
the queue, `a`, `r` and `x` decide, `e` edits, and `?` lists the keys.

## Insights and analytics

The Insights view counts, from the gate's own records: how many changes were
accepted as written, approved with an edit, sent back and rejected; the time
to a first decision; how often each model's work was accepted as written;
which files reviewers edit or send back; and every override rationale and
rejection note, linked to its change. `npm run report` prints the same
figures as Markdown.

`npm run analytics` goes further with `chap-analytics`, the package that reads
a CHAP chain into documented tables. It reads the gate's store and writes,
under `analytics/`: the interactive report with its filters, charts and
briefs; the evaluation cases, each corrected change with the agent's patch
and the reviewer's, for an evaluation harness; and a refinement page with the
correction clusters ranked, the files reviewers edited with their rationales,
and every rejection note. The desk links them under Insights. It needs Python
3.10 or later and `pip install chap-analytics`; `--watch 300` keeps the pages
current, and `npm run demo` does that by itself where the package is
installed.

The loop that makes the agent better: a correction that recurs is a rule.
Put it in `AGENT_INSTRUCTIONS.md`, or in the prompt in `agent.mjs`, and watch
the cluster shrink on the next run.

## The built-in agent

`agent.mjs` takes each row of `tasks.csv` (`title`, `brief`, and a `files`
hint, semicolon separated) as one task, on one branch, one at a time: it asks
the model for the files to change, writes them into the working tree,
proposes the patch, waits, and commits an approved change through the hooks,
signed with its key. A rejection that asks for a revision has it draft again
with the note; a rejection takes the change back out. The model comes from
`ANTHROPIC_API_KEY`, `OPENAI_API_KEY` or `OLLAMA_URL`, with
`CHAP_MODEL_PROVIDER` choosing when more than one is set; with none, a
scripted drafter handles the three sample tasks and leaves a note in
`NOTES.md` for any other. A restarted agent finds its tasks by their
idempotency keys and commits what was approved and not yet committed.

It shows the whole path with nothing else installed. An assistant with its
own tools, like Claude Code, is the agent for real work; the gate is the
same either way.

## What each profile changes here

- `core/1.0`: the workspace, its members, the tasks and the chain.
- `review/1.0`: a change waits for a decision; the agent cannot approve its
  own work (`-32011`); an edit at the desk is an override with its patch and
  rationale, and the committed tree is the reviewer's version.
- `modes/1.0`: the workspace runs in trial mode with a trial ceiling, so
  every task requires review whatever the agent passes, and a task asking
  for `production` is refused (`-32040`).
- `control/1.0`: a paused agent is assigned nothing (`-32063`) until it is
  resumed.
- `security-signed/1.0`: every call after the join is signed with the
  sender's key or refused (`-32070`); the reviewers' signatures on their
  decisions are what the note carries and the verifier checks.
- `audit-scitt/1.0`: the chain is on, `audit.verify_chain` checks it, and
  the chain head at the decision is a trailer on the commit.

`npm run diff-profiles -- --against core/1.0` runs the same workload under
the configured profiles and under `core/1.0` alone and prints each call with
its outcome under both.

## What the deployment supplies

- TLS in front of the gate and the team's login in front of the desk. A
  `human:` URI in the desk is a label; the browser key under
  `security-signed/1.0` is what ties a decision to its holder, and OIDC at
  the join ties the key to a person.
- Branch protection that requires the verify check, and a push policy for
  `refs/notes/chap`, so the evidence reaches the server with the commits.
- Delivery: telling reviewers a change waits. The desk polls; nothing here
  pushes a notification.
- A key per reviewer and per agent, kept where the gate's `keys/` directory
  is not: a secrets store, an HSM or the team's identity provider.
- Keeping the chain head somewhere the gate's operator cannot change it, a
  SCITT receipt or a head published elsewhere (SECURITY.md), since whoever
  can write to the store can rewrite the log.

`tests/` and `diff-profiles.mjs` script the decisions, because nobody is at
the desk when they run; the project itself never does.
