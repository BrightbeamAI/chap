# Changelog

All notable changes to the Collaborative Human-Agent Protocol (CHAP) are
recorded here. The format follows
[Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/).

Version numbers follow
[ROADMAP.md, section "Version numbers"](./ROADMAP.md#version-numbers).
Before 1.0:

- A minor release may break things. Its entry lists every breaking change
  under a "Breaking" heading, each with a migration.
- A patch release brings an implementation into line with the
  specification, or corrects documentation. A change to what the
  specification requires is a minor release.
- The protocol packages take the specification's minor version: packages
  0.3.x implement specification 0.3. `chap-analytics` implements no part
  of the specification and has its own version numbers.

Several 0.2 patch releases changed behaviour. Their entries say so.

---

## Unreleased

### Added

- **`create-chap-app`.** `npx create-chap-app` generates a project that
  runs against the developer's own agent and data: one process owns the
  SQLite store and serves a review desk and `POST /chap`, the agent is a
  separate process attached through an MCP client, a framework bridge or
  the HTTP endpoint, the profiles are one setting in `chap.config.json`,
  and `diff-profiles` runs the project's workload under two profile sets
  and prints each call's outcome under both. Five templates: the code gate
  below; an MCP gate for Claude Desktop, Cursor and Claude Code; a support
  desk and an outbound approval gate in Python; and the Handbook's
  production set with signed
  calls, a token presented at join verified against its issuer, the chain
  on, and a `doctor`. Each template's agent waits for a reviewer on a read,
  drafts again after a rejection that asks for a revision, and finds its
  tasks again on a restart. CI generates each template, installs the
  packages it pins from npm or PyPI, and runs its tests, so a template is
  held to what the released coordinators do.
  `packages/create-chap-app/DESIGN.md` is the design.
- **The code gate.** A `create-chap-app` template that puts every commit a
  coding agent makes behind a person's decision. The agent's change is
  proposed as a patch and reviewed as a diff; the repository's hooks commit
  only an approved change, on the commit it was approved against, with four
  trailers (the model that drafted it, each approving reviewer by name and
  email, the committer's sign-off, and the approval it carries), signed as
  the committer signs their own commits, and a note under `refs/notes/chap`
  holding the agent's signed proposal, each reviewer's signed decision and
  the chain head. A trust policy the team keeps in the repository pins whose
  approvals count and the rule; `verify.mjs` checks a range of commits
  against it from the repository alone, and a workflow runs the base
  branch's verifier and policy on every pull request: its commits must be
  one line from where they leave the base branch, each approved on the
  commit it sits on, and no approval may be used twice in the base branch's
  history. Each approval names the review round it was made in, so approvals
  from before and after a revision never add up to a quorum. `npm run demo`
  runs the whole path with a built-in agent, and Claude Code, Cursor or any
  agent that can run a command proposes through `propose.mjs`. An agent that
  commits on its own has its branch reviewed before it is pushed:
  `propose-branch.mjs` proposes the commits as one review, read commit by
  commit in the desk, seals them on approval with the same trailers, and a
  pre-push hook lets only approved commits leave the machine. One command
  runs the review loop: it starts the gate when none runs, opens the review
  in the browser, waits, and hands the agent the reviewer's decision as a
  prompt, with their comments on lines; the revision shows what changed
  since the last look.
- **The desk, rebuilt.** Every template's desk shows the artefact by its
  shape (a diff for code, editable a file at a time; a letter for a
  message), lets a reviewer request changes as well as approve, edit or
  reject, reads the chain as activity, and counts in an Insights view what
  reviewers accept, edit and send back, by model and, for code, by file.
- **Analytics in every project.** `analytics.py` writes the
  `chap-analytics` report, the evaluation cases and a refinement page from
  the project's store, and the desk links them.
- **The profile explorer.** `docs/profile-explorer.md` shows, for each
  profile, the calls whose outcome changes when the profile is added, built
  from the templates' workload in both languages and checked in CI.
- **The playground drafts with any model, or none.** It reads
  `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` or `OLLAMA_URL` and drafts with a
  scripted agent when none is set, so it runs on a fresh machine without a
  model download. `CHAP_NO_LLM=1` still selects the scripted agent.

## 0.3.0: documents that match the code, refused calls on the record, and review that holds

Read the Breaking section before upgrading. The packages implement
specification 0.3. [RELEASE_NOTES_0.3.0.md](./RELEASE_NOTES_0.3.0.md)
summarises the upgrade, the security fixes and the known issues.

### Breaking

- **A token or presentation binds only to its participant.** A
  `participant.join` whose token carries a `chap_participant_uri` naming
  another participant is refused with `-32404`. A join under an existing
  member's name is accepted with a token only for the member's recorded
  subject, or, for a member with no recorded subject, with a token whose
  `chap_participant_uri` names the member (`-32404` otherwise), and with a
  presentation only from the member's recorded holder (`-32411` otherwise). A
  re-join token with no `acr` clears the member's old `acr`. Before, anyone
  holding a token or presentation the deployment's verifier accepted could
  join under an existing member's name, add a key and sign as that member.
  Migration: a deployment that binds an identity to a member after it first
  joined issues tokens carrying `chap_participant_uri`, or has its verifier add
  the claim. A member with no recorded holder cannot bind a presentation by
  joining again.
- **The routing methods and `participant.leave` need a member.** `task.route`,
  `review.depth`, `escalate.auto` and `participant.leave` refuse a caller who
  is not a workspace member with `-32011`, as SPECIFICATION 6.3.1 requires of
  every method outside its exemptions. A non-member's `task.route` reassigned
  the task, its `review.depth` and `escalate.auto` recorded decisions, and its
  `participant.leave` wrote a leave to the log, each under whatever name the
  caller gave. The refusal is not recorded. A second `participant.leave` from
  the same caller is refused, where it answered `left: true`, and the routing
  parameter schemas list `from` as required. A `from` that is not a string is
  refused by both references with `-32011` and the words `Not a workspace
  member: from is not a participant URI`, or with `-32070` before that where
  signatures are required; the Python coordinator raised out of `dispatch` on
  a list or an object `from`, and with signatures or step-up on, on a list or
  an object `workspace`. Migration: send these calls from a member.
- **`escalate.raise` applies the checks a created task meets.** The successor
  requires review when the original did, or when it is a `trial` task on a
  workspace that advertises `modes/1.0`; it never required review before, so
  escalating a task let its successor complete with no reviewer decision. An
  assignee who is paused is refused with `-32063`. The successor takes the
  mode `new_task.mode` names, or else the original's, and a mode above the
  workspace's current ceiling is refused with `-32040`; an escalation carried
  the original's mode past a lowered ceiling. Migration: completing the
  successor of a reviewed task opens a review addressed to the human members
  other than its assignee and completer, and is refused with `-32011` where
  there are none, so escalating a reviewed task to the only human member needs
  another human to join, or an explicit `review.request` naming the reviewers.
  To escalate a task whose mode is above a lowered ceiling, name a mode within
  the ceiling in `new_task.mode`.
- **`control.supersede` keeps the review requirement and refuses a paused
  assignee.** The successor requires review when the original did, whatever
  `successor_task.review_required` says; any member, the assignee included,
  could supersede a task awaiting review with a successor that needed none and
  complete it unreviewed. A paused assignee is refused with `-32063`, as at
  `task.create`. Migration: a successor of a reviewed task is reviewed, as its
  original would have been, and a participant is resumed before work is
  handed to it.
- **A paused participant is assigned no new tasks through `task.route` or
  `handoff.accept`.** The default routing policy passes over a paused
  candidate, lists it in `alternatives_considered` with the reason `paused`,
  and answers `-32510` when no candidate is a member who is not paused. A
  deployment's policy that selects a paused participant is refused with
  `-32063`, and the task keeps its assignee. `handoff.accept` from a paused
  participant is refused with `-32063`, and the handoff stays open. The pause
  binds only a participant that cooperates: any member can lift it with
  `control.resume`. Migration: resume a participant before routing work to it
  or having it accept a handoff.
- **A member's refused attempt is recorded, under `request`.** A refused call
  that is a governed attempt is an entry on the log (SPECIFICATION 10.1). A
  member's refused call is recorded unless it was malformed or invalid, failed
  a signature or key check, hit an internal error, or was answered `-32601`
  other than by the profile gate refusing a privileged method. Refusals of the
  reads, of `audit.submit_to_scitt`, of `workspace.create` and
  `participant.join`, and of a request that cannot be canonicalised are not
  recorded, and neither is any call from a caller who is not a member. So a
  decision on a review addressed to someone else is recorded, as are an
  approval whose artefact digest does not match, an act on a paused workspace,
  and `control.pause` on a workspace that has switched `control/1.0` off. A
  refusal entry holds the call under `request`, with
  `outcome: {"status": "refused", "code": …}` beside it, and its chain link
  hashes the outcome together with the request, so altering either breaks the
  chain. `audit.verify_chain` reports an entry as malformed unless it holds
  exactly one JSON-RPC call, under `envelope`, or under `request` with a valid
  outcome. An entry with no outcome hashes exactly as before, so every
  existing chain still verifies. `audit.read` returns refusal entries and takes
  an `outcome` filter of `accepted` or `refused`, and statements from
  `audit.submit_to_scitt` carry the record the link hashes. In the TypeScript
  package `AuditEntry.envelope` is optional, and `entryCall`, `entryRecord`,
  `isRefusal` and `linkHash` are exported for readers. Migration: a reader
  that takes `envelope` from every entry allows for entries without one, reads
  the call with `entryCall`, or asks `audit.read` for
  `filter.outcome: "accepted"`. Back up a Python store before going back to an
  earlier release: an earlier release skips a workspace whose log holds a
  refusal, and the next call naming that workspace re-creates it empty and
  overwrites the stored log.
- **A signed copy of a recorded refusal is answered with that refusal.** It
  carries `data.refused_at_seq` and is neither evaluated nor recorded again,
  so a refused request read from the log cannot be sent later to take effect.
  A signed copy of a call that took effect is evaluated, and its refusal is
  not recorded. Both rules compare what the signer signed, the request without
  its `sig`, so re-encoding a signature does not make a new request. Unsigned
  calls are not compared with the log. Migration: a retry is a new request
  with a new `id`.
- **Calls are checked in the order SPECIFICATION 10.1 fixes.** A method that
  does not exist and a request that cannot be canonicalised are refused
  before the signature, the profile gate and the pause, so on a paused
  workspace they answer `-32601` and `-32602` where they answered `-32063`. An
  empty method is `-32600` in both references, and the TypeScript reference
  answers a method that is not a string with `-32600` where it failed with an
  internal error. A request nested deeper than 64 levels is refused with
  `-32600`; the two coordinators failed on it at different depths, after the
  call had taken effect. Migration: read `-32601` and `-32602` from a paused
  workspace as errors in the call itself.
- **A workspace serves the methods of the profiles it advertises.** A method
  whose owning profile the workspace does not advertise is refused with
  `-32601`, the answer a coordinator that never implemented it would give,
  with `data: {"profile": …, "advertised": [...]}` for an operator.
  SPECIFICATION 15.4 has required this since 0.1 and neither reference did, so
  removing `control/1.0` from a workspace left its emergency brake fully live.
  The reads (`workspace.describe`, `audit.read`, `audit.verify_chain`,
  `audit.verify_receipt`) and the key lifecycle (`participant.rotate_key`,
  `participant.revoke_key`, now in Core) are always served. Migration:
  advertise every profile whose methods a workspace calls, at
  `workspace.create` or with `workspace.set_profiles`.
- **`workspace.create` refuses a descriptor that understates enforcement.** A
  coordinator requiring signatures adds `security-signed/1.0` to the
  advertised profiles, and one with a token verifier adds
  `identity-oidc/1.0`. Advertising either without the matching enforcement is
  refused with `-32602`. Advertising still turns no enforcement on. Migration:
  advertise `security-signed/1.0` or `identity-oidc/1.0` only on a coordinator
  that enforces it.
- **`task.update` opens no review and reaches no pause.** `review_requested`
  as a target is refused with `-32602`: it left the task under review with no
  review, so `decide.approve`, `decide.reject` and `abstain.declare` then
  failed with `-32603` and `decide.override` with `-32602`. `paused` as a
  target is refused too, since `control.resume` was the only way out and a
  workspace advertising `core/1.0` alone could hold a task it had no method to
  lift. `paused` to `cancelled` through `task.update` is unchanged, so a task
  paused by an earlier version can still be closed. Migration: open a review
  with `review.request`, or with `task.complete` on a task that requires one,
  and pause with `control.pause`.
- **Fields of the wrong type are refused with `-32602` in the same words by
  both references.** `task.create` and `control.supersede` refuse a `mode`
  that is not a string and a `review_required` that is not a boolean,
  `task.route` refuses `candidates` that are not a list, and `escalate.raise`
  refuses a `new_task` that is not an object. The references read such values
  differently: a list or object `review_required` was true in TypeScript and
  false in Python, and Python iterated a `candidates` object and reassigned
  the task where TypeScript refused it, so the same call left different
  chains. An empty `successor_task.mode` falls back to the superseded task's
  mode in TypeScript as it did in Python. Migration: send `review_required` as
  `true` or `false`; `null` still counts as `false`.
- **A signed call whose `ts` is not a string is refused with `-32070`.**
  TypeScript chose a key for it anyway, and Python raised out of `dispatch`.
  An empty `ts` is a time no key covers, and Python answers it with `-32071`
  as TypeScript does. Migration: send `ts` as an RFC 3339 string, or omit it.
- **An explicitly empty selection list is refused with `-32602`.** This covers
  `accepted_task_ids: []` on `handoff.accept`, `include: []` on
  `control.snapshot` and `what_to_restore: []` on `control.rollback`. Each wrote
  an audit entry for an event that did not happen, and the references
  disagreed on them. Migration: omit the field to select everything, or name
  a non-empty subset.
- **`control.snapshot` returns the `Artefact` shape.** The result is the shape
  [`chap-task.schema.json`](schemas/core/chap-task.schema.json) defines: `id`,
  `kind`, `produced_by`, `produced_at`, `content_hash` and inline `content`,
  with `content_hash` the SHA-256 of the JCS bytes of `content`. Each `include`
  slice has one documented projection, absent and empty optional fields are
  omitted, and `control.rollback` reads the captured `content.state`. Records
  written by an earlier version are normalised when a store loads them.
  Migration: read a snapshot's captured state from `content.state`.
- **`control.resume` restores the state the task held at the pause.** It set
  `in_progress` unconditionally, so a task paused during a review came back
  with the review attached and no way for a reviewer to act. A task paused
  before work began resumes to `created`. Where no state was captured, as in a
  snapshot written before this release, the result is still `in_progress`.
  Migration: read the restored state from the result's `state`.
- **The schema identifiers carry 0.3.** Every schema's `$id` is
  `https://chap.dev/schemas/0.3/…`, and the method catalogue's `version` is
  `0.3`. Migration: point `$ref`s at the 0.3 identifiers.
- **`whisper.answer` is recorded as the client sent it.** Both references
  wrote the whisper's `task_id` into the request after the signature had been
  verified, so the recorded envelope no longer verified under its own
  signature. The answer's task is resolved from the whisper at read time, so
  an `audit.read` filtered by task still returns the answer with the ask.
  Migration: take an answer's task from its whisper, which the answer names by
  `whisper_id`, or filter `audit.read` by `task_id`.
- **`audit.submit_to_scitt` is a read.** It was recorded, so submitting the
  chain appended to the chain being submitted and moved its head, and the
  receipt attested a log one entry shorter than the workspace then held. A
  submission now leaves no entry on the log. Migration: keep the receipts the
  method returns as the record of what was submitted.
- **SCITT statements carry `version=0.3`.** The `content-type` in a
  statement's protected header is `application/chap+json;version=0.3`.
  Migration: a verifier that matches the content type accepts `version=0.3`.

### Specification

- **What advertising a profile does.** New 6.5 is a normative table of what
  advertising each profile does. Advertising had grown three meanings:
  changing behaviour, doing nothing while a separate option did the work, and
  doing nothing at all. 15.4's dispatch rule follows from the table as a
  per-method rule.
- **Two requirements no reference met move to SECURITY.md.** Refusing a
  repeated envelope `id` with `-32701`: the size of a seen-id set is a
  deployment decision, and `idempotency_key` already covers the retry case it
  would break. Refusing a non-monotonic `ts` with `-32401`: 4.3 said strictly
  monotonic and 15.4 said non-decreasing, and millisecond precision makes the
  strict rule refuse a participant that sends twice in one millisecond. The
  chain is ordered by arrival and `prev_hash`, and 15.1 says so.
- **15.1 is grouped by who does the work.** Signature verification and
  step-up are turned on by a profile, TLS and delivery filtering belong to the
  deployment, and a method's `required_scope` is declared and enforced by
  neither reference. 11.3's shadow-observer requirement moves with them.
- **The specification, the profiles and the conformance documents describe
  what both coordinators do.** Among the corrections:
  - Rotating a key ends the old key at once; there is no grace window
    (SPECIFICATION §5.7).
  - Signatures are checked only where the Coordinator requires them, and never
    on `workspace.create` or `participant.join` (§5.2, §15.1).
  - Step-up covers every privileged method when the Coordinator enforces it,
    and the catalogue's `privileged` flags match them (§5.6, §12).
  - `review/1.0` supports `any_one_approves`, `all_approve` and `quorum:<n>`;
    weighted rules belong to `deliberation/1.0` (§8.3, profiles/review.md).
  - A copy of an accepted call is evaluated again; only a signed copy of a
    recorded refusal is answered with that refusal (§15.4).
  - Routing decisions are held in workspace state and returned to the caller,
    and the log records the request (profiles/routing.md).
  - `conformance/test-vectors.md` §1 carries the RFC 8032 signature, and §3
    shows a chain both coordinators record.
  - `profiles/audit-scitt.md` cites the SCITT architecture as RFC 9943.
  - SPECIFICATION §4.1 and §5.2 point to core/SPEC.md §2 for the messages the
    coordinators send today; milestone 0.4 settles the format.

### Added

- **One catalogue, generated into both references.** Which profile owns which
  method is stated once, in `chap-methods.schema.json`, and
  `scripts/sync-method-catalogue.mjs` writes the table each reference
  dispatches against. CI fails when a committed copy is stale.
- **Shared vectors for the profile gate and for refused calls.**
  `conformance/profile-gate-vectors.json` fixes the exact response for
  refusals and calls that pass the gate.
  `conformance/refusal-record-vectors.json` fixes, for each refused call, the
  response, whether it is recorded, the recorded entry and the chain head
  after it; the Python suite recomputes every head with a canonicaliser of its
  own, and harness vector `rv-13` checks the same rule over HTTP.
- **An error-code check.** Every code the normative tables allocate must exist
  in both references, and a code in one reference alone is a divergence. Run
  in CI.
- **A wrapped call can name the decision it carries out.** The wrap helpers
  take an optional `fulfils`, which records the id of the authorising decision
  on the result artefact. SPECIFICATION 9.4 defines the field: it is asserted
  by the producer and not verified by the Coordinator, so a wrong id is a
  dangling reference the chain still verifies.
- **A roadmap to 1.0.** [`ROADMAP.md`](./ROADMAP.md) sets out what 1.0 will
  promise, the known gaps and the milestone that closes each one, the
  milestones in order with the checks that finish them, and the open
  questions.
- **A licence file for the specification text.** `LICENSE-SPEC.md` names the
  files licensed under CC BY 4.0; everything else, code included, stays under
  Apache 2.0. Contributions made to those files before 0.3.0 also remain
  available under Apache 2.0. A contribution takes the licence of the file it
  changes (CONTRIBUTING.md §7).
- **Versioning rules, written down.** The header of this file, GOVERNANCE.md
  §4 and CONTRIBUTING.md §5 follow ROADMAP.md: before 1.0 a minor release may
  break things and lists each break with a migration, and a patch release
  brings an implementation into line with the specification or corrects
  documentation.
- **IMPLEMENTATIONS.md says what backs each claim.** The packages are labelled
  Beta, and a column names the run each claim was tested with.
- **Editorial notes in GOVERNANCE.md and CONTRIBUTING.md** point to
  MAINTAINERS.md for how decisions are made today.
- **Wider differential fuzzing.** The fuzzer pauses and resumes participants,
  lowers the mode ceiling, has members leave and join again, creates reviewed
  tasks, and sends calls that fail the membership floor, so those paths are
  compared across the two references.

### Fixed

- **The guides describe what the coordinators do today.** The README tour
  switches the hash chain on, HANDBOOK says to run one active coordinator per
  workspace because two writers lose entries, the IN_PRACTICE samples use only
  calls both coordinators accept, and features that are specified and not yet
  built are marked as such.
- **SECURITY.md is one threat model that matches the specification and both
  coordinators.** It replaces text that described an older message format, a
  different chain formula, a key grace window and coordinator-signed
  checkpoints, none of which exist. It sets out what each option switches on,
  what the deployment supplies, which releases receive fixes and the known
  limitations.
- **A TypeScript coordinator started on a store brings back every workspace.**
  It restored the stored records one at a time, each restore replacing the
  last, so only one workspace came back, and the next call naming a lost
  workspace re-created it empty and overwrote its stored log.
- **Every typed collection on a workspace is rebuilt when a store is loaded.**
  `overrides` and `route_decisions` came back as plain dicts in the Python
  reference, which broke the first attribute read after a restart.
- **A captured snapshot no longer follows live state.** `control.snapshot`
  held references to the workspace's own mutable structures, so later changes
  reached the saved snapshot. The capture is copied, and restored scopes are
  copied back, so the content and its hash stay as they were at the capture.
- **`audit.read` reports where the next read starts.** A `range.to_seq` past
  the end of the log came back as `next_seq`, so a reader paging forward from
  it skipped every entry written after its read. `next_seq` is the length of
  the log in that case.
- **The two references refuse the same JSON Patch in the same words.** Python
  quoted with apostrophes and named Python types, TypeScript quoted with
  double quotes and named JavaScript types. Both now speak JSON, and a shared
  vector file holds every message.
- **`remove` and `replace` at the position after the last array element are
  refused in both references.** RFC 6902 admits `-` for `add` alone.
  TypeScript compared the token numerically, so `remove` deleted the first
  element and `replace` reported success without changing anything, and the
  same `decide.override` produced a different corrected artefact in each
  reference.
- **`whisper.answer` matches an option the same way in both references.** A
  null `answer_option` is refused with `-32602`, an option id matches only an
  equal id of the same JSON type, and the refusal shows the option as JSON. An
  empty answer is no answer in both, and `whisper.ask` refuses `options` that
  are not a list.
- **`workspace.describe` answers the same on both references.** Python sent
  `evidence_head: null` where TypeScript omitted the key. A workspace with no
  chain has no head, and both omit it.
- **The workspace descriptor schema describes the descriptor.** It required
  fields neither reference sends and declared none of `profiles`,
  `audit_count`, `task_count`, `override_count` or `routing_policy_uri`, which
  both send. A test in each reference now holds the two together.
- **The `chap-conformance` GitHub Action runs.** It passed a `--profiles`
  argument the harness refuses, so every run failed before testing anything.
  The `profiles` input is replaced by `core-only`, the default `ref` is the
  release the action ships in, and the inputs reach the script through the
  environment.
- **`reference/core-plus-review` holds every actor to membership** and keeps
  the review requirement on escalation. It checked membership on
  `review.request` and the review decisions alone, so a non-member could
  create, update and complete tasks, escalate them and write a leave.
- **The reference servers keep the log rules.** `reference/core` and
  `reference/core-plus-review` recorded reads, which core/SPEC.md 3.2 forbids.
  Both leave reads off the log and accept the `outcome` filter, and
  `reference/core-plus-review` records governed refusals and answers a
  resubmitted refusal as the coordinators do.
- **The authorisation walkthrough runs.** It passed `confidence` as `0.82`,
  which the canonical number rule refuses, so it stopped at `task.complete`.

---

## 0.2.13: the review gate closed on both routes, and descriptions that match the code

**Behaviour change.** `task.update` with `state: "completed"` is refused with
`-32602` on a task that requires review. `in_progress → completed` is otherwise
legal and carried no review check, so the gate `task.complete` enforces could be
walked around in a single call. Code that completed a review-required task this
way has to submit through `task.complete` and let a reviewer decide. Tasks that
need no review are unaffected, as is every other `task.update` transition.

**Schema change.** The MCP parameters `confidence`, `max_cost_usd`, `weights`
and `weight` were declared `type: "number"`, which no caller could satisfy:
canonicalisation admits integers only, so any fractional value was refused at
ingress. They are now `string` or `integer`. A client that was sending
`confidence: 0.86` was already failing; it now has a schema that says so, and
`"0.86"` works.

### Fixed

- **A required review is addressed to people.** `task.complete` on a task whose
  review is required opens a review and selects the reviewer set. That set
  excluded the completer and the assignee but nobody else, so in a workspace
  with more than one agent the others were eligible and an agent could record a
  `decide.approve` on another agent's output. The set is now the `human`
  members other than the completer and the assignee, and a workspace with no
  eligible human refuses the completion with `-32011`.

  An explicit `review.request` keeps whatever `to` it was given, so an
  agent-reviews-agent flow remains available where it is asked for. A trial-mode
  workspace under `modes/1.0` needs at least one human who is neither the
  completer nor the assignee. Harness vector `rv-12` covers it.

- **`review.request` refuses a stopped task.** It had no precondition on task
  state, so a request revived a `cancelled` or `superseded` task and pulled a
  `paused` one back into play. Those three are now refused with `-32010`,
  matching the allowlist `task.complete` already carried.

  `completed` remains legal: completing a task and then requesting review of
  its output is how the framework bridges submit a draft. Nothing that worked
  before stops working except reviving stopped work.

- **`task.update` cannot complete a task that requires review.**
  `in_progress → completed` is a legal transition and carried no review check,
  so the gate that `task.complete` enforces could be walked around in one call.
  The task finished carrying no artefact and no `decide.*` reached the chain,
  which is the single failure `review_required` exists to prevent. The
  transition is now refused with `-32602` and the message names
  `task.complete`. Tasks that need no review are unaffected, as is every other
  `task.update` transition.

- **`review.request` accepts the documented widen path.** Adding a reviewer to
  an open review requires re-requesting the same artefact. The rule was
  compared after the default had been applied, so omitting `rule` on the second
  request read as a change of rule and was refused with `-32014` on any review
  not opened under `any_one_approves`. Only a rule the caller actually supplied
  now counts as a change.

- **Tool and parameter descriptions match the implementation.** An audit of the
  MCP schema table against both coordinators found nineteen descriptions that
  named behaviour the code does not have. Among them: `control.pause`
  `in_flight_policy` is recorded and never acted on; `scope: "participant"`
  blocks new assignment rather than stopping work in flight;
  `control.rollback` restores only `mode_ceiling` and `members`;
  `deliberate.vote` `weight` is not read by the tally; `deliberate.open`
  `deadline` does not close the vote; `audit.read` has no tag filter;
  `control.snapshot` `label` is not a rollback target; and `escalate.raise`
  gives the successor an empty input rather than the original's. Each now
  states what happens, and names the error code where a constraint is enforced.

- **Fractional parameters are typed as decimal strings.** `confidence`,
  `max_cost_usd`, `weights` and `weight` were declared `type: "number"`, which
  no caller can satisfy: §7 admits integers only, so any fractional value was
  refused at ingress with `-32602`. They are now `string` or `integer` as the
  case requires. The same error ran through the documentation, including a
  runnable `curl` in the five-minute start and the JCS vector in
  `conformance/test-vectors.md`, whose sample envelope carried `0.42` and whose
  stated canonical bytes were neither sorted nor whitespace-free. The vector is
  recomputed and agrees byte for byte across both implementations.

### Changed

- **SPECIFICATION.md §8.1 describes the implemented state machine.** The
  lifecycle table routed a new task through `task.assign`, `task.accept` and
  `task.start`, and through the states `assigned` and `accepted`; no
  coordinator implements those methods and neither state is in `TaskState`. The
  table also omitted every transition `task.update`, `control.*`,
  `abstain.declare` and `escalate.raise` perform.

  The replacement is exhaustive over the ten task states, and both coordinators
  are checked against it by a test that drives a task into each state and
  attempts every state-changing method: a transition the table permits must
  work, one it does not list must be refused. `task.assign`, `task.accept`,
  `task.decline`, `task.start`, `task.progress` and `task.describe` are marked
  reserved in §12.3. The `assigned` and `accepted` states are removed from
  `schemas/core/chap-task.schema.json`, which also gains the missing `paused`.

  The lifecycle diagrams in ARCHITECTURE.md, `profiles/review.md`,
  `core/SPEC.md` and `diagrams/task-lifecycle.mmd` are corrected to match, and
  the conformance checklist's Core rows now reference the table rather than
  restating a subset of it.

### Added

- **Every MCP tool parameter carries a description.** 64 of the 192 parameters
  on the 39 tools had a name and a type and nothing else, so a client deciding
  whether to call `chap.deliberate.vote` or `chap.control.rollback` had to guess
  what five of its seven arguments meant. All 195 now say what they are for, and
  `chap.task.create` gains the `review_required`, `deadline` and
  `idempotency_key` parameters the coordinator has always accepted but the
  schema never advertised.

  The Python MCP transport says it mirrors `schemas.ts` exactly and had drifted
  to 87 differing descriptions. Its table is now generated from the TypeScript
  one by `scripts/sync-mcp-schemas.mjs`, and CI fails when the committed copy is
  stale or when any parameter is left undescribed. The tool-level descriptions
  in `mcp_tools.py` are generated from `tools.ts` the same way, since they had
  drifted too: `chap.task.complete` still told callers to follow it with
  `chap.review.request`, which 0.2.12 made wrong.

- **A front door.** [`START_HERE.md`](./START_HERE.md) and
  [`start-here/`](./start-here/) take a new developer from a clone to one
  recorded human decision with Python 3.10 and nothing else: no install, no
  npm, no Docker, no model, no API key. One command opens a local review desk
  where an agent's draft can be approved, edited or rejected, with the
  envelopes, the patch and the chain verdict on screen. The folder also carries
  a copyable `ReviewGate` helper, three terminal demos and a dependency-free
  Node client.

  The desk renders bidi and zero-width characters visibly before a reviewer
  decides. A digest binds a decision to content, not to what the decider saw,
  and a reviewer surface is responsible for the difference.

  The starter adds nothing to the wire format and no new package.

- **One release version, checked.** `scripts/check-versions.mjs` holds the
  release version to the root `package.json` across the forty-eight places it
  is written: nine manifests, their cross-dependency pins, `server.json`, the
  version each server reports to its client, the reference servers and the
  documentation tables. It found `cli.ts` carrying a version constant of its
  own. A pattern that stops matching is a failure too, so the check cannot
  quietly stop covering a file. CI runs it, so no tag can carry a partial bump.

### Packaging

- **`zod` is declared rather than bundled.** `coordinator-mcp` imports `zod` to
  build one request schema for the MCP SDK, but never declared it, so the
  bundler inlined the whole library into all four entry points: 598 KB each and
  a 1 MB tarball. Worse, the inlined copy is a second `zod` instance, and the
  schemas built with it are handed to the SDK, which validates against its own
  copy. It is now a dependency on the range the SDK asks for, and external to
  the bundle. Entry points are 50 KB and the tarball is 205 KB.

---

## 0.2.12: review rules, modes gating, and an envelope ceiling

**BREAKING for `modes/1.0` workspaces.** `task.complete` on a trial-mode task
no longer completes it. Trial forces review, so the call now opens a review and
the task moves to `review_requested`; a reviewer decision completes it. Code
that expected `completed` back from `task.complete` on a trial task has to
follow the review. Workspaces that never loaded `modes/1.0` are unaffected,
which is itself part of the fix.

### review_required is enforced, and modes stops leaking outside its profile

`review_required` was written in three places and read by neither coordinator,
so a task whose review was mandatory completed with unreviewed output and no
`decide.*` recorded. It is now enforced: `task.complete` opens the review that
`review.md` §3.1 always described.

Enforcing it exposed the reason nobody had noticed. New workspaces defaulted to
`mode: "trial"`, and trial forcing ran with no check that `modes/1.0` was
loaded, so every default workspace, including Core-only ones, silently forced
review on every task. `modes.md` declares the profile "Depends on: Core" and
the descriptor examples in SPECIFICATION.md use `"mode": "production"`, so this
was a defect rather than a default. Mode semantics are now gated on the profile
being loaded (#93, #97).

The implicit review excludes the task's producer and assignee. Without that, a
single-member workspace would have the producer approving its own output, and
the chain would carry a `decide.approve` that looked like oversight. With
nobody eligible the completion is refused with `-32011` rather than opening a
review only its author could decide.

`decide.approve` now evaluates `review.rule` rather than completing on the
first approval regardless (#99).

### The reference server catches up

`reference/core-plus-review` is a from-scratch reimplementation sharing no code
with the packages, and it had none of the above. It now honours
`review_required` on `task.create`, opens the review from `task.complete` with
the same eligible-reviewer rule as the coordinators, and refuses with `-32011`
when nobody qualifies. `review_required` and `pending_artefact` are declared on
its `Task` interface; `pending_artefact` was used in five places, four of them
through `as any` casts, which are now gone.

### The conformance harness pins its workspace mode

The harness never created a workspace, relying on `participant.join` to
auto-create one, so it inherited each target's defaults. Those differ: the
Python reference loads every profile and defaults to trial, while the
Core+Review TypeScript reference has no modes at all. Once trial started
forcing review, `cm-08` failed against Python and passed against TypeScript,
and the two targets had not been comparable for some time. The harness now
creates its workspace in production mode before any vector runs. Both
references pass all 26.

### Also

A maximum envelope size is enforced and published (#108), with request body
size and JSON depth capped in the reference TypeScript servers (#111).
`whisper.*` requires workspace membership (#105). `superseded` is terminal in
`control.pause` and `control.cancel` (#101). JSON Patch array indices parse
through one strict shared rule (#103). Signature verification fails closed with
`SIG_VERIFY_FAILED` on an exception (#116). The threaded Python reference
server serialises dispatch (#113).

---

## 0.2.11: coordinator-mcp only, a namespace casing fix

`@brightbeamai/chap-coordinator-mcp` only. The coordinator and A2A packages
are unchanged and stay at 0.2.10; republishing identical code to keep version
numbers in step would be noise.

The MCP Registry grants a GitHub organisation namespace using the
organisation's own casing, `io.github.BrightbeamAI/*`. `server.json` and
`mcpName` both used lowercase, so the publish was refused twice: first 403
for claiming a namespace that was not granted, then 400 once the claim was
corrected, because the registry proves npm ownership by reading `mcpName`
out of the published package and 0.2.10 carries the old value. npm does not
allow a published version to be replaced, so the corrected `mcpName` needs a
new version.

No behaviour change. The only differences from 0.2.10 are `mcpName`, the
version, and the version string the server reports in its banner and
`serverInfo`.

---

## 0.2.10: MCP registry entry, and two verdicts that changed

Published 2026-08-20. **Two calls behave differently from 0.2.9 and `^0.2.9`
resolves this release automatically.** If you pin that way, read the next two
paragraphs before upgrading.

`review.request` on a task that already has an open review with different
content now returns `-32014` instead of quietly replacing the artefact under
review. `audit.verify_chain` now returns `ok: false` with `status:
"not_evaluated"` where it previously returned `ok: true`, whenever any part
of the log lies outside the chain. Both were reported as defects, both fail
closed, and both are described in full below.

The profile identifier stays `audit-scitt/1.0`. That is a deliberate choice
rather than an oversight: profile surfaces are expected to move before 1.0
and 0.2.9 changed this same profile without a bump, so a bump now would imply
a stability guarantee the 0.x line does not offer.

### The MCP server is launchable

`@brightbeamai/chap-coordinator-mcp` exported `makeChapMcpServer` and had no
`bin`, so it was a library that `npx` could not start. Directories and the
official registry list servers a client can launch, so listing it required an
entrypoint.

`npx -y @brightbeamai/chap-coordinator-mcp` now starts a stdio server exposing
all 39 CHAP methods as MCP tools. Two environment variables: `CHAP_DB_PATH`
for a SQLite file, and `CHAP_PROFILES` to override the profile set. Without
`CHAP_DB_PATH` the coordinator runs in memory and workspaces are lost when the
client exits, which suits a trial and does not suit real decisions. If the
path is set and the store cannot be opened, the server exits with the reason
rather than starting in memory and silently discarding what it was asked to
keep.

The package's peer dependencies became real dependencies. Peers are correct
for a library and wrong for something `npx` launches, where neither the
coordinator nor the MCP SDK would resolve.

New workspaces get `audit-scitt/1.0` by default, so their chain starts at the
first entry. A workspace that adds the profile later can never chain-verify
what came before it.

`server.json` at the repository root carries the registry metadata.


### `audit.verify_chain` no longer passes over what it did not check

A workspace can enable chaining part-way through its life. Verification then
replayed from the first chained entry, which is correct, and reported `ok:
true` with a smaller `entries_checked` beside it, which is not. Four entries
with three written before chaining returned a pass having checked one. The
coverage number was present and sat next to the pass, so a reader took the
pass.

The verdict is now one of three terminal outcomes, mutually exclusive. A
broken chain stays a JSON-RPC error. A log with entries outside coverage
returns `status: "not_evaluated"` with `ok: false` and `reason:
"unchained_prefix"`. `verified` requires complete coverage. `ok` is `true`
only alongside `verified`, so a caller reading `ok` alone now fails closed
where it previously passed.

The result also carries `entries_total`, `entries_unchecked` and
`checked_from_seq` next to the existing `entries_checked`, so a verdict names
the range it evaluated instead of leaving the reader to infer it.

Two things this does not change. A workspace that never enabled chaining is
still refused outright rather than answered, because there is no chain to ask
about. And tampering in a covered entry is still an error, never downgraded
to `not_evaluated`.

This came out of the IETF SCITT thread, where Henri Sirkkavaara and Iman
Schrock converged on `NOT_EVALUATED` as a terminal verdict after 1F916
Maintainer reported four cases of a row that was never written passing every
instrument they had. Checking CHAP against that discussion found the same
gap here. Tracked as
[#76](https://github.com/BrightbeamAI/chap/issues/76).

Two supporting corrections went with it. The declared TypeScript type
`AuditVerifyChainResult` described `valid` and `breaks`, neither of which any
handler has ever returned; it now matches the wire. And the regression test
`a chain enabled mid-life verifies from the first chained entry` asserted the
old pass in both languages, so the gap was not an oversight but a documented
expectation, now corrected in place.

Two related honesty fixes went with it. `from_seq` and `to_seq` are declared
on the request and honoured by neither implementation; supplying either now
returns an error rather than silently answering the whole-log question with
whole-log counts. And a present-but-empty `chain_head`, reachable through a
store restore, was read as absent in Python and as a value in TypeScript, so
the two returned different verdicts for the same state; both now treat it as
a value.

`profiles/audit-scitt.md` §6.1 records what this means for a workspace that
adopts the profile late: `prev_hash` is written at append time and nothing
back-fills it, so the verdict for such a log is permanently `not_evaluated`
and assurance over the historical range has to come from receipts, not from
the chain.

Normative text in SPECIFICATION.md §10.2, vectors `av-01` to `av-05` in
`conformance/test-vectors.md`. Nine mirrored unit tests per language, and the
two implementations answer every probe byte-identically.

### Unreleased features are marked as such

`main` documents `approved_artefact_digest`, the open-review guard, and error
codes `-32014` and `-32074` across five documents. None of them exist in any
published package: the newest release is 0.2.9, which predates all of it. A
reader following the specification while running the published packages would
have found the behaviour missing and had no way to tell why.

Those sections now carry an explicit unreleased marker, including the
conformance vectors `rv-09` to `rv-11`, which a coordinator built from 0.2.9
cannot pass and should not be expected to. The markers come out when the work
ships.

### Documentation consistency

- **The 0.2.7 changelog section is restored.** 218 lines covering the four
  framework bridges, the scenarios directory and the cross-implementation
  fixes were lost between 9e7af2b and the 0.2.9 work, and recovered verbatim.
  A 0.2.8 entry is added; that release published the 0.2.7 line and carried no
  change of its own.

- **The error registry matches the implementations.** SPECIFICATION.md §13.2
  assigned overlapping bands, which produced §13.3 giving `-32600`, `-32601`
  and `-32602` two meanings each and naming errors neither coordinator
  returns. §13.2 now allocates one decade per profile with no overlap, and
  §13.3 carries only the five JSON-RPC codes, treating each profile's own
  table as authoritative rather than mirroring 49 codes that would drift again.

- **`identity-vc/1.0` error codes corrected.** The profile assigned `-32411`
  to issuer trust and `-32413` to claim disclosure, while both implementations
  use them for holder binding and unknown schema. The table now matches what
  is returned, and the two conditions that exist only on paper are recorded as
  unimplemented rather than deleted.

- **One name for the proposal process.** CONTRIBUTING called it "CHAP
  Improvement Proposals" in one place and "RFC-style proposal" in three
  others, while GOVERNANCE defines CEPs. All now say CEP, and CONTRIBUTING
  points at `ceps/` and the governing section.

- **`MAINTAINERS.md` exists.** SECURITY, GOVERNANCE and CODE_OF_CONDUCT all
  referred readers to a file that was not there. It records the present
  position plainly: two maintainers, with protocol decisions resting with
  Arsalan Shahid, and the Steering Committee and Working Groups in
  GOVERNANCE §2 described as the structure intended for standards-track
  promotion rather than today.

- **Em-dashes removed from shipped non-markdown files**, and the CI check
  widened past `*.md` to the source, schema and UI files where they had
  accumulated.

- **A CI guard for bare `require()` in ES modules.** Three faults this release
  had the same cause, and the guard immediately found a fourth: the
  corrupt-row recovery suite had been skipping silently for the same reason as
  the storage suites.

### Storage contract and verification limits (#69)

- **The store contract no longer claims optimistic concurrency.**
  `WorkspaceRecord.version` was documented as "monotonically increasing for
  optimistic concurrency" in both languages while every `save` was an
  unconditional upsert, and `sqlite.py` two files away described the same
  component as single-writer. The field is recorded but not enforced, and the
  docstrings now say so.

- **The single-writer requirement is written down.** SPECIFICATION.md §10.3
  states that running more than one Coordinator against a shared store is
  unsupported, what happens if you do, and how to serialise writes above the
  Coordinator instead.

- **Verification proves consistency, not completeness.** §10.2 now records
  that a chain which lost entries and was re-linked verifies exactly as one
  that lost none, so `audit.verify_chain` returning `ok` does not mean nothing
  is missing. Detecting absence needs a witness outside the chain, which is
  what `audit-scitt/1.0` anchoring provides.

- **The SQLite store suites now actually run.** Their availability probe used
  a bare `require`, which is undefined under the ESM test runner, so the
  `ReferenceError` was caught and read as "driver missing" on every machine
  including CI. The probe now uses `createRequire` and opens a database rather
  than only resolving the module, and `CHAP_REQUIRE_SQLITE`, set in CI, turns
  an unavailable driver into a failure instead of a silent skip.

- **`SqliteStore` had the same defect in its own loader**, so from source it
  reported the dependency as missing when it was installed. The bundled build
  masked it by shimming `require`, which is why it went unnoticed.

- Characterisation tests pin the multi-writer behaviour and the verification
  limit, so a future change to either has to be made deliberately.

### Review decisions bind the content they decided on (CEP-001)

Reported by Iman Schrock (EMILIA) from a source-pinned CHAP-to-AEB
interoperability profile, and tracked as #71 and #72. Proposal in
[`ceps/CEP-001.md`](./ceps/CEP-001.md).

- **Optional `approved_artefact_digest` on `decide.approve`, `decide.reject`
  and `decide.override`.** SHA-256 over the RFC 8785 (JCS) canonicalisation of
  the artefact under review, in the `sha256:<hex>` form the evidence chain
  already uses. The Coordinator verifies it against the artefact under review
  and refuses a mismatch with `-32074`, recording no decision and changing no
  state. Absent, behaviour is unchanged.

  The field sits in `params`, so it falls inside whatever the envelope
  signature covers. A decision then attests content rather than a task
  reference, and a relying party can verify it without trusting the
  Coordinator that produced it. Previously a plain approval bound `task_id`
  and no content, so a consumer could not tell what had been approved.

- **`review.request` on an open review no longer replaces the artefact.**
  An identical artefact is an amendment: the reviewers in `to` are added to the
  existing set, decisions already cast are preserved, and the result carries
  `amended: true`. A different artefact is refused with `-32014`, as is a
  request that would change the decision rule mid-review.

  Both implementations previously accepted the re-request and overwrote the
  pending artefact, so a reviewer could decide on content they never saw. Under
  a quorum rule the effect was worse: the replacement built a fresh review with
  an empty decision list, discarding decisions already cast while their
  envelopes remained in the audit log.

- Conformance vectors `rv-09`, `rv-10` and `rv-11`, passing against both
  reference servers.

---

## 0.2.9: complete package publish and consolidated 0.2.x hardening

First release with the full set on npm and PyPI: the MCP and A2A coordinators
(`@brightbeamai/chap-coordinator-mcp`, `-a2a`) and all five framework adapters
(`chap-langgraph`, `chap-pydantic-ai`, `chap-llama-index`, `chap-ag2`,
`chap-google-adk`), previously unpublished, now ship alongside the coordinators.
It also consolidates the security, audit-integrity, and robustness work that
landed across the 0.2.x line. Both coordinators remain at parity. The CHAP
wire format is unchanged; the MCP transports change observable behaviour,
detailed under MCP 2026-07-28 below.

### Security

- **Key lifecycle bound to signatures.** `participant.rotate_key` must be signed
  with the old key under `require_signatures` (`SIG_ROTATION_KEY_MISMATCH`
  otherwise); `participant.revoke_key` is gated to the key's owner or an
  admin-role member. (#61, #62)
- **`participant.join` no longer replaces a member.** Re-joins are additive; only
  attested (OIDC/VC-bound) keys merge into an existing member, and self-asserted
  keys are not co-registered alongside a proof-of-possession binding. (#25, #37)
- **Step-up fixed and scoped.** Fails closed for OIDC actors and enforces a
  configurable `min_acr`, while agents and services authenticating out of band
  (SPIFFE/X.509) are correctly exempt. (#63)
- **Membership floors on mutating methods.** `task.create` and `task.update`
  require membership; `workspace.set_profiles` requires the admin role;
  `escalate.raise` requires membership and a non-terminal task. (#64, #32)
- **Adapters can no longer fabricate decisions:** no default-to-approve, no
  forced-human reviewer, unknown identity schemes rejected rather than treated as
  human. (#57)
- **SCITT verification fails closed** when no receipt verifier is configured. (#27)
- **Optional read authorisation.** `require_read_membership` (default off) gates
  `audit.read` and `workspace.describe` for multi-tenant or directly-exposed
  deployments. (#64)

### Fixed

- **The whisper lapse notification carries `task_id`.** A lapsed whisper
  applies its default with no human input; it is now visible on a
  task-filtered `audit.read` rather than only under the whisper id.
- **`whisper.answer` records its `task_id`.** The answer previously carried only
  `whisper_id`, so an `audit.read` filtered by `task_id` returned the ask without
  the answer and the thread could not be reconstructed without the whisper id or
  a full read. The Coordinator now records the answer against the task held on
  the whisper (overwriting any caller-supplied value, so an answer cannot be
  filed against a different task) and echoes `task_id` in the response, as
  `profiles/whisper.md` section 3 already specified.
- **Reads no longer mutate the audit chain.** `audit.read`, `workspace.describe`,
  `audit.verify_chain`, and `audit.verify_receipt` are no longer recorded, so
  inspecting or verifying the log no longer changes it. (#43)
- **Chain verification** refuses to run without chaining enabled, verifies from
  genesis, and no longer reports an unchained workspace as tampered. (#23)
- **Overrides** diff against the artefact under review, not a caller value. (#41)
- **Cross-implementation hashing.** Canonical object keys sorted by UTF-16 code
  unit (JCS); `crypto.sign` emits the `ed25519:<kid>:<b64>` wire tag. (#53, #51)
- **Restart-safety.** `Member.keys` and `Task.review` rehydrate on load, so signed
  workspaces and in-flight reviews survive a restart; a corrupt persistence row is
  skipped instead of dropping every workspace. (#39, #49)
- **Deliberation vote weights** taken from the opener, not self-declared by the
  voter. (#30)
- **Decision tags** validated as a list of strings. (#35)

### Added

- **Optional `idempotency_key` on `task.create`:** a redelivered create carrying a
  seen key returns the original task with no duplicate and no second audit entry;
  unchanged when no key is supplied. (#68)
- Regression tests across the dispatch, join, and canonicalisation fixes. (#28)

### Hardened

- **JSON Patch** op-count and result-size bounds guard against amplification and
  copy-bomb inputs, with a shared conformance reject vector. (#55)
- **Python reference server** hardened against malformed requests: invalid
  `Content-Length`, unbounded reads, and deeply nested bodies. (#59)

### Runtime support

- **The supported Node floor is now 20.** Node 18 reached end of life in April
  2025 and lacked a standard `globalThis.crypto`, which sent id generation down
  a fallback that called `require` from an ES module and threw. The fallback is
  removed rather than repaired: every supported runtime provides the global.

### MCP 2026-07-28

Both MCP adapters now target the 2026-07-28 revision and continue to serve
2025-11-25 clients on the same server. The 2026 revision is stateless: rather
than negotiating once through an `initialize` handshake, every request carries
its protocol version and client capabilities in `_meta`, and the server accepts
or rejects each request independently.

- **`server/discover` implemented (SEP-2575)** in both languages, answering a
  discovery probe with the supported protocol versions, capabilities and
  identity in one request. It advertises `["2026-07-28", "2025-11-25"]`,
  spanning both eras because both are served. The method belongs to the 2026
  vocabulary, so a request carrying no per-request envelope is answered with
  `MethodNotFound`.
- **Per-request version negotiation.** A request declaring a protocol version
  the adapter does not implement is refused with `UnsupportedProtocolVersion`
  (-32022), carrying the versions that may be declared so a retry cannot land
  on the same refusal. Only `2026-07-28` is declarable per request; 2025-11-25
  is reached through `initialize`, as its own revision defines.
- **Envelope validation.** A request declaring a protocol version must also
  carry `io.modelcontextprotocol/clientCapabilities`; a missing required field
  is refused with `InvalidParams` (-32602), as is a non-string version.
- **Results are typed and cacheable (SEP-2322, SEP-2549).** A 2026-era caller
  receives `resultType: "complete"` on every result, and `ttlMs` with
  `cacheScope` on `tools/list` and `server/discover`. A 2025-era caller
  receives the result shape its own revision defines, without the 2026 fields.
- **The Python transport builds on the `mcp` 2.x SDK**, which implements the
  2026 boundary natively; the `mcp` extra is now `>=2,<3`. The TypeScript
  adapter implements the same rules against SDK 1.x, which predates the
  revision. Both were verified against the same probe suite and answer it
  identically.

### Dependencies

- Bump `hono`, `fast-uri`, `@hono/node-server`, and `@modelcontextprotocol/sdk`.
  (#66, #67, #70)

### Packaging

- npm packages under the `@brightbeamai/` scope; the full nine-package set now
  published on npm and PyPI. Stopped tracking Python bytecode caches. (#60)

---

## 0.2.8: publish of the 0.2.7 line

A packaging release. `chap-coordinator` on PyPI and
`@brightbeamai/chap-coordinator` on npm were published from this tag; the
other seven packages remained unpublished until 0.2.9.

The protocol and implementation changes it carried are recorded under 0.2.7
below, whose entry was expanded rather than duplicated at the time. No
change is unique to 0.2.8.

---

## 0.2.7: framework adopters, the scenarios directory, and cross-implementation fixes

Adds four framework bridges and a runnable `scenarios/` directory, and
hardens the two reference implementations for their first registry
publish. Most of this release is additive, but it also includes a
normative canonicalisation change (numbers restricted to safe integers, to
guarantee byte-identical hashing across implementations) and a JSON Patch
prototype-pollution fix; see Changed and Security below. Both changes can
affect envelopes that carried non-integer numbers, so read those sections
before upgrading.

### Added

- **Four framework adopters**, joining `chap-langgraph` (shipped in 0.2.5).
  Each bridges a real agent framework's human-in-the-loop mechanism to
  CHAP's `review`/`decide` methods, so an approval, edit, or denial in the
  framework becomes a `decide.approve` / `decide.override` /
  `decide.reject` on the audit chain:
  - **`chap-pydantic-ai`** (`ChapApprovalBridge`): bridges
    [Pydantic AI](https://ai.pydantic.dev)'s deferred-tool approval flow.
    An edit before approval records an override with the diff; per-call
    rationale and tags come from the tool-result metadata.
  - **`chap-ag2`** (`ChapTurnBridge`): bridges
    [AG2](https://github.com/ag2ai/ag2) (AutoGen) agent turns.
  - **`chap-llama-index`** (`ChapHitlBridge`): bridges
    [LlamaIndex Workflows](https://developers.llamaindex.ai/python/framework/understanding/workflows/)
    human-in-the-loop events.
  - **`chap-google-adk`**: bridges
    [Google ADK](https://google.github.io/adk-docs/) human-in-the-loop
    tool confirmations.

  All four join both the agent and the reviewer, address the review to the
  approver, and decide from the approver, so they satisfy the actor
  membership and reviewer-set eligibility rules added in 0.2.6. Each ships
  with tests that run against the reference coordinator with authorisation
  enforcement active, plus a runnable example. The frameworks themselves
  are optional dependencies; the bridges and their tests do not require
  them installed.

- **`scenarios/` directory**: runnable, community-contributed domain
  narratives on CHAP core, one self-contained folder per scenario, kept
  distinct from `examples/` (capability walkthroughs) and the adapters'
  own `examples/` (framework demos). Includes a catalog README (all twelve
  `IN_PRACTICE.md` scenarios with status, labels, layout, and a
  definition-of-done) and the first three worked examples:
  - **`01-solo-dev-overrides/`** in two tiers: a zero-dependency
    `scenario.py` (core/1.0 + review/1.0 + audit-scitt/1.0) that records a
    mix of decisions, verifies the hash-linked chain, reconstructs one
    override, and prints an override learning report; and a `system/`
    implementation driving the same story through a real Pydantic AI agent
    whose review action is approval-gated, offline and reproducible, with a
    documented one-line path to a live model.
  - **`02-marketing-copy/`**: one drafter, one editor; overrides tagged and
    aggregated into an opener-rewrite report.
  - **`03-founder-inbox/`**: a support inbox reconstructed from the chain,
    with a repeated wrong-policy pattern surfaced across tickets.

### Changed

- **Canonicalisation now restricts numbers to safe integers.** A number in
  a CHAP envelope or artefact must be an integer with absolute value at
  most 2^53 - 1; non-integers and larger magnitudes are rejected and must
  be represented as strings (for example `"8.2"`). This makes the Python
  and TypeScript canonicalisers produce byte-identical output by
  construction, so a chain or signature written by one implementation
  verifies against the other. Previously each implementation formatted some
  numbers differently (for example `1e-7`), which could break
  cross-implementation verification. **Potentially breaking:** an envelope
  that carried a non-integer number as a JSON number is now rejected;
  carry it as a string. Integer-only payloads are unaffected. See the
  number-format note in `SPECIFICATION.md` and the shared vectors in
  `conformance/canonical-number-vectors.json`.
- **`confidence` accepts a string as well as a number.** Because a
  confidence score is typically a decimal and is recorded in the audit
  envelope, it follows the canonical-number rule: pass a decimal as a
  string (`"0.9"`). The routing engine coerces it to a number for its
  thresholds, so routing behaviour is unchanged.
- **JSON Patch (`decide.override`) is now full RFC 6902 in both
  implementations.** The TypeScript coordinator previously supported only
  `add`/`replace`/`remove` and threw on `move`/`copy`/`test`, auto-created
  missing intermediate objects on `add`, and silently ignored array append
  (`/-`) and out-of-range operations. It now matches the Python reference
  exactly (all six operations, correct array insert/append, out-of-range
  operations raise). Pinned by `conformance/json-patch-vectors.json`.
- `IMPLEMENTATIONS.md` updated: the four new bridges added to the registry
  with their test counts, and the `chap-langgraph` row bumped to 0.2.7.
- **Canonicalisation is enforced at the dispatch boundary.** An inbound
  envelope that fails to canonicalise (for example, one carrying a
  non-integer JSON number) is rejected with `-32602` at dispatch rather
  than being accepted and failing later when its audit entry is hashed.
- **`participant.join` is idempotent for re-joins.** When an existing member
  re-joins, newly attested keys and OIDC/VC identity fields are merged into
  the existing member record rather than replacing it, so a participant can
  rotate or add a signing key without dropping prior keys.
- **Non-object JSON-RPC `params` are rejected as `-32602` (Invalid
  params)** in both implementations, rather than being passed to a handler
  (which previously could raise an opaque internal error). CHAP methods use
  by-name params, so a non-object `params` is always invalid.
- **Clarified reviewer scoping for `group:` targets.** A review addressed
  to a `group:` URI is satisfied by any workspace member: the coordinator
  does not model group membership, so it cannot restrict a decision to a
  named group on its own. Deployments that need true group restriction must
  enforce it externally. This is now stated explicitly in the code and
  `SPECIFICATION.md`; a future profile may add a first-class group model.
  (Behaviour is unchanged; this is a documentation correction.)

### Security

- **`task.complete` now enforces its legal source states (both
  implementations).** Completion previously rejected only `completed` and
  `declined` tasks, so a `cancelled` or `superseded` task could be revived
  by completing it, and a `paused` task could be completed to bypass the
  pause. Completion is now allowed only from `created` or `in_progress`
  (an allowlist, so any other state is rejected). The `task.status`
  transition table already enforced this for status changes; this closes
  the equivalent hole on the dedicated completion path.
- **`whisper/1.0` answers now require the answerer to be an addressed askee
  (both implementations).** `whisper.answer` previously accepted an answer
  from any caller, so a party the question was not directed at could answer
  a directed whisper and have it recorded as authoritative. Only a
  participant in the whisper's `askee` set may now answer; a broadcast
  scope (`workspace:`/`group:`) is satisfied by any member, consistent with
  reviewer scoping. The existing already-answered, lapsed, and
  option-in-set checks are unchanged.
- **`handoff/1.0` methods now require workspace membership (both
  implementations).** The ownership check (proposer must be the current
  assignee of each task, `HANDOFF_TASKS_NOT_ASSIGNED_TO_PROPOSER`) and the
  recipient-membership check were already enforced; the added floor closes
  a gap where a non-member could call `handoff.decline` to write decline
  metadata onto a handoff.
- **`deliberation/1.0` open/close/comment now require workspace membership
  (both implementations).** These methods previously performed no
  membership check, so a non-member could open a deliberation (choosing its
  rule, participants, weights, and veto set) or call `deliberate.close` to
  finalize the tally early. Membership is now enforced at dispatch for all
  `deliberate.*` methods. The existing per-voter eligibility
  (`DELIB_VOTER_NOT_IN_LIST`) and double-vote (`DELIB_ALREADY_VOTED`) checks
  are unchanged and continue to apply.
- **`control/1.0` operations now require workspace membership (both
  implementations).** None of the control methods
  (`pause`/`resume`/`cancel`/`snapshot`/`rollback`/`supersede`/`set_mode_ceiling`)
  previously performed any authorization check, so a non-member could
  defeat the governance "emergency brake" -- for example resume a workspace
  a governor had paused, raise the mode ceiling to escalate autonomy, or
  cancel in-flight tasks. All `control.*` methods now enforce the
  membership floor at dispatch. (Deployments needing a stricter role gate
  than membership layer it on top via an identity-* profile or application
  check; `control.rollback` remains append-only and does not truncate the
  audit chain.)
- **Signature verification now fails closed (`security-signed/1.0`, both
  implementations).** When `require_signatures` is enabled and a signature
  is present but cannot be verified (missing `from`/`workspace`, or an
  unknown workspace), the request is now rejected rather than silently
  skipped. Previously these cases returned "no error", so a request with an
  unverifiable signature could proceed; notably `workspace.create` accepted
  a garbage signature because the workspace did not yet exist at
  verification time. `workspace.create` (like `participant.join`) is now an
  explicit bootstrap exemption -- it runs before any signing key is
  registered and so is not signature-verified -- while every other method
  must present a verifiable signature.
- **Signed-request key revocation can no longer be bypassed by backdating
  (`security-signed/1.0`, both implementations).** Signature verification
  previously selected the key and evaluated revocation using the envelope's
  own `ts`, which the signer controls; a holder of a revoked key could set
  `ts` to before the revocation and still be accepted. Revocation is now
  evaluated against the coordinator's trusted clock, so a revoked key is
  rejected for any live request regardless of the claimed `ts`. (Historical
  verification against the validity window still uses `ts`.)
- **Audit chain verification now detects tampering of every entry
  (`audit.verify_chain`, both implementations).** Two flaws previously let
  a modified chain pass verification: the replayed chain head was never
  compared against the stored `chain_head` (so tampering the final entry,
  which no stored `prev_hash` covers, went undetected), and an entry could
  opt out of its own check by dropping its `prev_hash`. Verification now
  recomputes every link, requires each stored `prev_hash` to match, and
  compares the replayed head to the stored head. This closes a
  tamper-evidence gap in the audit chain. Verification is scoped to the
  chained region: leading un-chained entries (from before chaining was
  enabled on the workspace) are skipped, and verifying a workspace with no
  chain enabled returns a clear error rather than a false pass.
- **JSON Patch prototype-pollution fix (TypeScript).** A crafted
  `decide.override` diff with a path through `__proto__`, `constructor`, or
  `prototype` could pollute `Object.prototype` in the coordinator process.
  Both implementations now reject those path segments. Since an override
  diff comes from a reviewer, this closes an injection vector reachable
  from ordinary protocol input.
- **Internal errors no longer echo raw exception text on the wire.** The
  JSON-RPC internal-error response (`-32603`) previously included the raw
  exception message, which could disclose internal detail to callers. The
  wire message is now generic; specifics are carried in the error `data`
  field for operators.

### Packaging

- Publish-readiness fixes across all packages: author contact set to
  `oss@brightbeam.com`; an Apache-2.0 `LICENSE` file added to every
  package; the SPDX `license` expression adopted (deprecated classifier
  removed); package `__version__` now derives from installed metadata
  instead of a hardcoded string that had drifted; the npm scope is
  `@brightbeamai`; bridge READMEs and dependency pins corrected.
- The four newer bridges are wired for release the same way
  `chap-langgraph` is [pending: see the release-workflow decision]. Any
  bridge not yet published to PyPI ships as source in the repo and runs
  from a clone.

### Tests

- New bridge suites, all green against the coordinator with authorisation
  enforcement: `chap-pydantic-ai` 17, `chap-ag2` 14, `chap-llama-index` 13,
  `chap-google-adk` 15.
- New cross-implementation conformance suites for canonicalisation and JSON
  Patch, asserting byte-identical output and matching rejection between the
  Python and TypeScript references.
- TypeScript coordinator 103, MCP 17, A2A 14, playground 7; Python
  coordinator 172, langgraph 10. Conformance harness 23/23 on both
  reference implementations.

---

## 0.2.6: MCP argument coercion, dual-language tour, and authorisation enforcement

Follows the 0.2.5 adoption release. Three things: a real-world MCP
integration fix, a clearer README walkthrough, and an authorisation
tightening reported by a collaborator. Backward-compatible on the wire:
no envelope or schema changes. The authorisation work changes behaviour
(it rejects envelopes that were silently accepted before), so it lands
as a minor version rather than a patch.

### Fixed

- **MCP adapters coerce stringified-JSON arguments.** A real Claude
  Desktop integration surfaced that LLM MCP clients routinely serialise
  structured tool arguments as JSON-encoded strings rather than native
  objects or arrays. That left an artefact stored as a string, which
  then crashed a `decide.override` object-path patch with an internal
  error (-32603). Both the TypeScript and Python MCP adapters now
  normalise these at the adapter boundary, before the envelope reaches
  the protocol core: a string value whose parameter schema admits an
  object/array type is JSON-parsed when, and only when, it parses
  cleanly to an accepted type. Bare strings the schema accepts as
  strings (participant URIs, task ids, rationales) are left untouched.
  The protocol core is unchanged and stays strict; the audit log now
  records correctly-typed artefacts and the override applies on the
  first try. Tool descriptions for `output`, `artefact`, and `to` now
  state explicitly that a JSON value is expected, not a stringified one.
- **Actor membership is now enforced.** Before this release, only a
  task's *assignee* was checked for membership (at `task.create` /
  `task.route`); the *actor* (`from`) of a method was not. A decision,
  completion, or review request could therefore be attributed to a
  participant who had never joined. The error table (§13.3) defined an
  `unknown_participant` code for this condition, but no normative
  precondition stated it and no implementation enforced it. Every
  actor-action method in Core and `review/1.0` (`task.complete`,
  `review.request`, `decide.approve`, `decide.reject`, `decide.override`,
  `abstain.declare`) now verifies that `from` is a joined member and
  rejects a non-member with `not_authorised` (-32011). Applied
  identically in the TypeScript coordinator, the Python coordinator, and
  the standalone `core-plus-review` reference server. New precondition
  text added at SPECIFICATION.md §6.3.1. Reported by a collaborator
  integrating CHAP over MCP.

### Added

- **Reviewer-set eligibility (review/1.0).** To act on a review,
  `decide.*` and `abstain.declare` now require `from` to be one of the
  reviewers the review was addressed to (the `to` set on
  `review.request`), not merely any member. The `rule` field still
  governs *how many* must decide; the `to` set governs *who is eligible*.
  A review addressed to a broadcast scope (`workspace:<id>` or
  `group:<id>`) admits any member (resp. any group member); a review
  with no recorded reviewer set falls back to the membership floor. This
  is a new normative rule for the profile, surfaced via the `-32011`
  code review.md already defined. See profiles/review.md §3.2.
- **Worked authorisation walkthrough** at
  `packages/coordinator-py/examples/authorisation_walkthrough.py`:
  exercises an allowed approve and override plus the two refused paths
  (non-member, and member-not-in-reviewer-set), each rejected with
  -32011.
- **Conformance vectors `rv-07` and `rv-08`** covering the non-member
  and non-reviewer rejections; the harness now runs 23 vectors.

### Changed

- **README 90-second tour rewritten as dual-language.** The walkthrough
  now shows TypeScript (typed facade) and Python (dict-based dispatch)
  side by side, and the hero GIF was rebuilt with a step indicator and a
  progress bar so the six-step Core+review flow is legible. Documentation
  only; no API change.
- **Docs updated for the authorisation model.** New SPECIFICATION.md
  §6.3.1 (actor-membership precondition); profiles/review.md §3.2
  (reviewer-set eligibility, with the broadcast-scope caveat); a HANDBOOK
  §7.5 on what the Coordinator enforces beneath workspace policy; FAQ,
  ARCHITECTURE (authorisation layering), and GLOSSARY (Actor, Break-glass,
  Reviewer set) entries.

### Notes

- `escalate.raise` already required its escalation target to be a member,
  so it was unchanged. No break-glass machinery is introduced; admitting
  a new actor is done by joining first, which records the entry as its
  own audit event (flagged-join is the recommended future pattern).
- The reference implementations surface the membership and reviewer-set
  conditions with `not_authorised` (-32011) rather than the spec table's
  `unknown_participant` (-32403), because -32403 already denotes
  `OIDC_TOKEN_INVALID` in their private error range. The broader
  spec-vs-implementation error-table reconciliation is tracked
  separately and is out of scope here.
- The MCP coercion fix is scoped to the adapter boundary; the same
  stringified-JSON input reaching the core through a non-adapter path
  still produces -32603, a latent core rough edge left for a separate
  change.

### Tests

- TS coordinator: **95** (+11 authorisation), TS MCP: **17** (+9 coercion),
  TS A2A: 14, TS playground: 7
- Python coordinator: **120** (+9 coercion, +11 authorisation),
  Python langgraph: 10
- Conformance harness: **23/23** on both reference implementations
  (+2 authorisation vectors)

---

## 0.2.5: publish-ready packages, persistent storage, typed facade, framework adapter

The "adoption" release. The protocol was already there; this release closes
the gap between "impressive spec" and "I had it running in my agent before
lunch". Backward-compatible: no wire-format or schema changes.

### Added

- **Publish-ready npm packages.** `@brightbeamai/chap-coordinator`, `@brightbeamai/chap-coordinator-mcp`,
  and `@brightbeamai/chap-coordinator-a2a` now build to `dist/` (ESM + CJS + `.d.ts` +
  source maps via `tsup`), declare `exports` maps, and ship `prepublishOnly`
  that runs the schemas-drift check, typecheck, tests, and build.
- **PyPI-ready Python wheel.** `chap-coordinator` builds cleanly with a
  `py.typed` marker for type-checker consumers (PEP 561).
- **Pluggable storage with SQLite backend.** New `Store` interface in
  both languages; `MemoryStore` is the default, `SqliteStore` persists
  workspaces to disk and rehydrates on coordinator construction. The
  audit chain head survives restart. TypeScript uses `better-sqlite3`
  as an optional dep; Python uses the stdlib `sqlite3` module (no
  external dep needed). Both write the same schema so a database file
  from one implementation can be read by the other.
- **Typed method facade.** `coord.api.task.create({...})`,
  `coord.api.decide.override({...})`, and equivalents for all 39 methods.
  Full autocomplete and compile-time checking. The original
  `dispatch(envelope)` path is unchanged and still recommended for tools
  that build envelopes by other means.
- **`chap-langgraph`** package (Python). Bridges LangGraph's
  human-in-the-loop interrupt boundary into CHAP envelopes
  (`task.complete` + `review.request`, then `decide.approve` /
  `decide.reject` / `decide.override` on resume). LangGraph itself is
  optional; the bridge accepts any dict-shaped state.
- **Schema-drift detection.** `npm run check:schemas` enforces parity
  between the JSON-schema method catalogue and the TypeScript
  `MethodTable`. Caught and fixed 22 stale `spec-only` entries while
  landing it.
- **Zero-install playground.** `Dockerfile` + `docker-compose.yml`
  (one-command demo, bound to `127.0.0.1`). `.devcontainer/` config for
  Codespaces. New `CHAP_NO_LLM=1` deterministic mock-drafter mode in the
  playground so the marquee demo runs anywhere without a model download.
- **Audit/override viewer.** `tools/audit-viewer.html`: single-file HTML
  with no build step, no dependencies, no network. Drop a `snapshot()`
  JSON; see hash-chain integrity, method-frequency bars, override-tag
  bars, and the full chain rendered inline. Hardened with CSP and
  consistent HTML escaping.
- **Reusable conformance GitHub Action** at
  `.github/actions/chap-conformance/`. Other repos can drop in
  `uses: BrightbeamAI/chap/.github/actions/chap-conformance@v0.2.5` to
  get a "CHAP-conformant" badge.
- **Implementation registry** at `IMPLEMENTATIONS.md` (the long-promised
  link from `ABOUT.md`).
- **Root `package.json`** with `npm workspaces` so the monorepo is
  installable with one `npm install`.

### Changed

- **README quickstart fixed.** Previous version called a non-existent
  `storage` option, used the wrong param names (`workspace_id`/`uri`
  instead of `workspace`/`from`/`type`), and referenced an
  `npx @brightbeamai/analyze-overrides` package that did not exist. All three
  issues fixed; the snippet now runs against the real shipped library.
- **`examples/00-five-minute-start.md`** literal `{ ... same as above ... }`
  placeholder mid-flow replaced with the real payload.
- **`analyze-overrides.ts`** gained a `--db <path>` flag so the in-process
  SqliteStore quickstart works without spinning up the HTTP server.

### Tests

- TS coordinator: **84** (was 72; +6 storage, +6 typed facade)
- TS MCP: 8, TS A2A: 14, TS playground: 7
- Python coordinator: **100**, Python langgraph: **10** (new)
- Conformance harness: 21/21 on both references

### Security

- Audit viewer hardened: every user-controlled field that lands in
  `innerHTML` is now passed through `escapeHtml`. Restrictive CSP
  (`connect-src 'none'`, `frame-ancestors 'none'`) limits the blast
  radius even if a future innerHTML site slips through.
- Docker playground bound to `127.0.0.1` only.
- SqliteStore uses prepared statements with bound parameters; no string
  interpolation into SQL.
- `chap-langgraph` idempotency rewritten to use a structural
  post-condition check rather than matching error-message strings.

### What's not in 0.2.5

Streaming/SSE transports, A2A push notifications, MCP Streamable HTTP,
and A2A 1.0 in the TypeScript adapter (awaits `@a2a-js/sdk` upstream
upgrade) all carry forward as deferred items.

---

## 0.2.4: A2A server transport + inward wrap helpers

Third leg of the transport story. A Coordinator can now present itself as
an [A2A](https://a2a-protocol.org) agent, complementing the MCP server
transport from 0.2.3. Backward-compatible.

### Added

- **TypeScript A2A adapter** (`@brightbeamai/chap-coordinator-a2a`) on `@a2a-js/sdk`
  (A2A 0.3.0). `makeChapAgentCard(...)` returns an Agent Card with 39
  skills, one per CHAP method, named `chap.<method>`.
  `makeChapAgentExecutor(coord)` returns an `AgentExecutor`.
- **Python A2A adapter** (`chap_coordinator.transports.a2a_server`) on
  `a2a-sdk` 1.x (A2A 1.0, with v0.3 compatibility enabled). Same surface
  as the TypeScript adapter.
- **Reference A2A servers** at `reference/a2a-server-ts/` (Express) and
  `reference/a2a-server-py/` (FastAPI). Verified end-to-end with real
  HTTP.
- **Inward wrap helpers** (`wrapMcpToolCall`, `wrapA2aMessageExchange`,
  `contentHash`) in both languages: take a completed external event and
  emit the matching CHAP audit entries with input/output hashes.
- **Walkthrough**: `examples/drive-chap-from-an-a2a-orchestrator.md`.
- Documentation updates across `ABOUT.md`,
  `RELATIONSHIP-TO-OTHER-STANDARDS.md`, `ARCHITECTURE.md`,
  `SPECIFICATION.md` §16.3, `FAQ.md`, `GLOSSARY.md`.

### Spec version asymmetry

The Python `a2a-sdk` is at A2A 1.0, the TypeScript `@a2a-js/sdk` is at
A2A 0.3.0. The CHAP adapter layer is identical across both; Agent Cards
advertise the correct version per implementation.

### Tests

TS A2A: 14. Python A2A: 10. Wrap helpers: 10 each. Both reference
servers smoke-tested with `curl`.

---

## 0.2.3: MCP server transport

A Coordinator can now present itself as an MCP server. Point Claude
Desktop, Cursor, Claude Code, or any other MCP client at it and drive a
CHAP workspace from natural language. Spec target: MCP 2025-11-25.
Backward-compatible.

### Added

- **TypeScript MCP adapter** (`@brightbeamai/chap-coordinator-mcp`) on
  `@modelcontextprotocol/sdk`.
- **Python MCP adapter** (`chap_coordinator.transports.mcp_server`) on
  the official `mcp` SDK, installable via `pip install chap-coordinator[mcp]`.
- **39 CHAP methods exposed as MCP tools** named `chap.<method>`. Tool
  `inputSchema` is the JSON Schema for the method's params.
- **Reference stdio servers** at `reference/mcp-server-{ts,py}/`.
- **Walkthrough**: `examples/drive-chap-from-claude-desktop.md`.
- **`Coordinator.get_workspace(...)`** convenience on the Python
  reference, aligning the two implementations' surfaces.

### Tests

TS MCP: 8 integration tests via `InMemoryTransport.createLinkedPair()`.
Python MCP: 7 integration tests via
`mcp.shared.memory.create_connected_server_and_client_session`. Both
reference servers verified via `initialize` handshake + `tools/list`.

---

## 0.2.2: TypeScript profile parity

The TypeScript reference at `packages/coordinator/` is brought up to
parity with the Python reference: both now cover Core plus every profile,
39 method handlers each.

### Added

- TS handlers for `whisper/1.0`, `deliberation/1.0`, `handoff/1.0`,
  `control/1.0`, `routing/1.0`, `security-signed/1.0`, `audit-scitt/1.0`.
  Plus `modes/1.0` enforcement and `identity-oidc/1.0` / `identity-vc/1.0`
  binding hooks at `participant.join`.
- Supporting modules: `canonical.ts` (JCS), `crypto.ts` (Ed25519 via Node
  built-ins), `ids.ts`, `policy.ts`.
- 62 tests including JCS and Ed25519 conformance vectors, signed-envelope
  verification, OIDC/VC binding, and a composition test exercising every
  method handler.
- `getWorkspace`, `snapshot`, and `restore` methods on the Coordinator
  for persistence integrations.

### Changed (potentially breaking for `@brightbeamai/chap-coordinator` consumers only)

- Wire field rename: `workspace_id` → `workspace`. Matches the spec, the
  Python reference, the conformance harness, and the test vectors.
- `participant.join` field rename: `uri` → `from`.
- `policy: makeDefaultPolicy(...)` slot replaced by separate
  `routingPolicy`, `reviewDepthPolicy`, `escalationPolicy` hooks.
- `patch.ts` aligned to RFC 6902: `replace` against a non-existent path
  now throws (matching Python).

### Cross-language interop verified

All three configurations pass the same 21-vector conformance harness:
TypeScript standalone server, TypeScript library server, Python server.

---

## 0.2.1: Python reference implementation

A second reference implementation, in Python. No protocol or wire-format
changes.

### Added

- `packages/coordinator-py/` (`chap-coordinator`). Core plus every profile,
  39 method handlers, transport-agnostic library.
- `reference/python/`: HTTP server, demo client, `analyze_overrides.py`.
  Passes the same conformance harness as the TypeScript reference on the
  same JSON-RPC 2.0 wire.
- 63 tests covering Core, every profile, cryptographic test vectors,
  signed-envelope verification, OIDC and VC binding, and end-to-end
  composition.

### Notes

- Zero required runtime dependencies for Core. The `security-signed/1.0`
  profile needs `cryptography>=42` via `pip install "chap-coordinator[crypto]"`.
- The Python implementation closes the second-interoperable-implementation
  prerequisite for the Full conformance level.

---

## 0.2: First public release

The first public release of CHAP. A working draft suitable for review,
experimentation, and early production pilots; not yet a stable 1.0.

### Contents

- **Core.** A JSON-RPC 2.0 envelope and seven methods
  (`workspace.describe`, `participant.join`, `participant.leave`,
  `task.create`, `task.update`, `task.complete`, `audit.read`). Task
  lifecycle, participant model, append-only evidence log.
- **Eleven profiles.** `review`, `modes`, `routing`, `whisper`,
  `deliberation`, `handoff`, `control`, `identity-oidc`, `identity-vc`,
  `security-signed`, `audit-scitt`.
- **TypeScript reference implementation.** Core, Core+Review, the
  coordinator package, a CLI, an override-analytics tool, and a
  two-participant playground.
- **Conformance harness** with 21 test vectors covering wire format, all
  seven Core methods, and the six Review methods. Two conformance levels
  claimable (Minimal, Recommended).
- **Twelve worked scenarios** in [`IN_PRACTICE.md`](./IN_PRACTICE.md),
  spanning a solo developer through GMP-regulated manufacturing.
- **Documentation**: Specification, Handbook, Architecture, Security,
  FAQ, Glossary, and a relationship mapping to other standards.

---

## Versioning policy

The versioning rules are at the top of this file.
