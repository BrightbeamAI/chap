# __PROJECT_NAME__

A gate on the commits coding agents make. The agent works in a git
repository as it does today. Every change it wants to commit is proposed as a
patch, reviewed as a diff in the desk, decided and signed by a person, and
only then committed, by hooks that refuse anything else. The commit says
who drafted it, who reviewed it and who signed it off, in four short
trailers; the evidence travels beside it as a git note: the agent's signed proposal, each
reviewer's signed decision as the chain holds it, the artefact as proposed
and as approved, and the chain head. A verifier checks any range of commits
against a trust policy the team keeps in the repository, and a workflow runs
it on every pull request from the base branch, so a pull request cannot
change its own check. Insights and `chap-analytics` turn what reviewers
corrected into what to change in the agent's instructions.

An agent that makes its own commits, in a sandbox or a loop, proposes its
branch: the desk shows it commit by commit, and once it is approved each
commit is sealed with the names and emails of the people who reviewed it
and the model that wrote it, and a pre-push hook lets only approved commits
leave the machine.

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

- **Approve** it, and the agent commits it on `agent/demo` with its trailers
  and the note.
- **Request changes** with a note on the second, and the agent drafts again
  with your note and submits the revision to the same task.
- **Edit** a file of a change in the desk, look at the patch your edit makes,
  and approve that version: the agent applies your edit before it commits,
  and the commit is recorded as approved with an edit, with your rationale.
- **Reject** the third, and the agent takes the change back out.

Then look at what was recorded, with the demo still running:

```
cd demo-repo
git log --show-notes=chap agent/demo
node ../verify.mjs main..agent/demo
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
project's `hooks/`, so every `git commit` and `git push` there runs the
gate's hooks:

- `pre-commit` refuses the commit unless the staged change is exactly an
  approved change: approved against the commit's parent, read in the desk as
  the change git makes with it, giving the tree being committed, and not
  committed before. It then checks the approval as the verifier will, under
  the trust policy (below): the approvers, their signatures, the agent's
  signed proposal and the review rule.
- `commit-msg` sets the trailers for that approval: `Drafted-by` with the
  model that wrote the change, `Reviewed-by` with each approver's name and
  email, `Signed-off-by` with the committer's, and `CHAP-Approval`, the link
  to the evidence.
- `post-commit` writes the evidence note under `refs/notes/chap`, when the
  commit made is the one approved.
- `pre-push` refuses a push that would send a commit with no approval that
  holds, each checked as the verifier checks it.

It also points the repository's `gpg.ssh.allowedSignersFile` at
`keys/allowed_signers` when nothing is set there, so `git log
--show-signature` names the agent where commits are signed with its key
(`"sign_commits": "agent"`, below). `CHAP_GATE=off git commit` commits
without an approval and says so; the commit carries no trailers, and the
verifier fails it. `CHAP_GATE=off git push` pushes without the check, as the
first push of a repository's existing history needs to. `--remove` takes the
hooks away; a repository that runs hooks from somewhere else already is
reported and left alone unless `--force` is given.

**Claude Code.** Copy `AGENT_INSTRUCTIONS.md` into the repository as
`CLAUDE.md`, or into the project's instructions. It tells the assistant
never to commit itself, to write a short context note for the reviewer,
and to run `propose.mjs` with `--wait --commit` when a change is ready,
which proposes, waits for the decision and commits an approved change
through the hooks in one step: the review loop below. So that each commit
names the model the session runs, without the assistant saying so:

```
node install-hooks.mjs --repo <path> --claude
```

(the model that wrote it, below). For the `chap.*` tools, which let it read
its tasks and the chain:

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

**The environment.** The hooks and the commands read `chap.config.json`
beside them, or the file `CHAP_CONFIG` names. `CHAP_URL` is the gate's
`POST /chap` address (by default `http://127.0.0.1:8791/chap`, from
`CHAP_HOST` and `PORT`); `CHAP_AGENT_URI` replaces the agent's URI from the
configuration, and `CHAP_AGENT_KEY` the path of its key (by default under
`keys/`). `CHAP_MODEL` names the model of the session the commands run in.
A commit made by `propose.mjs` or the built-in agent passes `CHAP_URL` and
`CHAP_CONFIG` to its hooks, so they ask the gate the command asked.

## The review loop

With the hooks in and `AGENT_INSTRUCTIONS.md` in the repository's
`CLAUDE.md`, the loop runs with no terminal of yours:

1. Claude writes its context note and runs one command: `propose.mjs`, or
   `propose-branch.mjs` under `review_at: push`, with `--wait`. The command
   starts the gate in the background when it is not running on this
   machine, and brings the review to you: a desk already open shows it at
   once, and with none open the browser opens at it.
