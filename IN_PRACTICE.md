# CHAP in practice

Twelve situations where teams reach for the protocol. They run from one person on a side project up to GMP-regulated manufacturing. They are not meant to be exhaustive. They are meant to be recognisable. If one of them describes your week, the rest of the repo will make more sense.

A note on scope before the cases. CHAP defines what gets recorded and how it links together. It does not pick your model, write your prompts, design your routing rules, interpret your regulator, or decide whether a particular human review was substantively good enough. Each case below describes what CHAP contributes; the substantive work above it remains yours.

The code samples assume the `@brightbeamai/chap-coordinator` Node package or the reference TypeScript client. Equivalents in Python, Rust, or Go follow the same envelope shapes; the wire format is `application/chap+json` and any HTTP client works.

## Contents

Small teams:
1. [The solo dev who can't remember what they overrode](#1-the-solo-dev-who-cant-remember-what-they-overrode)
2. [Marketing copy with one drafter and one editor](#2-marketing-copy-with-one-drafter-and-one-editor)
3. [The indie founder, the inbox, and the angry customer](#3-the-indie-founder-the-inbox-and-the-angry-customer)

Mid-size teams:
4. [Support ops at 03:00](#4-support-ops-at-0300)
5. [Sixty engineers and a code-review bot they don't trust the same way](#5-sixty-engineers-and-a-code-review-bot-they-dont-trust-the-same-way)
6. [A junior, a partner, and a contract that ships tonight](#6-a-junior-a-partner-and-a-contract-that-ships-tonight)
7. [Trust and Safety, three auditors, one queue](#7-trust-and-safety-three-auditors-one-queue)
8. [The creative shop and the client who "never approved that"](#8-the-creative-shop-and-the-client-who-never-approved-that)
9. [The internal Q&A bot that keeps getting the same thing wrong](#9-the-internal-qa-bot-that-keeps-getting-the-same-thing-wrong)

Regulated and enterprise:
10. [A pressure drop on the fill-finish line](#10-a-pressure-drop-on-the-fill-finish-line)
11. [Motor claims and the bereavement that changes everything](#11-motor-claims-and-the-bereavement-that-changes-everything)
12. [Five years on, the regulator asks](#12-five-years-on-the-regulator-asks)

---

## 1. The solo dev who can't remember what they overrode

You ship to GitHub. Cursor reviews every PR. You accept most of its suggestions, reject some, rewrite a few. Three months in you have a vague sense that the bot is "pretty good" but couldn't tell a friend which specific things it gets wrong.

Run a single-binary coordinator on your laptop with SQLite under the hood. The cheapest possible setup is twenty lines of Node:

```ts
import { Coordinator } from "@brightbeamai/chap-coordinator";

const coord = new Coordinator({ storage: "sqlite:./chap.db" });
coord.dispatch({
  jsonrpc: "2.0", id: "init",
  method: "workspace.create",
  params: { workspace_id: "wsp_my_reviews", profiles: ["core/1.0", "review/1.0"] }
});
coord.dispatch({
  jsonrpc: "2.0", id: "j1",
  method: "participant.join",
  params: { workspace_id: "wsp_my_reviews", uri: "human:me@local", type: "human" }
});
```

Wire it into your PR script. When Cursor produces a review, you push the review as an artefact:

```ts
coord.dispatch({
  jsonrpc: "2.0", id: nextId(),
  method: "task.create",
  params: {
    workspace_id: "wsp_my_reviews",
    kind: "code_review",
    assignee: "human:me@local",
    artefact: cursorReview  // whatever Cursor returned
  }
});
```

When you reject a comment, that becomes a `decide.reject` with a category. When you edit before merging, that becomes a `decide.override` with the diff and a one-line rationale:

```json
{
  "method": "decide.override",
  "params": {
    "task_id": "tsk_pr_482",
    "from": "human:me@local",
    "logical_id": "lgl_pr_482_review",
    "intent_preserved": true,
    "diff": [
      { "op": "replace", "path": "/comments/0/severity",
        "from": "warning", "to": "info" }
    ],
    "rationale": "Bot flags unused parameter on every event handler. False positive in this codebase: handlers conform to a framework signature.",
    "tags": ["false-positive", "framework-pattern-misread"]
  }
}
```

Two months in you run the override analyser:

```bash
$ npx @brightbeamai/analyze-overrides wsp_my_reviews

Override Learning Report (wsp_my_reviews)
=========================================
Total overrides: 47

By tag:
  false-positive             ████████████████  31  (66.0%)
  framework-pattern-misread  ███████████       22  (46.8%)
  cosmetic-pref              ████              8   (17.0%)
  ...
```

The next prompt revision you ship for Cursor is no longer a guess. It cites the framework pattern by name.

## 2. Marketing copy with one drafter and one editor

Two-person marketing function at an early-stage company. One writes the long form. One edits and approves. They have added an agent that takes the client brief and produces a first draft. The drafter refines, the editor signs off, the copy ships.

The agent is fine. Some weeks it is better than fine. But the editor keeps making the same kinds of edits, softening corporate openers, swapping passive constructions, killing one cliché in particular, and the team retunes the agent prompt every Friday based on what they remember from the week.

With Core and `review/1.0`, every brief is a task. The agent's first draft is an artefact. Each editor revision becomes an override with tags the editor picks from a short list. The shape:

```json
{
  "method": "decide.override",
  "params": {
    "task_id": "tsk_brief_acme_q3_2026",
    "from": "human:editor@studio.com",
    "logical_id": "lgl_brief_acme_q3_2026",
    "intent_preserved": true,
    "diff": [
      { "op": "replace", "path": "/sections/0/text",
        "from": "Industry-leading solutions for forward-thinking teams.",
        "to": "We help teams ship faster. Here's how." }
    ],
    "rationale": "Opener was generic corporate boilerplate.",
    "tags": ["opener-rewritten", "tone-corporate-to-warm"]
  }
}
```

The tag list is a short controlled vocabulary the team agrees on. The point is not to capture every nuance, just enough that a query against the audit log shows you what you've been doing.

After two months of normal work, the tag histogram shows `opener-rewritten` at 62 percent. The next prompt revision bans three specific opener patterns by name. The cliché count drops next month and the team can see it drop instead of just guessing.

The `modes/1.0` profile is useful here too. The agent stays in `trial` (every output reviewed) until the tag distribution looks stable, then moves to `production` where the editor only spot-checks. The promotion is a conscious moment, not a drift.

## 3. The indie founder, the inbox, and the angry customer

You run a small SaaS. Support volume crossed the threshold where you couldn't read every ticket, so you put a triage agent in front of the inbox: it drafts a response, you approve, edit, or escalate. The plumbing is a webhook, the OpenAI API, and Linear.

Six weeks in, a customer files a chargeback citing a refund policy they say your bot got wrong. You can find the customer's original email and your final reply. The bot's draft is in OpenAI logs somewhere, but only for thirty days, and you've also rotated keys since. Your reasoning for accepting the draft is in nobody's head except your own, and you are not entirely sure about it.

This is where the chain pays for itself before you grow. Every ticket runs through a CHAP coordinator. The bot's draft, your approval-or-edit, the final response, all become evidence-chain entries linked by `prev_hash`. When the chargeback hits you call:

```ts
const audit = await coord.dispatch({
  jsonrpc: "2.0", id: "a1",
  method: "audit.read",
  params: {
    workspace_id: "wsp_support",
    filter: { task_id: "tsk_ticket_chargeback_8821" }
  }
});

for (const entry of audit.result.entries) {
  console.log(`[${entry.envelope.ts}] ${entry.envelope.method}`);
  console.log(`  from: ${entry.envelope.params.from}`);
  console.log(`  ${JSON.stringify(entry.envelope.params).slice(0, 120)}`);
}
```

The output is the whole story in chronological order: the agent's draft, your approval, the final outbound. Looking back, you can also see that you have approved seven other tickets where the bot quoted the same wrong policy. You fix the agent's retrieval to actually consult the policy doc. The eighth ticket is correct.

Six months later, you hire a contractor. The workspace policy is in `workspace.describe`. The override patterns are visible in the audit log. Onboarding is reading the chain, not reading your mind.

## 4. Support ops at 03:00

Mid-size SaaS, around forty support agents across three time zones. Tier-1 humans clear the queue with AI-drafted responses. Tier-2 specialists handle escalations. A senior reviewer gate-keeps any refund over $500. The team uses Zendesk for tickets, Slack for everything else, and an in-house drafting agent.

It is 03:00 in EMEA. A shift lead is taking over from the APAC team. They open Slack and find a 200-message thread with overnight context, several updates that scrolled past midnight, and a note pinned at the top that says "see Linear for blockers." They open Linear. The blocker tickets reference Zendesk tickets they need to look up. By the time they have the picture, twenty minutes have passed and they still don't know which of the overnight refunds were approved against the new policy that went live yesterday afternoon.

With CHAP, the APAC shift lead's last act is one envelope:

```json
{
  "method": "handoff.propose",
  "params": {
    "from": "human:apac.lead@acme.com",
    "to": ["group:emea-shift"],
    "tasks": ["tsk_ticket_8821", "tsk_ticket_8847", "tsk_ticket_8903"],
    "note": "8821: awaiting customer reply, no action needed. 8847: escalated to legal review around 23:00, they're on it. 8903: agent v2.4 drafted but quoted yesterday's shipping policy, do not approve as-is. We logged the wrong-policy-quoted tag three times in the last shift, looks like the agent hasn't picked up the policy refresh. Pinged eng."
  }
}
```

The EMEA lead picks up the queue. They read the handoff and start work:

```ts
// Pull all tasks now assigned to the EMEA shift group
const assignments = await coord.dispatch({
  jsonrpc: "2.0", id: "q1",
  method: "audit.read",
  params: {
    workspace_id: "wsp_support",
    filter: {
      method: "handoff.accept",
      from: "group:emea-shift",
      since: shiftStart
    }
  }
});

for (const a of assignments.result.entries) {
  const taskId = a.envelope.params.task_id;
  const task = await coord.dispatch({
    jsonrpc: "2.0", id: nextId(),
    method: "task.describe",
    params: { workspace_id: "wsp_support", task_id: taskId }
  });
  // task carries the original handoff note, the routing hints,
  // the current state, the policy reference in effect.
  console.log(`${task.result.id}: ${task.result.handoff_note}`);
}
```

The three tasks change ownership. The note is permanent. It doesn't scroll, it doesn't expire, it's in the audit log against those task ids. The lead picks up the queue and knows exactly which ticket to leave alone, which to chase eng on, and which is fine.

The wider picture: `routing/1.0` decides which tickets need senior eyes based on refund size and risk tier. `deliberation/1.0` puts refunds over $2000 to a two-reviewer vote. `whisper/1.0` lets the agent ask a tier-1 "refund or replacement?" mid-draft rather than guessing. None of this is exotic. Most teams already have the routing, the deliberation, and the clarifying questions, just spread across four UIs and one Slack channel. Having them in one chain is the difference between investigating a complaint in thirty seconds and investigating one in ten minutes.

## 5. Sixty engineers and a code-review bot they don't trust the same way

Series B startup. Sixty engineers, four squads, an AI code-review bot reviewing every PR (Greptile, Cursor's review feature, or something in-house). Squad A loves it. Squad B has it muted. The security squad asks a reasonable question one Wednesday: *did the bot ever flag a real security issue that we dismissed?*

There is no answer. Dismissals don't leave a trace.

CHAP turns dismissals into first-class events. A reject carries a category and a rationale:

```json
{
  "method": "decide.reject",
  "params": {
    "task_id": "tsk_pr_review_12482",
    "from": "human:alice@acme.com",
    "reason_category": "false-positive",
    "rationale": "Flagged SQL injection on line 47. Query is parameter-bound through sqlx; bot misread the template string interpolation.",
    "based_on_artefact_id": "art_bot_review_..."
  }
}
```

The security squad runs a quarterly audit query. In TypeScript:

```ts
const dismissals = await coord.dispatch({
  jsonrpc: "2.0", id: "sec-q1",
  method: "audit.read",
  params: {
    workspace_id: "wsp_eng_reviews",
    filter: {
      method: "decide.reject",
      since: "2026-01-01T00:00:00Z",
      until: "2026-04-01T00:00:00Z",
      artefact_tags: ["security-flag"]
    }
  }
});

const byCategory = {};
for (const e of dismissals.result.entries) {
  const cat = e.envelope.params.reason_category;
  byCategory[cat] = (byCategory[cat] || 0) + 1;
}
console.log(byCategory);
// { 'false-positive': 28, 'accepted-risk-tracked-in-jira': 3, ... }
```

Two profiles do most of the work here. `audit-scitt/1.0` anchors the workspace evidence to a transparency log: the security team can verify dismissal history without write access to engineering's tooling, which sidesteps the political problem entirely. `modes/1.0` lets the security squad pin `production` mode on security-sensitive paths (every bot comment must be acted on or dismissed with a reason) while everything else stays in `trial`.

Quarterly review: 4,212 security-flagged comments, 31 dismissals, 28 tagged `false-positive`, 3 tagged `accepted-risk-tracked-in-jira`. The security team has evidence for their own audit. The bot's prompts get tuned for the false-positive patterns. Squad B starts using the bot again because it is getting noticeably better.

## 6. A junior, a partner, and a contract that ships tonight

In-house legal team at a mid-size company, or a small firm, either works. Junior associate runs first-pass review on an MSA with an AI assistant. The assistant flags clauses against the firm's template, suggests adjustments, and the junior approves a redlined version. For non-standard terms, the file goes to a partner.

It is 18:00 on a Tuesday. The MSA needs to ship by 21:00. The junior approves what looks like a standard liability cap. The partner, reviewing at 19:30, catches it. Acme Corp is a Tier-A client, and Tier-A clients have a non-negotiable cap structure the junior didn't recognise. The partner rewrites the clause:

```json
{
  "method": "decide.override",
  "params": {
    "task_id": "tsk_msa_acme_corp",
    "from": "human:smith.j@firm.com",
    "logical_id": "lgl_msa_acme_corp",
    "intent_preserved": false,
    "diff": [
      { "op": "replace", "path": "/clauses/liability/cap",
        "from": "12_months_fees", "to": "24_months_fees" },
      { "op": "add", "path": "/clauses/liability/carve_outs",
        "value": "IP-indemnity-uncapped" }
    ],
    "rationale": "Acme is Tier-A; the 12-month cap shouldn't have left juniors' desks. Updating to the Tier-A structure. This is an escalation miss, junior should have flagged when they saw the client name.",
    "tags": ["liability-cap-modified", "tier-a-client", "junior-escalation-miss"],
    "policy_refs": ["firm.contract-policy.v3#tier-a-clients"]
  }
}
```

`intent_preserved: false` matters. The partner didn't refine the junior's expression of the same decision: the partner substituted a different decision. Over a quarter, the `junior-escalation-miss` tag count is the firm's actual training signal:

```ts
const misses = await coord.dispatch({
  jsonrpc: "2.0", id: "tr-q1",
  method: "audit.read",
  params: {
    workspace_id: "wsp_legal_review",
    filter: {
      method: "decide.override",
      tags: ["junior-escalation-miss"],
      since: lastQuarterStart
    }
  }
});

// Group by junior to see who needs training
const byJunior = new Map();
for (const e of misses.result.entries) {
  const original = e.envelope.params.based_on_artefact_id;
  const originalReview = await getArtefact(original);
  const junior = originalReview.reviewer;
  byJunior.set(junior, (byJunior.get(junior) || 0) + 1);
}
```

The verifiable-credentials profile (`identity-vc/1.0`) is the other piece. The partner's authority to approve a Tier-A modification is a credential, bar admission, firm partnership status, issued by bodies outside the workspace. Signing with a credential rather than just an OIDC identity is the right shape; the credential travels with the override, and a future reader of the chain can verify the signer had the right to make the call.

## 7. Trust and Safety, three auditors, one queue

Community platform, ten million users, the usual queue: reported content arrives, an AI classifier triages it (hate, harassment, CSAM, violence), human reviewers adjudicate. Decisions are leave-up, take-down, escalate-to-law-enforcement, deplatform-user.

The platform answers to three audit demands at once. The EU Digital Services Act and the UK Online Safety Act both require statements of reasons for takedowns and meaningful human review on the harder calls. Law enforcement sends subpoenas asking for specific user histories. Creators appeal takedowns through an internal process that needs the original decision record.

Today, these three demands are served by three logging pipelines that don't quite agree with each other. The DSA pipeline logs the policy citation and the reviewer; the LE pipeline logs the user, the content hash, and the legal hold; the appeals pipeline logs the reviewer's free-text reasoning. Reconciling them when something contentious happens is somebody's full-time job.

A CHAP workspace replaces those three pipelines with one chain. A takedown decision:

```json
{
  "method": "decide.approve",
  "params": {
    "task_id": "tsk_report_19204821",
    "from": "human:reviewer.j@platform.com",
    "based_on_artefact_id": "art_ai_classification_...",
    "decision": "take-down",
    "policy_refs": ["community.guidelines.v18#harassment", "DSA.art.16"],
    "rationale": "Targeted harassment of named individual; pattern matches s.3.2; three other accounts reported same user this week.",
    "tags": ["harassment", "targeted", "pattern-of-behaviour"]
  }
}
```

Generating the DSA statement of reasons becomes a templating exercise:

```ts
async function generateDSAStatement(taskId: string): Promise<string> {
  const chain = await coord.dispatch({
    jsonrpc: "2.0", id: nextId(),
    method: "audit.read",
    params: { workspace_id: "wsp_ts", filter: { task_id: taskId } }
  });

  const decision = chain.result.entries.find(
    e => e.envelope.method === "decide.approve"
  ).envelope.params;
  const classification = await getArtefact(decision.based_on_artefact_id);

  return `
Statement of Reasons (DSA Article 17)
=====================================
Content removed under: ${decision.policy_refs.join(", ")}
Decision date: ${decision.ts}
Classification: ${classification.category}
Confidence: ${classification.confidence}
Human review: ${decision.from}
Reasoning: ${decision.rationale}
Appeal: respond within 14 days at ...
`;
}
```

The subpoena response is the relevant slice of the chain. The appeal references the original `decide.approve` as its base. When the regulator audits a quarter later, `audit.read` produces the full chain. `audit-scitt/1.0` is load-bearing because regulators want third-party-verifiable evidence that takedowns followed policy, and law-enforcement subpoenas need verifiable chain integrity. `deliberation/1.0` handles high-stakes calls (deplatforming high-profile users) as a two-reviewer plus supervisor panel. `modes/1.0` rolls out new classifier versions safely (shadow then trial then production).

## 8. The creative shop and the client who "never approved that"

Fifteen-person creative agency. They use AI for first drafts of copy, image generation, video storyboards. Client approval is the gating event. Multiple internal eyes review before anything goes to the client.

Six months later the campaign is running and the client's new CMO is furious. The tagline isn't what they signed off on, they say. They never approved this version.

The agency has a Slack thread from May with the original brief. Figma comments from June with the internal revision rounds. An email from July with the version that went to the client. A reply two days later that says "looks great let's go." Nobody can find what exactly the new CMO is referring to, and the email "looks great let's go" was from the CMO's predecessor, who left the company in September.

The relevant CHAP move is `security-signed/1.0` plus `identity-oidc/1.0`. The client approval is signed with an OIDC-bound key. The approval envelope references the final artefact by content hash. The signature is non-repudiable. The chain shows exactly which version was approved, by which named individual, at which timestamp, against which brief.

The signing flow on the client side, simplified:

```ts
// Client receives a "please approve" notification linked to a CHAP envelope id.
// They click through, see the final artefact, click Approve.
// The browser signs the approval envelope with the OIDC-bound key.

import { signEnvelope } from "@brightbeamai/client";

const envelope = {
  jsonrpc: "2.0", id: nextId(),
  method: "decide.approve",
  params: {
    workspace_id: "wsp_agency_acme_q4",
    task_id: "tsk_campaign_q4_launch",
    from: clientOidcSubject,
    based_on_artefact_id: "art_final_v7_...",
    logical_id: "lgl_campaign_q4_launch_final",
    content_hash: "sha256:7f8e9d0c..."  // pinned at signing time
  }
};

const signed = await signEnvelope(envelope, clientOidcKey);
await postEnvelope(coordinatorUrl, signed);
```

When the new CMO disputes, you replay the chain. The signed approval references the artefact by content hash. If they change the artefact bytes by even one character, the hash doesn't match, and the chain shows it. The internal-review overrides with tags (`tone`, `imagery`, `factual-correction`, `client-preference`) build a portrait over time of which AI tools give which kinds of work.

## 9. The internal Q&A bot that keeps getting the same thing wrong

500-person company. Internal RAG-backed Q&A bot answers employee questions about benefits, IT, expense policies, internal tools. Some questions get escalated to subject-matter experts (HR for benefits, IT helpdesk for technical, finance for expenses).

The bot answers around 70 percent of questions; the other 30 percent get escalated. The SME team has no easy way to feed corrections back into the bot. They answer the employee in email and move on. The bot keeps making the same mistakes. There is no record of which SME validated which answer; new employees re-ask the same questions next quarter.

The pattern that helps is `whisper/1.0`. When the bot is uncertain, it asks the SME a typed question with a default if no one answers:

```json
{
  "method": "whisper.ask",
  "params": {
    "task_id": "tsk_employee_q_8821",
    "from": "agent:hr-bot",
    "to": ["group:hr-team"],
    "question": "Employee is asking about parental-leave eligibility for adoption. Policy doc doesn't explicitly say. Do we treat adoption same as biological?",
    "options": ["yes-same", "no-different", "needs-case-by-case"],
    "default_if_no_answer": "needs-case-by-case",
    "deadline": "2026-05-17T17:00:00Z"
  }
}
```

The HR partner taps `yes-same` in thirty seconds. The agent's loop receives the answer:

```ts
// Inside the agent's run loop
const whisperResp = await coord.dispatch({
  jsonrpc: "2.0", id: nextId(),
  method: "whisper.ask",
  params: { /* as above */ }
});

const answer = whisperResp.result;
if (answer.status === "answered") {
  // Use answer.choice (e.g. "yes-same")
  // Record it back into our policy KB so we don't ask again next time.
  await pushToKB({
    question_class: "parental-leave-adoption",
    policy_ref: "hr.parental-leave.v3#adoption-eligibility",
    answer: answer.choice
  });
}
```

The answer is recorded as a workspace policy reference. Next time the question comes up, the bot quotes the policy directly. `review/1.0` handles the rest. When the bot does answer a tricky question, SMEs spot-check and override with rationale. The overrides accumulate into a corrections corpus the team uses on the next training cycle.

## 10. A pressure drop on the fill-finish line

03:14 on a Wednesday. A predictive-maintenance agent monitoring an isolator on a fill-finish line at a GMP-regulated biopharma site flags a deviation: differential pressure on iso-3 dropped to 8.2 millibar against a 10.0 minimum, for six minutes, during batch BX-48219.

What happens next involves Annex 11 (electronic systems), Annex 1 (sterile manufacture), ICH Q9 (causation), ICH Q10 (management review evidence), and when the inspector arrives, possibly six months later, the question of whether the batch should have been released, by whom, on what evidence, under which procedure.

Today this story lives in four systems. The historian (AVEVA PI) has the pressure trace. The eQMS (Veeva Vault) has the deviation record. The site's lean daily management board (iObeya) has the shift leader's note. The batch record is somewhere between SAP and a printed paper file. An inspector asking "walk me through batch BX-48219" gets a presentation rather than a record.

A CHAP-instrumented site puts the whole story in one chain. The agent's flag:

```json
{
  "method": "task.create",
  "params": {
    "workspace": "wsp_site_fill_finish_b3",
    "from": "agent:pdm-isolator-monitor#v2.4",
    "kind": "deviation_review",
    "artefact": {
      "kind": "deviation_flag",
      "logical_id": "lgl_dev_2026_05_17_B3_001",
      "subject": "Isolator iso-3 dP below threshold during batch BX-48219",
      "evidence": {
        "historian_tag": "PI:ISO3.DP",
        "window": ["2026-05-17T03:14:00Z", "2026-05-17T03:20:00Z"],
        "value_min": 8.2,
        "threshold": 10.0
      }
    },
    "routing_hints": {
      "criticality": "high",
      "risk_tier": "GMP-batch-affecting"
    },
    "policy_refs": ["GMP.Annex-1.s.4", "site-procedure.fill-finish.v12"]
  }
}
```

Eight hours later, after investigation, the QP signs the release:

```json
{
  "method": "decide.approve",
  "params": {
    "task_id": "tsk_qp_release_BX_48219",
    "from": "human:qp.tanaka@biopharma.com",
    "logical_id": "lgl_batch_BX_48219_disposition",
    "signature_meaning": "qp_release",
    "rationale": "Deviation investigated; root cause = transient HVAC pressure imbalance; product not affected; CAPA-2026-0419 raised for HVAC tuning. Batch released under Annex 1 s.4.30.",
    "policy_refs": ["GMP.Annex-1.s.4.30"]
  }
}
```

When the inspector visits in October, the audit-SCITT receipt for that envelope is verifiable independently of the site. The compliance officer's prep is a single query:

```python
import chap

ws = chap.connect("https://coordinator.site.example/chap", workspace="wsp_site_fill_finish_b3")

# Pull the whole story for batch BX-48219
chain = ws.audit_read(
    logical_id="lgl_batch_BX_48219_disposition",
    include_referenced=True   # pull the deviation_flag chain too
)

for entry in chain:
    print(entry.ts, entry.method, entry.from_, "->", entry.summary)
    if entry.method == "decide.approve" and entry.signature_meaning == "qp_release":
        print(f"  QP credential: {entry.identity.credential_id}")
        print(f"  SCITT receipt: {entry.scitt_receipt_url}")
```

A lot of profiles compose here: `review/1.0` for the deviation review and CAPA initiation, `modes/1.0` for the PdM agent's own promotion from shadow to production with documented evidence, `identity-vc/1.0` because QP status is a regulatory credential, `security-signed/1.0` for Annex 11 signature parity, `audit-scitt/1.0` for the inspector-verifiable anchor, `deliberation/1.0` for multi-party CAPA decisions, `handoff/1.0` for shift handover with batch context. Each is doing one thing the site already needs to do.

## 11. Motor claims and the bereavement that changes everything

UK motor insurer, FCA-regulated, subject to the Consumer Duty. An AI agent handles first-notification-of-loss intake, drafts coverage assessments, recommends settlements. Humans approve everything customer-facing.

A claimant calls about a side-impact incident the previous Saturday. During the call the claimant mentions, in passing, that they were driving to their mother's funeral when the collision happened. The agent's intake module flags the disclosure as a vulnerability indicator.

What the regulator wants, in plain terms: evidence that meaningful human review happened before any decision affected the customer, and that the vulnerability was factored into the outcome. The Financial Ombudsman Service, if the customer ever complains, wants the same. The IAF/SEAR or SMCR regime wants to know which named individual was accountable.

The task envelope routes accordingly:

```json
{
  "method": "task.create",
  "params": {
    "workspace": "wsp_motor_claims_uk",
    "kind": "fnol_triage",
    "from": "agent:fnol-triage-bot#v3.2",
    "input": {
      "claim_ref": "MTR-2026-198421",
      "vulnerability_flags": ["recent_bereavement_in_household"]
    },
    "routing_hints": {
      "criticality": "high",
      "risk_tier": "consumer-duty-vulnerable",
      "max_review_lapse_hours": 4
    },
    "policy_refs": ["FCA.PS22-9", "internal.vulnerability-policy.v4"]
  }
}
```

The vulnerability flag forces senior-handler routing within four hours regardless of claim size. A senior handler picks up the file. Reviewing the AI's draft assessment, she notices the agent has classified two scratches on the rear bumper as new damage from the incident; she remembers from a previous claim that this make of car often arrives with cosmetic blemishes from transport. She also reads the customer's circumstances and decides the £200 goodwill payment described in the firm's vulnerability policy is appropriate:

```json
{
  "method": "decide.override",
  "params": {
    "task_id": "tsk_settlement_MTR-2026-198421",
    "from": "human:harper.s@insurer.com",
    "logical_id": "lgl_claim_MTR-2026-198421",
    "intent_preserved": true,
    "diff": [
      { "op": "replace", "path": "/settlement/amount", "from": 4200, "to": 4800 },
      { "op": "add", "path": "/settlement/goodwill",
        "value": { "amount": 200, "reason": "bereavement_acknowledgement" } }
    ],
    "rationale": "Customer disclosed recent bereavement during intake call; vulnerability policy applied. Rear bumper scratches the AI flagged appear to be pre-existing, common on this model, so removed from damage estimate. Net adjustment of £600.",
    "tags": ["vulnerability-policy-applied", "ai-damage-misclassified", "fair-outcome-adjustment"],
    "policy_refs": ["FCA.PS22-9.fair-outcome", "internal.vulnerability-policy.v4#bereavement"]
  }
}
```

A year later, an Ombudsman complaint arrives about a different customer, but the regulator routinely samples nearby files for context. The team prepares evidence by replaying the chain:

```python
chain = ws.audit_read(logical_id="lgl_claim_MTR-2026-198421")

# Show that meaningful review happened, with timing
trigger = next(e for e in chain if e.method == "task.create")
review = next(e for e in chain if e.method == "decide.override")
print(f"Vulnerability flag at: {trigger.ts}")
print(f"Senior handler decision at: {review.ts}")
print(f"Lapse: {review.ts - trigger.ts}")  # well under 4 hours
print(f"Handler: {review.from_} (credential: {review.identity.credential_id})")
print(f"Policy applied: {review.policy_refs}")
print(f"Rationale: {review.rationale}")
```

No PowerPoint required.

## 12. Five years on, the regulator asks

Wealth management firm. Advisors use an AI assistant to draft suitability assessments, retirement projections, rebalancing recommendations. The firm is subject to SOX-404 (US) or IAF/SEAR (Ireland) or SMCR (UK), regimes that make individuals personally accountable for advice given to customers.

The CF1 / PCF / SMF24 (per regime) is personally on the hook. Their reconstruction substrate today is: the CRM, the planning tool, the agent's transcript, the email to the client, the PDF report. When the regulator inspects under personal-accountability regime, the advisor's defence depends on being able to show exactly what they reviewed, what the AI suggested, what they changed, and why, across years.

Every piece of advice is a task. The AI's proposal is an artefact. The advisor's approval, or her override, is signed with her OIDC-bound key and references the version of the firm's advice rubric in effect at the time:

```json
{
  "method": "decide.approve",
  "params": {
    "task_id": "tsk_rebalance_advice_client_4821",
    "from": "human:advisor.lee@firm.com",
    "logical_id": "lgl_client_4821_advice_q2_2024",
    "based_on_artefact_id": "art_ai_rebalance_proposal_...",
    "signature_meaning": "advice_authorised",
    "rationale": "Client is in accumulation phase, target allocation matches risk profile (assessed 2025-11), no material change since. AI proposal accepted with one adjustment: emerging-markets weight reduced from 12% to 9% per client's specific instruction in Q1 review.",
    "policy_refs": ["firm.advice-policy.v8", "MiFID-II.suitability"],
    "executable_expertise_version": "advice-rubric-v3.2",
    "advisor_credential": "vc:CF1#advisor.lee@firm.com"
  }
}
```

Five years later, the advisor's personal-accountability inspection. The compliance team's job becomes a chain replay rather than a forensic reconstruction:

```python
from datetime import datetime

# Pull every advice approval signed by this advisor across the inspection window
audit = ws.audit_read(
    filter={
        "method": "decide.approve",
        "from": "human:advisor.lee@firm.com",
        "signature_meaning": "advice_authorised",
        "since": datetime(2021, 1, 1),
        "until": datetime(2026, 1, 1)
    }
)

# Group by rubric version to show continuity of the control across the period
rubric_versions = {}
for e in audit:
    v = e.executable_expertise_version
    rubric_versions.setdefault(v, []).append(e)

for v, entries in sorted(rubric_versions.items()):
    first = min(e.ts for e in entries)
    last = max(e.ts for e in entries)
    print(f"{v}: {len(entries)} approvals, active {first} -> {last}")

# The SOX-404 Year-5 control-reliance question: did the control operate
# effectively across the period, or only when last sampled? The chain
# answers that directly: continuity is visible in the rubric-version
# transitions, with cryptographic continuity from the prev_hash links.
```

The verifiable-credentials profile carries the advisor's regulatory status as a credential whose issuer is the regulator itself, not the firm. The audit-SCITT profile anchors the chain to an external transparency service, which means the inspector verifies chain integrity without trusting the firm's own systems. The modes profile shows the AI assistant's promotion history, when each version went into production, against what evidence.

---

## What's actually happening here

Read these twelve in sequence and a small set of mechanisms keeps surfacing in different costumes. A typed override that carries the diff and the reason. A clarifying question with a default if nobody answers. A handoff that preserves context across a shift. A signed approval that someone outside the system can verify.

The pattern is not novel. Every team in these scenarios already does these things. They do them in chat threads, in ticket comments, in email subject lines, in spreadsheets. What they don't have is one place to put them and one shape to put them in. CHAP is that place and that shape.

The larger cases are not technically harder than the smaller ones. The fill-finish line and the wealth-management firm run on the same primitives as the solo developer. They just run more of them, with stronger signatures, against longer retention windows, for more demanding readers. Start with whichever situation looks closest to yours. The rest of the repo will tell you what to add.
