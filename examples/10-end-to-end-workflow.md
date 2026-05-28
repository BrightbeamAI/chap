# Example 10: End-to-end workflow

**Scenario.** A single thread of customer support shows every primitive
working together. A customer files a complex query. A triage agent
classifies and drafts. The agent **whispers** for a quick
clarification. A human **reviews** and **overrides** the tone. The
override triggers a **deliberation** about whether to issue a goodwill
credit. The deliberation produces a decision. A specialist agent
issues the credit via an **MCP** tool. The full thread is auditable
end-to-end through one signed chain.

This example does not introduce new primitives. It shows how the ones
introduced in examples 1-9 compose into a real, realistic flow.

---

## 10.1 Cast and setup

Workspace: `wsp_support_triage` (mode `production`).

| URI                                       | Role           |
|-------------------------------------------|----------------|
| `human:alice@example.org`                 | reviewer       |
| `human:bob@example.org`                   | senior reviewer|
| `human:carol@example.org`                 | finance approver |
| `agent:triage-bot#v3.2`                   | drafter        |
| `agent:credit-issuer#v1.1`                | credit-issuance agent |
| `service:coordinator@example.org`         | coordinator    |

The customer message:

> "I've been a customer for 8 years. Today is the third time in two
> months an order has arrived broken. I'd like a refund on this one
> and frankly some acknowledgement that this isn't working for me."
>, `[email protected]`, ticket `INC-48910`.

---

## 10.2 The trace

Each numbered step is one CHAP message. Signatures and hashes elided.

**(1) Human creates a task and assigns the agent.**
*Method:* `task.assign` · *From:* `human:alice@example.org` · *To:* `agent:triage-bot#v3.2`

The agent is asked to draft a response. Constraints: deadline 09:30,
max 5 tool calls, permitted tools = order-lookup, customer-history,
knowledge-base.

**(2) Agent accepts.**
*Method:* `task.accept` · *From:* `agent:triage-bot#v3.2`

**(3) Agent calls MCP tools (order-lookup, customer-history) to gather
context.** *(Outside CHAP; cited inside the artefact in step 6.)*

**(4) Agent whispers Alice, needs disambiguation.**
*Method:* `whisper.ask` · *From:* `agent:triage-bot#v3.2` · *To:* `human:alice@example.org`

> *"Customer's history shows two prior breakage incidents; both led
> to replacements, not refunds. Customer is explicitly asking for a
> refund this time. Honour the request, or offer the standard
> replacement first?"*
> *Options:* `refund_per_request` / `offer_replacement_first` ·
> *deadline_ms:* 90,000 · *default_if_lapsed:* `offer_replacement_first`.

**(5) Alice answers.**
*Method:* `whisper.answer` · *Answer:* `refund_per_request` · *Comment:*
*"Eight years, three incidents, yes, honour the refund."*

**(6) Agent completes the task with a draft artefact.**
*Method:* `task.complete` · *Artefact:* `draft` with full text, three
MCP citations (order-lookup, customer-history, knowledge-base), confidence 0.87.

The draft:

> *"Hi. I'm very sorry for the trouble with your order. I can see
> this is the third issue you've had this year, which isn't the
> experience we want for you. I've initiated a full refund of £148
> for order ORD-93021; you'll see it back on your card in 3-5
> business days. We'll also waive the return shipping. Thank you for
> being with us so long, and I hope we can make the next one
> right.. Support team."*

**(7) Coordinator opens review.** *(Implicit from `review.required:
true` on the original task.)*

**(8) Alice reads the draft and overrides.**
*Method:* `decide.override` · *From:* `human:alice@example.org` ·
*Diff:* softens "isn't the experience we want for you" to "I can
absolutely see why you'd feel this isn't working for you" and adds a
sentence about a goodwill credit. *Tags:* `tone-warmed`,
`goodwill-credit-suggested`.

The override artefact is recorded. Notably, the override **adds**
content (mention of a goodwill credit) that the agent did not propose.
Per workspace policy, mentioning a credit triggers a separate
authorisation flow because it exceeds the customer-support team's
£50 unilateral limit (Alice has noted £75 in the override).

**(9) Coordinator opens a deliberation.**
*Method:* `deliberate.open` · *From:* `service:coordinator@example.org` ·
*To:* `[human:bob@example.org, human:carol@example.org]`

> *"Authorise £75 goodwill credit on ticket INC-48910? Three product
> failures in two months; customer of 8 years; refund already
> issued."*
> *Rule:* `weighted_vote:2.0` · *Weights:* bob=1.0, carol=1.0.

**(10) Bob comments.**
*Method:* `deliberate.comment`