2. Claude waits. With `--timeout 9` the wait hands back exit code 4 within
   the ten minutes a command may run, and Claude runs the same command
   again; the review stays open in the desk all the while.
3. You review. The context note says what was asked, what changed and how
   it was tested. `+` on a line comments on it, and "Show N unchanged
   lines" opens the code around a change.
4. **Approve**, and the change is committed, or the branch sealed and, with
   `--push`, pushed. **Request changes**, and the command hands Claude your
   review as a prompt: your note, your comments with the code each is
   about, and the command to run again. Claude fixes it and runs the
   command; the same review shows the revision, with what changed since you
   looked. **Reject**, and Claude gets your reasons and stops. An **edit**
   of yours is committed as yours, and Claude gets a prompt showing what
   you changed and why, to make the same correction elsewhere.

The desk shows each decision's prompt with a Copy button too, for an agent
that is not waiting on a command. `npm run stop` stops a gate started in
the background; its log is `data/gate.log`. `--no-open` (or
`CHAP_NO_BROWSER=1`) leaves the browser alone, and `CHAP_AUTOSTART=0` keeps
a command from starting the gate.

## Agents that commit on their own

Some agents commit as they go: Claude working in a sandbox, an agent
running in a loop, an assistant told to commit after each step. Their work
arrives as a branch, and the gate reviews the branch before it is pushed.
With `"review_at": "push"` in `chap.config.json` (`create-chap-app
--review-at push` writes it) and the hooks installed, the agent commits
freely: `pre-commit` lets an unapproved commit through and says so, and
`pre-push` refuses every commit that would leave without an approval.

When the branch is ready:

```
node propose-branch.mjs origin/main..agent/work --by "Claude Code" --wait --push origin
```

(`--to <branch>` pushes it under another name, and `--help` lists the
options.)

proposes the commits the branch adds as one review. The desk lists them in
order and shows each with its message, author, files and diff; `n` and `p`
move between them, and the desk marks the ones you have opened. The
approval covers every commit as it stands, so approving with commits unread
asks first, and a branch has no edit: request changes, the agent amends its
commits, and the same command proposes them again to the same review.

Once the branch is approved, it is sealed: each commit is written again
with the same tree, author and message, followed by the gate's trailers,
which name the model that wrote it, who reviewed it, who signed it off and
its place in the approved branch. Each sealed commit is signed as your own
commits are (see signing, below) and has the evidence beside it as a note. The branch then points
at the sealed commits, and `--push` pushes them through the pre-push hook
and pushes the notes after them. Without `--push`, push the branch
yourself, then `node push-notes.mjs`. The same command, run again, carries
on: it waits while the review is open, seals the commits once they are
approved, sends amended commits to a review sent back for a revision, and
pushes a branch that is sealed already. Commits at the start of the range
that are approved already stay as they are, and a new review covers the
ones after them. A branch has one open review at a time: commits made while
one waits go to it once its reviewers have decided.

A branch is one line of commits from where it leaves its base: a merge in
it is refused, with what to do. A line in a commit message that only the
gate writes (`Reviewed-by`, `CHAP-*`) is dropped from the proposal, so a
commit cannot name a reviewer the approval does not. The verifier holds
each sealed commit to the commit the reviewers saw (its tree, author and
message), to its place in the branch, and to the patch the desk showed for
it, and an approved branch is pushed whole: the pre-push hook and the pull
request check refuse part of one. The hook asks the remote which commits it
has, so a local remote-tracking ref cannot hide a commit from it. Commits
people make themselves pass the hook when they are signed with a key that
chap-trust.json, as the remote holds it, lists under `people`, and
`chap.config.json` sets `"allow_people": true`; otherwise they go through
review like any other.

## The trust policy

Whose approvals count, and which agents may commit, is a file the team keeps
at the root of the governed repository, `chap-trust.json`:

```
npm run trust -- --out /path/to/repo/chap-trust.json
```

reads the workspace from the running gate and writes, for each reviewer
`chap.config.json` names, their name and email and the public keys the
workspace records, for each agent that has joined its keys, and the review
rule:

```
{
  "chap_trust": 1,
  "workspace": "__WORKSPACE__",
  "rule": "any_one_approves",
  "reviewers": {
    "__HUMAN_URI__": { "name": "__HUMAN_NAME__", "email": "__HUMAN_EMAIL__", "keys": [{ "kty": "OKP", "crv": "Ed25519", "x": "...", "kid": "..." }] }
  },
  "agents": { "__AGENT_URI__": [{ "kty": "OKP", "crv": "Ed25519", "x": "...", "kid": "..." }] },
  "people": []
}
```

A reviewed commit names each reviewer by that name and email, and the
verifier holds it to them. It prints each key's fingerprint;
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

## Where the gate holds

