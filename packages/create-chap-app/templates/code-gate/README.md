# __PROJECT_NAME__

A gate on the commits coding agents make. The agent works in a git
repository as it does today. Every change it wants to commit is proposed as a
patch, reviewed as a diff in the desk, decided and signed by a person, and
only then committed, by hooks that refuse anything else. The commit carries
the decision as trailers and is signed with the agent's key; the evidence
travels beside it as a git note: the agent's signed proposal, each
reviewer's signed decision as the chain holds it, the artefact as proposed
and as approved, and the chain head. A verifier checks any range of commits
against a trust policy the team keeps in the repository, and a workflow runs
it on every pull request from the base branch, so a pull request cannot
change its own check. Insights and `chap-analytics` turn what reviewers
corrected into what to change in the agent's instructions.

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
- **Edit** a file of a change in the desk, look at the patch your edit makes,
  and approve that version: the agent applies your edit before it commits,
  and the commit is recorded as approved with an edit, with your rationale.
- **Reject** the third, and the agent takes the change back out.

Then look at what was recorded, with the demo still running:

```
cd demo-repo
git log --show-signature --show-notes=chap agent/demo
node ../verify.mjs main..agent/demo --require-signed-commit
cd .. && npm run report
```

`npm run demo -- --fresh` starts again from nothing; a demo stopped while a
change waits carries on where it was. `npm test` runs the whole path
in-process with no model and no network, the ways an unapproved change could
try to land included.

## Put a repository under the gate

```
npm run keys                                     the agent's key, once
npm start                                        the gate
node install-hooks.mjs --repo /path/to/repo      the hooks
```

`install-hooks.mjs` sets `core.hooksPath` in that repository to this
project's `hooks/`, so every `git commit` there runs the gate's three hooks:

- `pre-commit` refuses the commit unless the staged change is exactly an
  approved change: approved against the commit's parent, read in the desk as
  the change git makes with it, giving the tree being committed, and not
  committed before. It then checks the approval as the verifier will, under
  the trust policy (below): the approvers, their signatures, the agent's
  signed proposal and the review rule.
- `commit-msg` sets the `CHAP-*` trailers for that approval.
- `post-commit` writes the evidence note under `refs/notes/chap`, when the
  commit made is the one approved.

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
`desk/chap-client.mjs` shows the signing. A patch the hooks accept is one the
desk reads as the change git makes: write it with `git diff --binary
--full-index --no-renames`, as `lib/git.mjs` does.

## The trust policy

Whose approvals count, and which agents may commit, is a file the team keeps
at the root of the governed repository, `chap-trust.json`:

```
npm run trust -- --out /path/to/repo/chap-trust.json
```

reads the workspace from the running gate and writes, for each reviewer
`chap.config.json` names and each agent that has joined, the public keys the
workspace records, with the review rule. It prints each key's fingerprint;
confirm them with their holders, then commit the file through a change
people review. The desk shows a reviewer their own fingerprint on the
signing badge.

The policy is what makes the evidence mean something on its own. The
verifier checks every signature against the keys the policy pins, never
against keys a note carries; it counts an approval only from a reviewer the
policy names, never from an agent; it requires the agent's proposal to be
signed by an agent the policy names; and it applies the policy's rule,
whatever rule the review itself ran under. The pre-commit hook does the same
with the policy at the commit's parent, and, in a repository with none, with
the reviewers `chap.config.json` names and the keys the workspace records.

A URI is held by whoever joined the workspace first under it, unless the
deployment ties joins to a login (OIDC, below). Pinning keys in the policy,
after confirming their fingerprints, is what closes that.

## Rolling it out to a team

1. **One gate for the team.** Run the gate as a service: `npm start` behind
   TLS and the team's login, or the production template's Compose file with
   this project's files. Name the host the proxy passes in `allowed_hosts`
   or `CHAP_ALLOWED_HOSTS`, and set `OIDC_ISSUER` to tie each reviewer's
   browser key to a person, as the production template describes.
2. **The gate project in the repository.** Commit this project as
   `tools/chap-gate` in the governed repository. Its `.gitignore` keeps
   `keys/`, `data/` and `node_modules/` out. The verifier needs nothing
   installed: it uses git, OpenSSH and Node only.
3. **One agent identity per developer.** Each developer sets `CHAP_URL` to the
   team's gate and `CHAP_AGENT_URI` to their agent's own URI, say
   `agent:claude-code@alice`, runs `node keys.mjs` once to make that agent's
   key, joins it by proposing once, and runs `install-hooks.mjs` in their
   clone. The agent's public line from `keys.mjs` goes on the code host as a
   signing key for that identity, so the host shows its commits as verified.
4. **A review rule.** `"review": { "rule": "quorum:2" }` in
   `chap.config.json` makes every change wait for two reviewers. Under a rule
   that needs more than one approval, each revision opens a new round, so an
   approval of an earlier version does not count for the revised one, and
   the desk offers no edit, since an edit settles a review with one person's
   decision: reviewers request changes and the agent revises. `all_approve`
   needs every reviewer the policy names.
5. **The trust policy.** `npm run trust` once the reviewers and agents have
   joined, then commit `chap-trust.json` at the root, as above. Add it, the
   gate project and `.github/workflows/` to CODEOWNERS.
6. **The check on every pull request.** Copy
   `.github/workflows/chap-verify.yml` into the repository and make the job
   a required check in the branch protection. It runs on
   `pull_request_target`, reads the pull request's commits as data, and runs
   the verifier and the policy from the base branch, so a pull request that
   edits them cannot pass its own check. Push the notes with the commits:
   `node tools/chap-gate/push-notes.mjs` fetches the notes already on the
   server, merges them with yours and pushes. Agent branches are pushed to
   the repository itself; a fork cannot push notes there.

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
CHAP calls with. The note under `refs/notes/chap` holds the agent's signed
submission of the change, every decision of the final review round with its
signature, the artefact as proposed and as approved, the rule and who the
round was addressed to, the keys the workspace recorded, and the chain head.
`git log --show-notes=chap` prints it.