> *"Pattern of three failures is unusual; product-quality team should
> see this. Credit looks reasonable on retention grounds."*

**(11) Bob votes yea.** Carol votes yea. Both with weight 1.0.

**(12) Coordinator closes the deliberation.**
*Method:* `deliberate.close` · *Outcome:* `approved` · *Tally:* yea 2.0
nay 0.0.

**(13) Coordinator assigns the credit-issuance task.**
*Method:* `task.assign` · *To:* `agent:credit-issuer#v1.1` · *Input
includes a reference to the deliberation decision artefact.*

**(14) Credit agent accepts; calls MCP `customer-credits/issue`;
completes.**
*Method:* `task.complete` · *Artefact:* `decision` with the credit
transaction id, amount £75, citation hash of the MCP call.

**(15) Coordinator publishes the final response to the customer**
*(implementation-side action; the CHAP record is the final
`task.complete` plus a `notify.message` to Alice and Bob with the
sent-message reference).*

---

## 10.3 What the chain looks like

Replayed from the audit:

```
seq=2012  task.assign           alice  -> triage-bot     (INC-48910)
seq=2013  task.accept           triage-bot
seq=2014  task.start            triage-bot
seq=2015  whisper.ask           triage-bot -> alice      (refund vs replacement?)
seq=2016  whisper.answer        alice -> triage-bot      (refund_per_request)
seq=2017  task.complete         triage-bot               (draft + 3 MCP citations)
seq=2018  review.request        triage-bot -> alice
seq=2019  decide.override       alice                    (tone-warmed + credit suggested)
seq=2020  deliberate.open       coordinator -> [bob, carol]
seq=2021  deliberate.comment    bob                      (quality team notified)
seq=2022  deliberate.vote       bob                      (yea, 1.0)
seq=2023  deliberate.vote       carol                    (yea, 1.0)
seq=2024  deliberate.close      lee                      (approved)
seq=2025  task.assign           coordinator -> credit-issuer
seq=2026  task.accept           credit-issuer
seq=2027  task.complete         credit-issuer            (decision + MCP citation)
seq=2028  notify.message        coordinator -> [alice, bob]   (final response sent)
```

Sixteen entries. One customer ticket. Two agents, three humans, three
MCP tools, one deliberation, one override. End-to-end signed,
hash-linked, replayable.

---

## 10.4 What an auditor sees

The auditor runs:

```json
{
  "method": "audit.read",
  "params": {
    "filter": {
      "ticket_id": "INC-48910"
    },
    "include_citations": true,
    "verify_chain": true
  }
}
```

…and receives:

- The sixteen evidence entries above.
- Five resolved MCP citations (order-lookup, customer-history,
  knowledge-base, customer-credits/issue × 1), each with the external
  audit URL and a verified hash match.
- The full text of the draft, the override, the deliberation
  decision, and the final response, addressable by artefact id.
- A chain-verification result: every signature verified, every
  `prev_hash` matched, no replay or id reuse, no temporal-order
  violations.

The "what happened on INC-48910" question goes from days of
spelunking to a single query.

---

## 10.5 What this is and isn't

**What this is.** A demonstration that CHAP's primitives compose. The
review primitive plugged into the deliberation primitive plugged into
the second task. The MCP citations in the first agent's artefact and
the credit agent's artefact share a single verification mechanism.
The override didn't need a custom UI to be useful downstream, its
diff and tags are queryable as data the moment it was signed.

**What this isn't.** A claim that this exact flow is the right one
for every support team. The flow is application-defined. CHAP's job
is to provide the wire format, the identity model, and the evidence
chain. What you compose on top of those primitives is yours.

---

## 10.6 Where to go from here

- **Implement.** Start with [`SPECIFICATION.md`](../SPECIFICATION.md)
  and [`reference/`](../reference/).
- **Compose.** Read [`integrations/CHAP-with-MCP.md`](../integrations/CHAP-with-MCP.md),
  [`integrations/CHAP-with-A2A.md`](../integrations/CHAP-with-A2A.md),
  and [`integrations/CHAP-with-OIDC-OAuth2.md`](../integrations/CHAP-with-OIDC-OAuth2.md).
- **Deploy.** Read [`integrations/CHAP-deployment-patterns.md`](../integrations/CHAP-deployment-patterns.md).
- **Verify.** Use [`conformance/test-vectors.md`](../conformance/test-vectors.md)
  to check your signer and chain implementation against the canonical
  outputs.
- **Improve.** Read [`CONTRIBUTING.md`](../CONTRIBUTING.md). The
  protocol welcomes well-reasoned proposals from implementers.