The hooks run on the developer's machine, and whoever controls that machine
can switch them off: `CHAP_GATE=off`, `git commit --no-verify`, or another
`core.hooksPath`. They stop an agent working there from committing a change
nobody approved, and they record the evidence while the decision is fresh.
The boundary is the check on every pull request (step 6 below): the verifier
and the trust policy from the base branch, run over the commits the pull
request adds, with branch protection that requires the check to pass. A
commit made around the hooks fails there.

Beyond each commit's approval, the check asks three things of a pull
request:

- Its commits are one line from the point where they leave the base branch,
  with no merge among them. Each was approved on the commit it sits on, so a
  pull request behind its base passes as it is. A rebase puts each commit on
  a new parent, which no approval names, so a rebased change is proposed
  again.
- No approval in it was used anywhere in the base branch's history, so an
  approved change that landed and was reverted cannot return on an older
  commit with its old approval.
- Each approval names the review round it was made in, by the digest of the
  submission that opened the round, so approvals from before and after a
  revision do not add up to a quorum.

Merge pull requests with a merge commit. It keeps the agent's commits, their
signatures and their notes as the check verified them; a squash or rebase
merge writes new commits that carry none of them. Branch protection's
"require branches to be up to date" asks for a rebase before each merge,
which means a fresh review of every change behind its base; teams that want
each change reviewed against the code it lands on turn it on, and others
leave it off.

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
   clone. Governed commits are signed as each developer signs their own;
   with SSH signing set up for git and the key added to GitHub as a signing
   key, GitHub shows them as verified.
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
   edits them cannot pass its own check. Merge with a merge commit, as
   above. Push the notes with the commits:
   `node tools/chap-gate/push-notes.mjs` fetches the notes already on the
   server, merges them with yours and pushes. Agent branches are pushed to
   the repository itself; a fork cannot push notes there.

## What a governed commit carries

The message ends with four trailers:

```
Drafted-by: Claude Opus 5.5
Reviewed-by: __HUMAN_NAME__ <__HUMAN_EMAIL__>
Signed-off-by: __HUMAN_NAME__ <__HUMAN_EMAIL__>
CHAP-Approval: tsk_01HV...
```

`Drafted-by` names the model that wrote the change, from the agent's own
record (the model that wrote it, below), the model the built-in agent
drafted with, or the agent's URI when no model is named.
`Reviewed-by` names each approving reviewer, one line each, by the name and
email the trust policy gives, or `chap.config.json` where there is no
policy. `Signed-off-by` names the committer, as git records them on the
commit. `CHAP-Approval` names the task whose evidence the note holds, with
the commit's place (`3/9`) when it is one of an approved branch; it is what
holds an approval to one commit, in history nobody can rewrite once it is
on the main branch.

Everything else stays out of the message and in the note under
`refs/notes/chap`: the agent's signed submission of the change, every
decision of the final review round with its signature, the artefact as
proposed and as approved, the workspace, the rule and who the round was
addressed to, the keys the workspace recorded, the gate's address and the
chain head. A branch's commits share one note, which holds the branch once,
in the agent's signed submission. `git log --show-notes=chap` prints it.

**The model that wrote it.** The gate reads the model from the agent's own
harness, so nobody types it:

- Claude Code ends each commit it makes with `Co-Authored-By: <model>
  <noreply@anthropic.com>`, naming the model of that moment. The commit
  keeps that model, the line leaves the message the reviewers see, and the
  sealed commit names the model in `Drafted-by`. Each commit of a branch
  names its own model, so a branch written partly by one model and partly
  by another says which wrote which.
- `install-hooks.mjs --claude` adds a SessionStart hook to the
  repository's `.claude/settings.local.json` (kept out of git): as each
  Claude Code session starts, `claude-session.mjs` hands its model to the
  commands it runs as `CHAP_MODEL`. `propose.mjs` reads it, and so does
  `propose-branch.mjs` for commits that carry no line of their own. A model
  chosen with `/model` partway through a session reaches the commits it
  makes, through their own line, and not `CHAP_MODEL`.
- `--model` names the model where neither says, and the command says when
  no model is named.

The desk shows where the name came from beside it. Each of these is a
record of what the harness or the agent said: the agent writes its own
commit messages and runs its own commands, and the reviewer's signature
covers the name as it was proposed.

**Signing.** `sign_commits` in `chap.config.json` says how the gate's
commits are signed. `"committer"`, the default, signs them as your own
commits are signed: with your key when git is set to sign
(`commit.gpgsign`), so GitHub shows them as verified once that key is on
your account as a signing key, and unsigned when it is not, as your other
commits are. `"agent"` signs with the agent's key, which a code host
verifies only for an account that holds that key and commits under that
account's email. `false` signs nothing. The approval's own signatures are in
the note whatever this says.

## Verify

```
node verify.mjs main..HEAD --trust chap-trust.json
```

