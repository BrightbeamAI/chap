# Scenario 3: the indie founder, the inbox, and the angry customer

> Narrative: [`IN_PRACTICE.md` §3](../../IN_PRACTICE.md#3-the-indie-founder-the-inbox-and-the-angry-customer)
> · Profiles: `core/1.0`, `review/1.0`, `audit-scitt/1.0` · No framework · **good first issue**

## The story

A solo SaaS founder puts a triage agent in front of the support inbox: it
drafts a reply, and the founder approves, edits, or escalates each one. Six
weeks in, a customer files a chargeback claiming the bot quoted the wrong
refund policy. The provider logs have expired and the keys have rotated, so
the founder's own memory is the only record, except every ticket ran
through a CHAP coordinator.

One `audit.read` reconstructs the disputed ticket's whole history in order.
A scan of the same chain then shows the bigger problem: the founder approved
the *same wrong policy* on seven earlier tickets, not just this one. Fix the
agent's retrieval, and the next ticket is correct. Six months later a
contractor onboards by reading the chain, not the founder's mind.

## Run it

```bash
python3 scenario.py
```

That is the whole setup. The script has **no dependencies beyond the
standard library**: from a clone of the repo it imports the in-repo
`coordinator-py` automatically, and once `chap-coordinator` is on PyPI,
`pip install chap-coordinator` works with the same script. No network, no
services, no config.

## What you'll see

1. **Is this record trustworthy?** It re-walks the hash chain the way an
   auditor would (recomputing `sha256(JCS(envelope) || prev_hash)` for
   every entry), confirms it is intact, then shows that quietly
   reattributing one past approval on a copy breaks verification at that
   entry. When the chargeback lands, the record is provable, not a story
   about expired logs.
2. **The chargeback query.** It pulls the disputed ticket back out of the
   chain and prints its full history in order: opened, drafted, sent for
   review, approved, and shows the one ticket the founder escalated, which
   opened a new task for a specialist rather than ending there.
3. **The pattern scan.** It counts how many drafts cited the wrong refund
   policy across the whole chain. The punchline: not one bad ticket but
   seven, so the fix is the agent's retrieval, not this one reply.

### Output shape

```
2. The chargeback: reconstruct ticket 8821 from the chain
   task.create      ticket opened, drafted by the triage agent
   task.update      agent drafting
   task.complete    draft cites refund-30d
   review.request   sent to the founder
   decide.approve   approved by human:you@saas.com
   (separately, ticket 8822 was escalated -> new task tsk_... for human:specialist@saas.com)

3. Pattern scan: which tickets cited the wrong policy?
   Drafts citing 'refund-30d' (the wrong policy): 7
   Tickets: 8815, 8816, 8817, 8818, 8819, 8820, 8821
```

The sample set is small so the run is instant; the point is the pattern,
not the volume.

## A note on `policy_cited`

The wrong policy the bot quoted lives as `policy_cited` on the draft
artefact, deliberately **not** the protocol's `policy_refs`. `policy_refs`
is the *governing* policy of a task or decision; `policy_cited` is *content*
the agent wrote into its draft, and the whole scenario turns on that content
being wrong. Modelling it as artefact content is what lets the pattern scan
count it.

## CHAP methods used

| Method | Role in the story |
|---|---|
| `workspace.create` | Open the workspace with `core/1.0` + `review/1.0` + `audit-scitt/1.0` (the last adds the hash-linked chain). |
| `participant.join` | Join the founder (`human:you@saas.com`), the triage agent (`agent:triage@saas.com`), and the specialist (`human:specialist@saas.com`). |
| `task.create` | Open a `support_reply` task for a ticket, assigned to the triage agent. |
| `task.update` | The agent reports `in_progress`. |
| `task.complete` | The agent records its draft reply (with the cited policy) as the task's output artefact. |
| `review.request` | The agent asks the founder to review, addressed `to` the founder. |
| `decide.approve` / `decide.override` | The founder approves or edits the draft; overrides carry `diff`, `rationale`. |
| `escalate.raise` | The founder hands a hard ticket up: the original task becomes `escalated` and a new task opens for the specialist. |
| `audit.read` | Reconstruct one ticket's history, and scan every draft for the wrong policy. |

Note the authorisation shape every scenario respects: the founder is joined
before they decide, and the review is addressed to the founder who then
decides it. A decision from a non-member, or from a member who was not an
addressed reviewer, is refused (`-32011`).
