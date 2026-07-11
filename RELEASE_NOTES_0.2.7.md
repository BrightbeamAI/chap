# CHAP 0.2.7

An additive release on top of 0.2.6: four more framework bridges and a new
runnable `scenarios/` directory. **No change to the protocol core, the
coordinators, or the wire format**: this release adds adapters and worked
examples around an unchanged 0.2.x protocol.

## Highlights

**Four framework adopters.** `chap-langgraph` (shipped in 0.2.5) is joined
by four more bridges, each connecting a real agent framework's
human-in-the-loop mechanism to CHAP's `review`/`decide` methods. An
approval, edit, or denial in the framework becomes a `decide.approve` /
`decide.override` / `decide.reject` on the audit chain:

- **`chap-pydantic-ai`**: bridges [Pydantic AI](https://ai.pydantic.dev)'s
  deferred-tool approval flow. An edit before approval is recorded as an
  override carrying the diff, rationale, and tags.
- **`chap-ag2`**: bridges [AG2](https://github.com/ag2ai/ag2) (AutoGen)
  agent turns.
- **`chap-llama-index`**: bridges [LlamaIndex
  Workflows](https://developers.llamaindex.ai/python/framework/understanding/workflows/)
  human-in-the-loop events.
- **`chap-google-adk`**: bridges [Google
  ADK](https://google.github.io/adk-docs/) human-in-the-loop tool
  confirmations.

All four respect the authorisation rules added in 0.2.6 (they join both the
agent and the reviewer, address the review to the approver, and decide from
the approver), each ships with tests that run against the reference
coordinator with enforcement active, and each has a runnable example. The
frameworks themselves are optional dependencies; the bridges and their
tests do not require them installed.

**The `scenarios/` directory.** Runnable, community-contributed domain
narratives on CHAP core, one folder per scenario, distinct from `examples/`
(capability walkthroughs) and the adapters' own `examples/` (framework
demos). It ships with a catalog of all twelve `IN_PRACTICE.md` scenarios
and the first three worked examples:

- **`01-solo-dev-overrides/`** in two tiers: a zero-dependency
  `scenario.py` that records decisions, verifies the hash-linked chain
  (and shows a tamper being caught), reconstructs one override, and prints
  an override learning report; and a `system/` implementation driving the
  same story through a real Pydantic AI agent whose review action is
  approval-gated, offline, with a one-line path to a live model.
- **`02-marketing-copy/`**: one drafter, one editor; the opener-rewrite
  report the audit trail writes itself.
- **`03-founder-inbox/`**: a support inbox reconstructed from the chain,
  surfacing a repeated wrong-policy pattern across tickets.

The scenarios directory is open to contribution: good-first-issue scenarios
for newcomers, `help wanted` for the regulated ones.

## Also in this release

- `IMPLEMENTATIONS.md` updated with the four new bridges and their test
  counts.

## Upgrade notes

Nothing to change. This release adds packages and examples; it does not
alter the coordinators, the profiles, or the wire format. Existing 0.2.6
deployments are unaffected. If you use one of the new frameworks, install
its bridge; otherwise there is nothing new to adopt.

## Tests

- New bridge suites, all green against the coordinator with authorisation
  enforcement: `chap-pydantic-ai` 17, `chap-ag2` 14, `chap-llama-index` 13,
  `chap-google-adk` 15.
- Unchanged elsewhere: TypeScript coordinator 95, MCP 17, A2A 14,
  playground 7; Python coordinator 120, langgraph 10.
- Conformance harness: 23/23 on both reference implementations.

## Packages

All bumped to 0.2.7 in lockstep. The new bridges' publication status
depends on the release-workflow decision; anything not yet on PyPI ships as
source and runs from a clone.

- `@chap/coordinator`, `@chap/coordinator-mcp`, `@chap/coordinator-a2a` (npm)
- `chap-coordinator`, `chap-langgraph`, and the new `chap-pydantic-ai`,
  `chap-ag2`, `chap-llama-index`, `chap-google-adk` (PyPI)

Full detail in [CHANGELOG.md](./CHANGELOG.md).