checks every commit in the range with nothing but the repository and the
policy: the commit's approvals come from reviewers the policy names, verify
against their pinned keys, and sign the digest of what the agent proposed;
the agent's submission is signed by an agent the policy names; the policy's
rule is met, and an edit counts only where one approval is enough; each
approval names the round it was made in and the workspace the policy covers;
an override's operations lead from the proposed artefact to the approved
one; the commit's parent is the commit the change was approved against; the
desk reads the approved patch as the change git makes, and it gives the
commit's tree; no other commit in the range uses the same approval; the
trailers say what the evidence says, reviewers' names and emails and the
model included, and the sign-off names the committer; and a git signature on
the commit, when it is one the policy can check, verifies.
`--require-signed-commit` also requires each commit to be signed with a key
the policy lists, the agent's or a person's. A sealed commit of a branch is
held to the commit the reviewers saw, to its place in the branch and the
sealed commit before it, and to the patch the desk showed for it, and each
place in an approved branch is used once. `--base <commit>` checks the range
as a pull request into that commit, as the workflow does: one line of
commits from where it leaves the base's history, no approval used in that
history already, and an approved branch there whole. `--trust-ref <ref>`
reads the policy as the repository holds it at a revision. With no policy
given, the verifier asks the gate in `chap.config.json` (or `--coordinator
<url>`), and checks against the reviewers the configuration names and the
keys the workspace records.

`--allow-people` lets a commit with no approval through when it is signed
by a person's SSH key that the policy lists under `people` (an
`allowed_signers` file given to `trust.mjs --people`); the agent's key never
passes that way. A merge commit fails unless `--allow-clean-merges` is
given, which passes a merge whose tree is the clean merge of its parents
(git 2.38 or later): the merge commits the host writes on the base branch,
when its history is audited with `node verify.mjs <from>..main --trust
chap-trust.json --allow-clean-merges`.

## The desk

The queue lists what waits for you, what is in progress and what was
decided. A code change opens as a diff, file by file, with its mode changes,
the task, the agent, the model, the branch and the base commit beside it.
Approve as written, request changes with a note, reject, or press Edit on a
file, change it whole, look at the patch your edit makes, and approve that
version with a rationale. Press `+` on a line to comment on it: the comments
go with your decision and into the prompt for the agent. Where a file's
whole text travelled with the change, "Show N unchanged lines" opens the
code around it. The agent's context note sits above the change, and on a
revision, what changed since you looked. A branch opens as its commits in
order, each with its message, author, files and diff, and `n` and `p` move
between them; approving it with commits unopened asks first. A file is
offered for editing only when the contents that came with it agree with its
patch. A patch the desk cannot show as git would apply it (text before the
first file, names that differ between its header lines, a binary section for
a text file), or a change that lists a file twice, is marked, and the desk
offers to reject it or request changes. Characters a reader cannot see in a
changed line (bidirectional controls, zero-width characters, a carriage
return inside a line) are shown as markers, with a warning above the diff.
Every decision carries the digest of the artefact you saw and of the
submission that opened the round, so a decision on a draft that changed
under you is refused, and the verifier counts it in its own round only.
Under a multi-approval rule the queue shows the approvals so far, and a
change you have approved leaves your queue until the next round. Activity
reads the chain, with what each call did. `j` and `k` move through the
queue, `a`, `r` and `x` decide, `e` edits, and `?` lists the keys. The text
grows with the window, from a phone to a large monitor; the two A buttons at
the top make it smaller or larger, and the desk keeps your choice.

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
hint, semicolon separated) as one task, on one branch, one at a time: it
asks the model for the files to change, writes them into the working tree,
proposes the patch, waits, and commits an approved change through the hooks,
signed with its key. A rejection that asks for a revision has it draft again
with the note; a rejection takes the change back out. It writes only real
files inside the repository: nothing under `.git` in any letter case or
under a name a file system takes for it (`.git.`, `GIT~1`), nothing inside
the gate project when the repository holds it, and nothing through a
symbolic link. The model comes from `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` or
`OLLAMA_URL`, with `CHAP_MODEL_PROVIDER` choosing when more than one is set;
with none, a scripted drafter handles the three sample tasks and leaves a
note in `NOTES.md` for any other. A restarted agent finds its tasks by their
idempotency keys, carries on with a change that was still waiting, and
commits what was approved and not yet committed.

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

- A merge commit carries no approval, so a pull request holds no merges.
  An amended or rebased commit is a new change on its parent: propose it
  again.
- A binary file is shown as binary, without its content. A submodule change,
  or a text file that is not UTF-8, cannot be proposed.
- A branch review takes up to 100 commits, and the branch's patches must fit
  in one envelope (`max_envelope_bytes`, 16 MiB here): review a longer branch
  in parts.
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
