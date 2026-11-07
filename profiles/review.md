# Profile: `review`

**Profile id:** `review/1.0` · **Depends on:** Core

The `review` profile adds the workflow most teams want when humans
oversee agent output: **request review**, **approve / reject /
override**, **abstain**, **escalate**. This is CHAP's highest-value
profile, it's where the structured-override learning data lives.

If you implement only one profile beyond Core, implement this one.

---

## 1. New methods

| Method               | Type            | Privileged? | Summary                                                |
|----------------------|-----------------|-------------|--------------------------------------------------------|
| `review.request`     | request         | no          | Ask one or more reviewers to evaluate an artefact.     |
| `review.acknowledge` | request         | no          | Optional: reviewer signals they have begun review.     |
| `decide.approve`     | request         | no          | Approve a draft as-is.                                 |
| `decide.reject`      | request         | no          | Reject a draft with reasoning.                         |
| `decide.override`    | request         | no          | Approve a modified version; produces an override artefact. |
| `abstain.declare`    | request         | no          | Decline to decide; flags for escalation.               |
| `escalate.raise`     | request         | no          | Hand a task up the chain with context.                 |

---

## 2. New task states

The `review` profile adds these states to Core's `task` state machine:

```
created      ─┐
in_progress  ─┤
completed    ─┼──▶ review_requested ──▶ completed   (decide.approve, once the rule is met)
declined     ─┤                      ──▶ completed   (decide.override, with an override artefact)
abstained    ─┤                      ──▶ declined    (decide.reject)
escalated    ─┘                      ──▶ in_progress (decide.reject with request_revision)
                                     ──▶ abstained   (abstain.declare)
```

