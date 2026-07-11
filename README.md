<div align="center">

# Collaborative Human-Agent Protocol (CHAP)

**The protocol for humans and agents doing real work together.**

When a bot drafts something and a human edits it, where does that edit live?
In CHAP, it lives in an envelope you can query, replay, and verify six months later.

[Install](#install) · [The 90-second tour](#the-90-second-tour) · [Twelve scenarios](./IN_PRACTICE.md) · [About this repo](./ABOUT.md) · [Paper](https://arxiv.org/abs/2606.09751)

</div>

---

<p align="center">
  <img src="docs/img/hero-before-after.svg" alt="Same scenario, two stacks. Without CHAP: six tools holding fragments of one decision (OpenAI logs expired, Zendesk thread, Slack scrolled past, Linear comments, webhook tail, Notion runbook), 45 minutes across four UIs to answer 'what did the bot draft and why did we approve it?'. With CHAP: three hash-linked envelopes (task.create → artefact → decide.override), queryable tags, one audit.read call, 30 seconds." width="100%">
</p>

---

## Why CHAP exists

You have agents doing real work. Drafting code reviews, triaging tickets, suggesting settlements, reviewing contracts. A human approves, edits, or rejects each one. Right now, that decision lives in your application code, your chat threads, your ticket comments, and your head. When something goes wrong six weeks later, reconstructing what happened costs you forty-five minutes and is half guesswork.

CHAP gives you one place to put those decisions and one shape to put them in. The agent's draft is an artefact. The human's edit is a structured override with a diff, a rationale, and tags you control. The whole thing chains together by content hash. You query the chain instead of grepping logs across four UIs.

That's the whole pitch.

## The 90-second tour

A solo developer using Cursor to review pull requests. The bot flags a "warning" the developer disagrees with. Here is the whole exchange, end to end. The clip below runs in about 23 seconds across six labelled steps; the matching code is right underneath.

<p align="center">
  <img src="docs/img/hero.gif" alt="Six-step CHAP Core+Review walkthrough with a progress bar and step indicator across the top. Step 1: Setup (workspace, two participants, a task). Step 2: Drafting (agent drafts a response). Step 3: Pending review (review.request with the draft artefact). Step 4: Override (human disagrees: diff, rationale, tags). Step 5: Audit chain (hash-linked replay, prev_hash continuous). Step 6: Two months in (override learning report shows framework-pattern as the top tag, pointing the next prompt revision at the right problem)." width="100%">
</p>

And here is the code, every line of it. The narrative below is one continuous story in two languages; pick whichever stack you actually use.

**1. Spin up a workspace.** An embedded coordinator with SQLite persistence, two participants, a workspace:

<table>
<tr><th>TypeScript</th><th>Python</th></tr>
<tr><td valign="top">

```ts
import { Coordinator } from "@chap/coordinator";
import { SqliteStore } from
  "@chap/coordinator/storage/sqlite";

const coord = new Coordinator({
  store: new SqliteStore("./chap.db"),
});

coord.api.workspace.create({
  workspace: "wsp_pr_reviews",
  profiles:  ["core/1.0", "review/1.0"],
});

coord.api.participant.join({
  workspace: "wsp_pr_reviews",
  from:      "human:me@local",
  type:      "human",
});

coord.api.participant.join({
  workspace: "wsp_pr_reviews",
  from:      "agent:cursor#v1",
  type:      "agent",
});
```

</td><td valign="top">

```python
from chap_coordinator import Coordinator
from chap_coordinator.storage.sqlite \
    import SqliteStore

coord = Coordinator(store=SqliteStore("./chap.db"))

def send(method, params):
    return coord.dispatch({
        "jsonrpc": "2.0", "id": method,
        "method": method, "params": params,
    })

send("workspace.create", {
    "workspace": "wsp_pr_reviews",
    "profiles":  ["core/1.0", "review/1.0"],
})

send("participant.join", {
    "workspace": "wsp_pr_reviews",
    "from":      "human:me@local",
    "type":      "human",
})

send("participant.join", {
    "workspace": "wsp_pr_reviews",
    "from":      "agent:cursor#v1",
    "type":      "agent",
})
```

</td></tr></table>

**2. The bot drafts, you override.** Wire your existing Cursor integration to emit envelopes:

<table>
<tr><th>TypeScript</th><th>Python</th></tr>
<tr><td valign="top">

```ts
// The bot's review is the output of a task.
const { task_id } = coord.api.task.create({
  workspace: "wsp_pr_reviews",
  from:      "agent:cursor#v1",
  assignee:  "agent:cursor#v1",
  kind:      "code_review",
  input:     { pr_id: "PR-482" },
});

coord.api.task.complete({
  workspace: "wsp_pr_reviews",
  from:      "agent:cursor#v1",
  task_id,
  output:    cursorReview,
});

coord.api.review.request({
  workspace: "wsp_pr_reviews",
  from:      "agent:cursor#v1",
  task_id,
  artefact:  cursorReview,
  to:        "human:me@local",
});

// You disagree with one comment. Override it.
coord.api.decide.override({
  workspace:        "wsp_pr_reviews",
  from:             "human:me@local",
  task_id,
  intent_preserved: true,
  diff: [{ op: "replace",
           path: "/comments/0/severity",
           value: "info" }],
  rationale: "False positive. Framework " +
             "convention, not a bug.",
  tags: ["false-positive",
         "framework-pattern-misread"],
});
```

</td><td valign="top">

```python
# The bot's review is the output of a task.
r = send("task.create", {
    "workspace": "wsp_pr_reviews",
    "from":      "agent:cursor#v1",
    "assignee":  "agent:cursor#v1",
    "kind":      "code_review",
    "input":     {"pr_id": "PR-482"},
})
task_id = r["result"]["task_id"]

send("task.complete", {
    "workspace": "wsp_pr_reviews",
    "from":      "agent:cursor#v1",
    "task_id":   task_id,
    "output":    cursor_review,
})

send("review.request", {
    "workspace": "wsp_pr_reviews",
    "from":      "agent:cursor#v1",
    "task_id":   task_id,
    "artefact":  cursor_review,
    "to":        "human:me@local",
})

# You disagree with one comment. Override it.
send("decide.override", {
    "workspace":        "wsp_pr_reviews",
    "from":             "human:me@local",
    "task_id":          task_id,
    "intent_preserved": True,
    "diff": [{"op":    "replace",
              "path":  "/comments/0/severity",
              "value": "info"}],
    "rationale": "False positive. Framework "
                 "convention, not a bug.",
    "tags": ["false-positive",
             "framework-pattern-misread"],
})
```

</td></tr></table>

> **About the surfaces.** TypeScript ships a typed facade (`coord.api.*`) so every method gets full autocomplete and compile-time checks. Python keeps the JSON-RPC envelope shape on the surface (`coord.dispatch({...})`) and consumers wrap it however suits the call site; a `send()` helper is the idiom the Python tests use. Both paths emit identical wire bytes; the audit chain is byte-for-byte the same regardless of which client made the call.

**3. Two months in, analyse what you have been doing.** This is where the protocol pays you back. The reference repo ships an analytics script in both languages that reads the audit chain (over HTTP or directly from your SQLite file) and groups overrides:

```bash
# TypeScript reference, against the SqliteStore from step 1:
$ npm --prefix reference/core-plus-review run analyze -- --db ./chap.db wsp_pr_reviews

# Python reference, same idea:
$ python3 reference/python/analyze_overrides.py --db ./chap.db wsp_pr_reviews

Override Learning Report
========================
Total overrides: 47

By tag:
  false-positive             ████████████████  31  (66%)
  framework-pattern-misread  ███████████       22  (47%)
  cosmetic-pref              ████              8   (17%)

Top file paths:
  src/handlers/                                    18 overrides
  src/components/                                  9  overrides
```

Your next prompt revision for Cursor is no longer a guess. It cites the pattern by name.

---

## The override envelope, in detail

The override envelope is the single most important shape in CHAP. Every field has a job:

<p align="center">
  <img src="docs/img/override-anatomy.svg" alt="Anatomy of an override envelope, with each field annotated: task_id links to the PR review chain, from carries queryable identity, logical_id survives revision, intent_preserved separates refining from substituting overrides, diff is RFC 6902 JSON Patch, rationale is the 'why' alongside the 'what', tags are structured supervision data." width="100%">
</p>

The two fields most people miss on first read are `intent_preserved` and `tags`.

`intent_preserved` distinguishes a *refining* override (the human agreed with the agent's decision but rewrote how it was expressed) from a *substituting* override (the human reached a different decision). These are two different failure modes and they want different fixes. A high refining rate around one policy clause means the agent's retrieval is off; a high substituting rate on the same clause means the policy itself is ambiguous, or the agent's task context is wrong.

`tags` is the controlled vocabulary your team agrees on. Keep it small. Whatever you put there is the dimension you will aggregate on three months from now, when you are answering questions like *which prompts need work?* or *which paths is the bot getting consistently wrong?*

## Install

**TypeScript / Node:**

```bash
npm install @chap/coordinator
```

**Python:**

```bash
pip install chap-coordinator
```

Either path gets you Core plus the `review/1.0` profile and a runnable reference. The TypeScript reference is in [`reference/`](./reference/); the Python reference is in [`reference/python/`](./reference/python/). The TypeScript library lives at [`packages/coordinator/`](./packages/coordinator/); the Python library at [`packages/coordinator-py/`](./packages/coordinator-py/).

Five-minute hands-on walkthrough: [`examples/00-five-minute-start.md`](./examples/00-five-minute-start.md).

## What ships today

CHAP 0.2 is a public draft. Concretely, this repo contains:

- **The specification.** Core (seven methods, one envelope, one wire format) plus ten optional profiles. Combined into a single document at [`SPECIFICATION.md`](./SPECIFICATION.md), or read individually from [`core/SPEC.md`](./core/SPEC.md) and [`profiles/`](./profiles/).
- **Two reference implementations.** Both cover **Core plus every profile, 39 method handlers in total**. The TypeScript reference is at [`packages/coordinator/`](./packages/coordinator/), with HTTP servers at [`reference/core/`](./reference/core/) and [`reference/core-plus-review/`](./reference/core-plus-review/) and a runnable playground with two browser sessions and a local LLM at [`reference/playground/`](./reference/playground/). The Python reference is at [`packages/coordinator-py/`](./packages/coordinator-py/) with an HTTP server at [`reference/python/`](./reference/python/). Both pass the conformance harness on the same JSON-RPC 2.0 wire.
- **A conformance harness.** 23 test vectors, signing/canonicalisation/chain checks, in-toto attestation output. Two conformance levels are claimable today (Minimal, Recommended); Full waits on broader interop testing across the two implementations.
- **MCP server transport.** A CHAP Coordinator can present itself as an [MCP](https://modelcontextprotocol.io) server, exposing every CHAP method as a tool. Point Claude Desktop, Cursor, Claude Code, or any MCP client at it and drive a CHAP workspace from natural language. TypeScript adapter at [`packages/coordinator-mcp/`](./packages/coordinator-mcp/), Python adapter at [`chap_coordinator.transports.mcp_server`](./packages/coordinator-py/chap_coordinator/transports/mcp_server.py), runnable reference servers at [`reference/mcp-server-ts/`](./reference/mcp-server-ts/) and [`reference/mcp-server-py/`](./reference/mcp-server-py/). Five-minute walkthrough at [`examples/drive-chap-from-claude-desktop.md`](./examples/drive-chap-from-claude-desktop.md).
- **A2A server transport.** A CHAP Coordinator can also present itself as an [A2A](https://a2a-protocol.org) agent, advertising every CHAP method as a discrete skill on its Agent Card. Any A2A-aware orchestrator (Azure AI Foundry, Amazon Bedrock AgentCore, Google ADK, custom multi-agent systems) can register the coordinator by URL and delegate work to it. TypeScript adapter at [`packages/coordinator-a2a/`](./packages/coordinator-a2a/), Python adapter at [`chap_coordinator.transports.a2a_server`](./packages/coordinator-py/chap_coordinator/transports/a2a_server.py), reference servers at [`reference/a2a-server-ts/`](./reference/a2a-server-ts/) and [`reference/a2a-server-py/`](./reference/a2a-server-py/). Walkthrough at [`examples/drive-chap-from-an-a2a-orchestrator.md`](./examples/drive-chap-from-an-a2a-orchestrator.md).
- **Inward wrap helpers.** Small library utilities that turn an external MCP tool call or A2A exchange into a CHAP `task.create` + `task.complete` pair, with hashes of the input/output canonicalisations recorded as citations on the resulting artefact. The library counterpart to the citation patterns in `integrations/CHAP-with-{MCP,A2A}.md`. Available as `wrapMcpToolCall` / `wrapA2aMessageExchange` from `@chap/coordinator`, and as `wrap_mcp_tool_call` / `wrap_a2a_message_exchange` from `chap_coordinator.transports.wrap`.
- **Framework bridges.** Thin Python adapters that connect a real agent framework's human-in-the-loop mechanism to CHAP's `review`/`decide` methods, so an approval, edit, or denial in the framework becomes a `decide.approve` / `decide.override` / `decide.reject` on the audit chain. Five today, each with its own examples and tests, each framework an optional dependency: [`chap-langgraph`](./packages/chap-langgraph/) (LangGraph), [`chap-pydantic-ai`](./packages/chap-pydantic-ai/) (Pydantic AI), [`chap-ag2`](./packages/chap-ag2/) (AG2 / AutoGen), [`chap-llama-index`](./packages/chap-llama-index/) (LlamaIndex Workflows), and [`chap-google-adk`](./packages/chap-google-adk/) (Google ADK).
- **Twelve worked scenarios.** [`IN_PRACTICE.md`](./IN_PRACTICE.md) walks through real cases from a solo developer with Cursor up to GMP-regulated fill-finish manufacturing. Runnable implementations live in [`scenarios/`](./scenarios/), one folder per story (three implemented so far), open to community contributions.

Breaking changes follow Semantic Versioning. Profile surfaces will move faster than Core. Production deployments needing strict stability should wait for 1.0. The longer status statement and the contribution path are in [`ABOUT.md`](./ABOUT.md).

## What you get when you adopt this

- **An audit chain that survives key rotation, log expiry, and people leaving.** Every envelope links to the previous by content hash. One `audit.read` call returns the whole thing.
- **Structured supervision data as a side effect of normal work.** No separate annotation pipeline. The overrides you are already making become a dataset you would otherwise have to commission.
- **Signed, non-repudiable approvals when you need them.** Opt into `security-signed/1.0` for OIDC-bound signatures with a `signature_meaning` you define. Opt into `audit-scitt/1.0` for an external transparency-log anchor, verifiable without trusting your servers.
- **Composability with what you have already built.** CHAP does not replace MCP or A2A. It sits next to them: your agent uses MCP for tools, A2A for other agents, and CHAP to record the shared work with humans.

## Read this next

- **[`IN_PRACTICE.md`](./IN_PRACTICE.md)**. Twelve real-world scenarios from solo dev to GMP-regulated manufacturing. The most useful next read.
- **[`ABOUT.md`](./ABOUT.md)**. What is in this repo, how CHAP relates to MCP and A2A, the standards it reuses, and how to contribute.
- **[`core/SPEC.md`](./core/SPEC.md)**. The seven Core methods. The whole protocol surface fits on one screen.
- **[Technical report on arXiv](https://arxiv.org/abs/2606.09751)**. The full paper. Architecture, design rationale, profile semantics, threat model, and a worked appendix with the twelve scenarios as JSON traces. For readers who want the protocol grounded in its design choices.

## Cite

If you reference CHAP in academic or technical work, please cite the technical report:

```bibtex
@techreport{chap2026,
  author      = {Shahid, Arsalan and Suttie, Gordon and Black, Philip},
  title       = {Collaborative Human-Agent Protocol (CHAP): An open protocol for auditable, structured multi-human and multi-agent collaboration},
  institution = {Brightbeam AI},
  year        = {2026},
  type        = {Technical Report},
  number      = {arXiv:2606.09751},
  url         = {https://arxiv.org/abs/2606.09751}
}
```

---

CC-BY 4.0 (specification) · Apache 2.0 (code) · Royalty-free, any language, any deployment.