## Verify

```
node verify.mjs main..HEAD --trust chap-trust.json --require-signed-commit
```

checks every commit in the range with nothing but the repository and the
policy: the commit's approvals come from reviewers the policy names, verify
against their pinned keys, and sign the digest of what the agent proposed;
the agent's submission is signed by an agent the policy names; the policy's
rule is met, and an edit counts only where one approval is enough; an
override's operations lead from the proposed artefact to the approved one;
the commit's parent is the commit the change was approved against; the desk
reads the approved patch as the change git makes, and it gives the commit's
tree; no other commit in the range uses the same approval; and the commit's
signature verifies against the agent's pinned key. `--trust-ref <ref>` reads
the policy as the repository holds it at a revision. With no policy given,
the verifier asks the gate in `chap.config.json` (or `--coordinator <url>`),
and checks against the reviewers the configuration names and the keys the
workspace records.

`--allow-people` lets a commit with no approval through when it is signed
by a person's SSH key that the policy lists under `people` (an
`allowed_signers` file given to `trust.mjs --people`); the agent's key never
passes that way. A merge commit fails: rebase agent branches onto their
target. `--allow-clean-merges` passes a merge whose tree is the clean merge
of its parents, with git 2.38 or later.

## The desk

The queue lists what waits for you, what is in progress and what was
decided. A code change opens as a diff, file by file, with its mode changes,
the task, the agent, the model, the branch and the base commit beside it.
Approve as written, request changes with a note, reject, or press Edit on a
file, change it whole, look at the patch your edit makes, and approve that
version with a rationale. A file is offered for editing only when the
contents that came with it agree with its patch. A patch the desk cannot
show as git would apply it (text before the first file, names that differ
between its header lines, a binary section for a text file) is marked, and
can be rejected but not approved. Every decision carries the digest of the
artefact you saw, so a decision on a draft that changed under you is
refused. Under a multi-approval rule the queue shows the approvals so far,
and a change you have approved leaves your queue until the next round.
Activity reads the chain, with what each call did. `j` and `k` move through
the queue, `a`, `r` and `x` decide, `e` edits, and `?` lists the keys.

## Insights and analytics

The Insights view counts, over every change the gate holds: how many were
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
with the note; a rejection takes the change back out. It writes only real
files inside the repository: nothing under `.git` in any letter case, and
nothing through a symbolic link. The model comes from `ANTHROPIC_API_KEY`,
`OPENAI_API_KEY` or `OLLAMA_URL`, with `CHAP_MODEL_PROVIDER` choosing when
more than one is set; with none, a scripted drafter handles the three sample
tasks and leaves a note in `NOTES.md` for any other. A restarted agent finds
its tasks by their idempotency keys, carries on with a change that was still
waiting, and commits what was approved and not yet committed.

It shows the whole path with nothing else installed. An assistant with its
own tools, like Claude Code, is the agent for real work; the gate is the
same either way.

## What each profile changes here

- `core/1.0`: the workspace, its members, the tasks and the chain.
- `review/1.0`: a change waits for a decision; the coordinator refuses the
  agent's own approval of a review addressed to people (`-32011`), and the
  gate counts no approval from an agent in any case; an edit at the desk is
  an override with its patch and rationale, and the committed tree is the
  reviewer's version.
- `modes/1.0`: the workspace runs in trial mode with a trial ceiling, so
  every task requires review whatever the agent passes, and a task asking
  for `production` is refused (`-32040`).
- `control/1.0`: a paused agent is assigned nothing (`-32063`) until it is
  resumed.
- `security-signed/1.0`: every call after the join is signed with the
  sender's key or refused (`-32070`); the signatures on the agent's proposal
  and the reviewers' decisions are what the note carries and the verifier
  checks against the policy.
- `audit-scitt/1.0`: the chain is on, `audit.verify_chain` checks it, and
  the chain head at the decision is a trailer on the commit.

`npm run diff-profiles -- --against core/1.0` runs the same workload under
the configured profiles and under `core/1.0` alone and prints each call with
its outcome under both.

## Limits

- A merge commit carries no approval: rebase agent branches. An amended
  commit is not the approved one: propose the change again.
- A binary file is shown as binary, without its content. A submodule change,
  or a text file that is not UTF-8, cannot be proposed.
- Each approval covers one commit on the commit it was approved against;
  if the branch moves on first, the change is proposed again.

## What the deployment supplies

- TLS in front of the gate, and the team's login in front of it all. A
  `human:` URI in the desk is a label; the browser key under
  `security-signed/1.0` is what ties a decision to its holder, and OIDC at
  the join ties the key to a person. The read API under `/api/` serves every
  task, its patch and its file contents to whoever reaches it, and agents
  read it too, so the login covers agents as well as people.
- Branch protection that requires the verify check, CODEOWNERS on the trust
  policy, the gate project and the workflows, and notes pushed with the
  commits.
- Delivery: telling reviewers a change waits. The desk polls; nothing here
  pushes a notification.
- A key per reviewer and per agent, kept where the gate's `keys/` directory
  is not: a secrets store, an HSM or the team's identity provider.
- Keeping the chain head somewhere the gate's operator cannot change it, a
  SCITT receipt or a head published elsewhere (SECURITY.md), since whoever
  can write to the store can rewrite the log.

`tests/` and `diff-profiles.mjs` script the decisions, because nobody is at
the desk when they run; the project itself never does.