`review.request` is refused with `-32010` on a task that has been stopped:
`cancelled`, `superseded` or `paused`. The exhaustive transition table is
[SPECIFICATION.md §8.1](../SPECIFICATION.md#81-lifecycle).

`abstain.declare` moves the task to `abstained`, which closes the open
review for every addressed reviewer: a later `decide.*` is refused with
`-32010`. The task is not terminal. A fresh `review.request` reopens
review, and `escalate.raise` creates a new task whose `supersedes` names
the original.

---

## 3. Methods in detail

### 3.1 `review.request`

Open a review on a task. Issued either explicitly, most often by whoever
produced the draft, or implicitly when `task.complete` is called on a task
whose review is required. Refused with `-32010` on a task that has been
cancelled, superseded or paused, so that a review cannot revive stopped work
or step around a pause. On a task whose review is required, the review
opens only on `task.complete`, and `review.request` only adds reviewers to
it once it is open (below).

```json
{
  "method": "review.request",
  "params": {
    "workspace":   "wsp_demo",
    "from":        "agent:triage-bot",
    "to":          ["human:alice@example.org"],
    "ts":          "2026-05-17T09:14:27.105Z",
    "task_id":     "tsk_…",
    "artefact":    { "kind": "draft", "content": { "...": "..." } },
    "rule":        "any_one_approves",
    "deadline":    "2026-05-17T09:30:00Z",
    "summary":     "Drafted apology + carrier-tracking explanation."
  }
}
```

`deadline` is recorded with the request. This version of the profile
gives it no effect: when it passes, the review stays open, and a decision
that arrives later is accepted like any other. `-32013` is reserved for a
lapsed review (§5).

Supported `rule` values: `any_one_approves` (the default), `all_approve`
and `quorum:<n>`, where `n` is a positive integer. Under `all_approve`
every named reviewer must approve; a review addressed only to
`workspace:` or `group:` scopes completes on the first approval. Under
`quorum:<n>`, `n` distinct reviewers must approve. An approval that
leaves the rule unmet is recorded and the task stays `review_requested`.
Any other rule is refused with `-32602`.

#### The implicit review

`task.complete` on a task whose review is required does not complete it.
The call opens a review and the task moves to `review_requested`, with the
submitted output held as the artefact under review. Only a reviewer
decision then reaches `completed`. Review is required when the task carries
`review_required`, or when it runs in `trial` mode on a workspace that has
loaded `modes/1.0`.

Nobody chose the reviewer set on this path, so the Coordinator picks it: the
members of `type: "human"` who are not authors of the work (below), under
the workspace's `review_rule` (SPECIFICATION §6.2), `"any_one_approves"`
where none was set. Nobody opens a review of a review-required task with a
request, so the workspace's rule is how a deployment asks for more than one
approval, and no author can ask for fewer.

Both exclusions serve the same rule: a `decide.approve` on the chain must
mean a person looked. A review its own author can approve does not, and
neither does one an agent can approve. A reviewer set the Coordinator selects
on the operator's behalf MUST NOT widen to include software.

Where no human member qualifies, the Coordinator MUST refuse the completion
with `-32011` rather than open a review nobody can decide. A task requiring
review in a workspace with no independent human reviewer is a
misconfiguration, and the operator needs told.

#### The authors of the work

An open review records `authors`: the task's assignee, then the member that
completed the work under review, then the authors the task carries from a
task it replaces (below), once each. On the implicit review and on a new
round (below), the completer is the `from` of the `task.complete` that
submitted the artefact. On an explicit `review.request`, it is the `from` of
the task's latest `task.complete` that completed it, where there is one; a
reviewer's decision that completed the task is not a completion of the work,
and where the task held a review before, that review's authors stay
authors. A review recorded before authors were kept takes them from the
task's history in the same way: the assignee, and the sender of the latest
`task.complete` that completed the task or opened its review.

The authors of the work go with it. A new round's authors are the earlier
round's together with the new artefact's, whoever resubmits it. A task
`escalate.raise` or `control.supersede` makes carries, as `prior_authors`,
the authors of the task it replaces: that task's review authors where it
held a review, whoever completed its work, the authors it carried itself,
and its assignee where the assignee held the work. A task that
`task.route` or `handoff.accept` moves to another member adds the member it
leaves to its `prior_authors` where that member held the work. A member
held the work when it moves the task on itself, or has acted on the task:
any call of its own is in the task's history. Without this a person who
wrote the work could hand it on, or have it superseded, and then approve it
as one of its reviewers. A member another member passed the task to, and
who never acted on it, made none of the work, and stays a reviewer: were
it counted, any member could leave a person out of a review, or leave
nobody to decide it, by passing the task through them.

A Coordinator MUST refuse with `-32011`, "The author of the work under review
cannot decide it", a `decide.approve`, `decide.reject`, `decide.override`,
`abstain.declare` or `review.acknowledge` (§3.6) from an author, however the
review is addressed, broadcast scopes included. A decision by whoever made the work is not a review of it,
and without this rule an agent could request review of its own output from
`group:` or `workspace:` and approve it.

Where the task requires review, a person decides it, a person other than
the authors is asked, and the authors do not choose who:

- A decision from a member whose `type` is not `human` MUST be refused with
  `-32011`.
- The review opens on `task.complete` (below), under the workspace's
  `review_rule`. A `review.request` on a task whose review is not open MUST
  be refused with `-32011`, "This task requires a person's review, which
  opens on task.complete; review.request only adds reviewers to it once it
  is open", whoever sends it, on a task created, in progress or
  `completed`; a task cancelled, superseded or paused is answered with
  `-32010` first. Opened by a request, the review would take an artefact, a rule
  and reviewers its requester chose: a member who is not an author could
  supply the work, name themselves and approve it, or reopen a task a
  review already completed.
- A `review.request` amending the open review from an author MUST be
  refused with `-32011`, "The author of the work under review cannot
  request its review". Without this the agent whose work the review holds
  could add a person it had just joined, and that person's approval would
  read as oversight.
- A `review.request` whose `to` names an author or a member whose `type` is
  not `human` MUST be refused with `-32011`, and one whose `to` names a
  `workspace:` or `group:` scope with `-32602`: a required review names its
  reviewers, and a scope would let any person who joins later decide it.
- Only the assignee completes the task: a `task.complete` from any other
  member MUST be refused with `-32011`. A completion makes its sender an
  author, so a member who routed the task to a reviewer and completed it
  would otherwise take that reviewer off the review.

The guarantee is as good as a member's `type`. A join declares it, and
without an identity profile (SPECIFICATION §5.4) or admission by invitation
(SPECIFICATION §6.3.3) nothing checks that a member of type `human` is a
person. A deployment whose reviews must mean a person looked binds its
people with one or the other.

An explicit `review.request` on a task that does not require review keeps
whatever `to` it was given. Addressing another agent is a deliberate choice
and this profile does not override it; that is the route for an
agent-reviews-agent flow. Its authors still cannot decide it.

**Re-request on an open review.** A review already open on the task
constrains what a second `review.request` may do.

- If the `artefact` is byte-identical under JCS to the one already
  under review, the request is an **amendment**: the reviewers in `to`
  are added to the existing set, decisions already cast are preserved,
  and the result carries `"amended": true` with the resulting
  `requested_to`. This is how a reviewer is added to a review in
  flight. On a task that requires review, an amendment names people
  other than the review's authors, as an opening request does.
- If the `artefact` differs, the Coordinator MUST refuse with `-32014`
  and change nothing. Replacing the artefact underneath an open review
  would let a reviewer decide on content they never saw, and would
  discard decisions already cast while their envelopes remain in the
  audit log, so a quorum could appear to have been assembled across two
  different artefacts. To review new content, first close the open
  review with a decision, an abstention or an escalation.
  `control.cancel` also closes it, but leaves the task `cancelled`, and
  `review.request` then refuses it with `-32010`.
- A request that would change the `rule` of an open review MUST also be
  refused with `-32014`. The rule under which reviewers agreed to
  decide is fixed for the life of that review.
- Only a `rule` the caller actually supplied counts as a change.
  Omitting it leaves the open review's rule in place, so the amendment
  above does not require the caller to restate a rule to add a
  reviewer. Comparing the resolved default instead would refuse every
  amendment to a review opened under anything but
  `any_one_approves`.

**A review once opened is decided.** A task under review returns to
`in_progress` through `task.update`, or through a `decide.reject` with
`request_revision`. A task that holds a review completes only through a
decision, whether or not it requires review: when `task.complete` then
submits a new artefact, the Coordinator opens a new round of the same
review. The reviewers and the rule stay as they were, and the decisions
start empty, so a decision made in an earlier round does not count towards
the rule on the new artefact. The earlier round's decisions stay on the
chain. The new artefact's authors are recorded for the round and left out
of its named reviewers, and so is any member who is not human where the
task requires review; a completion that would leave no reviewer is refused
with `-32011`. The round's `requested_at` is the time of the completion.
Before this, a task that did not require review completed on that
`task.complete` with no decision at all.

Two rules hold across rounds. A signed copy of a decision already on the
log is refused with `-32075` and is not evaluated again
(SPECIFICATION §10.1). An unsigned copy is a new call, evaluated like any
other ([SECURITY.md, Envelope id replay](../SECURITY.md#envelope-id-replay)),
and counts in the round it arrives in; a decision that carries
`approved_artefact_digest` (§3.2) is refused against any artefact other
than the one it names, so a client that needs its decision tied to one
artefact sends the digest. And `review.request` still adds reviewers to an
open review, as described above, and, on a task that does not require
review, opens a review with new reviewers and a new rule on a task back in
progress. On a task that requires review it only adds people other than the
authors to the open round.

**Completing round the review.** `task.update` reaches `completed` by
another route and carries no output, so a task requiring review, or one
that holds a review, MUST NOT be completed through it. The Coordinator
refuses with `-32602` and names `task.complete` in the message. Without
this the review is skipped outright: the task finishes with no artefact
recorded and no `decide.*` on the chain, which is the single failure a
review exists to prevent. See
[SPECIFICATION.md §8.1](../SPECIFICATION.md#81-lifecycle).

**No way round a review.** More routes would end a review with no
decision. On any task that holds one:

- `task.update` from `review_requested` to `in_progress` takes the work
  back to revise it, and only its authors may: from anyone else it MUST be
  refused with `-32011`.
- `task.update` to `declined` of a task whose review no decision has
  closed, none of its decisions a `decide.reject`, MUST be refused with
  `-32602`. Once a reviewer has rejected the work, its author may give the
  task up.
- `escalate.raise` hands the work on with its review: the successor
  requires review (§3.5), and its review counts the original's authors
  among its own.

On a task whose review is required, from the moment the review opens until
the task ends, unless a reviewer has rejected the round under review:

- `control.cancel` and `control.supersede` from one of its authors MUST be
  refused with `-32011`, "The authors of the work under review cannot end
  its review without a decision", and so MUST a `task.update` from one of
  them that takes the paused task to `cancelled`. This holds under review,
  paused, and after the authors take the work back, which would otherwise
  end the review in two calls. Any other member may still pull either
  brake ([control profile](./control.md)); a `control.supersede` successor
  requires review and counts the original's authors among its own. An
  author hands the work on with `escalate.raise`, which carries the
  review.

### 3.2 `decide.approve` · `decide.reject`

The reviewer's identity, comment and tags are kept in the recorded
`decide.*` call. No separate `decision` artefact is created; the
decision exists only as that call.


**Binding the decision to the content (`approved_artefact_digest`).**
A decision names `task_id`, which identifies *which* review it settles
but not *what* was decided. A relying party consuming the decision as
evidence therefore cannot tell, from the envelope alone, which content
the human actually approved.

`decide.approve`, `decide.reject` and `decide.override` accept an
optional `approved_artefact_digest`: SHA-256 over the RFC 8785 (JCS)
canonicalisation of the artefact under review, in the same
`sha256:<hex>` form the evidence chain already uses. The reviewer's
client computes it from the artefact the human was shown at
`review.request`.

Because the digest sits in `params`, it falls inside whatever the
envelope signature covers under `security-signed/1.0`. The decision
then attests content rather than a task reference, and a relying party
can verify it against the artefact without trusting the Coordinator
that produced the decision.

When present, the Coordinator MUST compute the digest of the artefact
under review and compare. On mismatch it MUST refuse with `-32074`,
record no decision, and change no state. A member's refused call is
recorded as a refusal entry (SPECIFICATION §10.1). When absent,
behaviour is exactly as it was: the field is optional and additive.

```json
{
  "method": "decide.approve",
  "params": {
    "workspace":  "wsp_demo",
    "from":       "human:alice@example.org",
    "task_id":    "tsk_…",
    "approved_artefact_digest": "sha256:9f2c…",
    "comment":    "Reads well, ship it."
  }
}
```

**Eligibility.** The actor (`from`) of any review decision MUST be a
joined workspace member (Core §6.3.1), MUST NOT be one of the review's
authors (§3.1), MUST be a member of `type: "human"` where the task
requires review, **and** MUST be one of the reviewers the review was
addressed to in `review.request`'s `to` set. A Coordinator MUST reject a
decision that fails any of these with `-32011`, checking them in that
order. This holds for `decide.approve`, `decide.reject`,
`decide.override`, and `abstain.declare`. The `rule` field governs *how
many* addressed reviewers must decide for the review to terminate; the
`to` set governs *who is eligible* to decide at all.

A broadcast-scoped reviewer relaxes the last condition. If the `to`
set contains a `workspace:<id>` or `group:<id>` URI, any workspace
member other than the authors is an eligible reviewer, a person where
the task requires review. The Coordinator does not model group
membership; a deployment that needs a decision restricted to a named
group MUST enforce that outside the Coordinator. If a review carries no
recorded reviewer set, the other conditions alone apply.

```json
{
  "method": "decide.approve",
  "params": {
    "workspace": "wsp_demo",
    "from":      "human:alice@example.org",
    "to":        "service:coordinator@example.org",
    "ts":        "2026-05-17T09:15:11Z",
    "task_id":   "tsk_…",
    "comment":   "Looks good.",
    "tags":      ["routine-approval"]
  }
}
```

```json
{
  "method": "decide.reject",
  "params": {
    "workspace": "wsp_demo",
    "from":      "human:alice@example.org",
    "to":        "service:coordinator@example.org",
    "ts":        "2026-05-17T09:15:11Z",
    "task_id":   "tsk_…",
    "comment":   "Tone is too apologetic for a non-delivery issue.",
    "tags":      ["tone-issue"],
    "request_revision": true
  }
}
```

If `request_revision` is true, the task transitions back to
`in_progress` rather than `declined`, giving the assignee
a chance to revise. The revised artefact opens a new round of the review
(§3.1).

### 3.3 `decide.override`: the differentiator

The most valuable method in CHAP. The reviewer approves a *modified*
version of the draft, carrying a structured diff, rationale, and
tags. This is what turns human edits into learning data.

```json
{
  "method": "decide.override",
  "params": {
    "workspace":           "wsp_demo",
    "from":                "human:maya@example.org",
    "to":                  "service:coordinator@example.org",
    "ts":                  "2026-05-17T13:51:32Z",
    "task_id":             "tsk_…",
    "logical_id":          "lgl_01HZ9YX1A2B3C4D5E6F7G8H9J0",
    "intent_preserved":    true,
    "diff": [
      { "op": "replace", "path": "/comments/1/text", "value": "Consider splitting this function; not blocking for this PR." },
      { "op": "replace", "path": "/comments/1/severity", "value": "info" }
    ],
    "rationale": "Tone on the splitting comment was over-strong for a non-blocking suggestion.",
    "tags": ["tone-softened", "severity-downgraded"],
    "policy_refs": ["code-review-tone-guidelines-v2"]
  }
}
```

The `diff` MUST be a valid [RFC 6902 JSON Patch](https://datatracker.ietf.org/doc/html/rfc6902)
document. The Coordinator applies it to the artefact under review and
ignores any `based_on_artefact` in `params`. If the patch does not
apply, the Coordinator refuses with `-32012`, and the error message
names the failure and, where one applies, the failing path.

A patch also fails, with `-32012`, when it carries more than 1,000
operations, or when, after any of its operations, the artefact would hold
more than 100,000 values, or more string and key text than
`max_envelope_bytes` allows (1 MiB by default, counted in UTF-8 bytes),
or would nest deeper than 64 levels, counting the artefact itself as one,
as a request's nesting is counted. The checks run after each operation,
because a `copy` of the root doubles the document, and a patch can put one
value inside another: the value count alone let a small patch grow a
64 KiB artefact to about 2 GiB, or nest it thousands of levels deep.

Each copy, and each move to a deeper place or onto the whole artefact,
costs a pass over the value it carries. A patch whose copies and such
moves carry more than three times the value and size limits between them
also fails with `-32012`, which bounds the work one override can ask for.

When the artefact under review carries a `logical_id`, the override
SHOULD carry the same `logical_id` and SHOULD set `intent_preserved`:
`true` if the override refines the *expression* of the same
underlying decision (as in the example above, where tone was
softened but the underlying review remains "approve with comments"),
`false` if the override substitutes a different decision (e.g. an
"approve" overridden to a "reject"). The field is informational;
CHAP does not constrain semantics. It exists because *"the human
edited the agent's draft"* and *"the human replaced the agent's
draft with a different decision"* are operationally different events
that produce identical envelope structures without it.

The resulting override is recorded as a typed audit entry. Any
downstream analytics, "which tags appear most often?", "which
agent has the highest override rate?", "which guidelines are most
frequently cited?", falls out of querying the audit log.

### 3.4 `abstain.declare`

Decline to decide. The reviewer signals "I'm not the right person
for this." This is strictly better than rejecting (which says the
work is wrong) or staying silent (which is invisible).

```json
{
  "method": "abstain.declare",
  "params": {
    "workspace": "wsp_demo",
    "from":      "human:alice@example.org",
    "to":        "service:coordinator@example.org",
    "ts":        "2026-05-17T11:02:14Z",
    "task_id":   "tsk_…",
    "reason":    "Refund amount (£450) exceeds my authorisation limit (£200).",
    "category":  "out_of_authority",
    "suggested_escalation_target": "human:bob@example.org"
  }
}
```

Standardised categories (extensible):

- `out_of_authority`: exceeds my limit / role
- `out_of_scope`: outside my competence
- `conflict_of_interest`
- `insufficient_context`
- `other`: free-text in `reason`

Abstention rates are a primary signal for tuning agent/role
boundaries. They're queryable as audit data.

### 3.5 `escalate.raise`

Hand a task up the chain. Any workspace member may issue it, typically
after an abstention.

```json
{
  "method": "escalate.raise",
  "params": {
    "workspace":            "wsp_demo",
    "from":                 "human:alice@example.org",
    "to":                   "human:bob@example.org",
    "ts":                   "2026-05-17T11:02:14Z",
    "original_task_id":     "tsk_…",
    "abstention_reason":    "Refund amount exceeds my authorisation limit.",
    "new_task": {
      "kind": "refund_decision",
      "assignee": "human:bob@example.org",
      "input": { "...": "..." }
    }
  }
}
```

The Coordinator creates a new task assigned to `new_task.assignee`, who
MUST be a workspace member, with `supersedes` naming the original. The
original moves to `escalated`, with `superseded_by` naming the new task.
An original in `completed`, `cancelled` or `superseded` is refused with
`-32602`.

The new task takes the mode `new_task.mode` names, or else the original's,
and keeps the original's review requirement. It requires review when the
original did or held a review, or when it is a `trial` task on a workspace
that advertises `modes/1.0`, so escalating a task never removes the review
its completion needs, and never ends an open review with no decision. The Coordinator applies the checks `task.create` applies: an assignee
who is paused is refused with `-32063` ([`control.md`](./control.md) §2),
and a mode above the workspace's current `mode_ceiling` with `-32040`. After
the ceiling has been lowered, naming a mode within it in `new_task.mode` is
how such a task is escalated.

### 3.6 `review.acknowledge`

A reviewer says they have begun the open review, before deciding it. The
call is recorded, so the log holds when each reviewer took a review up as
well as when they decided it, and a reader can tell a review nobody has
opened from one that is being worked on.

```json
{
  "method": "review.acknowledge",
  "params": {
    "workspace": "wsp_demo",
    "from":      "human:alice@example.org",
    "task_id":   "tsk_…"
  }
}
```

The result is `{"acknowledged": true, "requested_at": "…"}`, where
`requested_at` is the time the current round of the review opened (§3.1),
which identifies the round.

- Only a participant who may decide the open review may acknowledge it,
  under the rules of §3.1 and §3.2: an author of the work, a member outside
  the reviewer set, and a member who is not human on a task that requires
  review are refused with `-32011`. Otherwise the log would name as having
  taken up a review someone who cannot decide it.
- A task that is not in `review_requested` has no open review, and the call
  is refused with `-32010`.
- Each reviewer acknowledges a round once. A second acknowledgement from
  the same reviewer in the same round MUST be refused with `-32602`,
  "Already acknowledged in this round", so the first stays the one that
  says when the reviewer began; like every `-32602`, the refusal is not
  recorded (SPECIFICATION §10.1).
- The open review keeps `acknowledgements`, a list of `{reviewer, ts}` in
  the order they arrived. A new round of the review (§3.1) starts with
  none, and an amendment that adds reviewers keeps them. A review recorded
  before acknowledgements were kept is read as having none.
- An acknowledgement decides nothing. It counts towards no rule, and the
  task stays `review_requested` until a decision.

---

## 4. Override-as-data: the unique value

Because every override carries a typed diff + rationale + tags, the
audit log becomes a structured tuning dataset for free. A weekly
aggregation reads the accepted overrides with `audit.read` and groups them on the
client by `params.tags` and `params.from`, over the entries whose
`arrived` time falls in the week:

```json
{
  "method": "audit.read",
  "params": {
    "workspace": "wsp_code_review",
    "filter": { "method": "decide.override", "outcome": "accepted" }
  }
}
```

…produces a tally like "67 overrides last week, 41 tagged
`tone-softened`, 16 tagged `false-positive-corrected`: by agent
version." That data didn't exist in the world before someone
captured it; the protocol makes capturing it the default.

This is the single most defensible reason to adopt CHAP. Every other
piece of the protocol is plumbing.

---

## 5. Error codes

| Code      | Meaning                                                |
|-----------|--------------------------------------------------------|
| `-32010`  | Task is not in a reviewable state: no open review for a decision or an acknowledgement, or a `review.request` on a cancelled, superseded or paused task. |
| `-32011`  | Actor is not authorised to decide or acknowledge the review: not a workspace member, an author of the work under review, a member who is not human on a task that requires review, or a member who was not an addressed reviewer for this task. Also a `review.request` on a task that requires review whose `to` names an author or a member who is not human, and a completion that would leave a review with no reviewer. Core uses the code too, for a `participant.join` naming an existing member that brings only keys the member does not hold (SPECIFICATION.md §6.3.2). |
| `-32012`  | JSON Patch application failed; the message names the failure and any failing path. |
| `-32013`  | Reserved for a lapsed review. No Coordinator returns it, and the review `deadline` is not enforced. |
| `-32014`  | A review is already open on this task with different content, or the request would change the decision rule of an open review. |

---

## 6. Worked example

Full end-to-end walk-through in [`../examples/05-override-capture.md`](../examples/05-override-capture.md).

---

## 7. Composition notes

- **With `modes`:** override capture is most informative in `trial`
  mode, where every output is reviewed. The override rate is a
  primary promotion-to-production criterion.
- **With `deliberation`:** when a single reviewer's override would
  invoke a goodwill credit beyond their authority, the workflow can
  spawn a deliberation rather than a simple escalation.
- **With MCP citations:** override patterns can be correlated with
  the MCP tools the original draft used, "drafts that called the
  knowledge-base tool have a higher override rate; tune the
  knowledge-base agent first."
