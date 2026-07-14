# CHAP 0.2.7

Adds four framework bridges and a runnable `scenarios/` directory, and
hardens the two reference implementations for their first registry publish.
Mostly additive, but with two changes that can affect envelopes carrying
non-integer numbers: a normative canonicalisation restriction (numbers must
be safe integers, so hashing is byte-identical across implementations) and
a JSON Patch prototype-pollution fix. See "What changed" and "Upgrade
notes" below.

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

## What changed

- **Canonicalisation restricts numbers to safe integers.** A number in a
  CHAP envelope must be an integer with absolute value at most 2^53 - 1;
  decimals and larger magnitudes are rejected and must be carried as
  strings (`"8.2"`). This guarantees the Python and TypeScript
  canonicalisers hash identically, so chains and signatures verify across
  implementations. It is potentially breaking: an envelope that carried a
  non-integer as a JSON number is now rejected.
- **JSON Patch is full RFC 6902 in both implementations**, and a
  prototype-pollution vector in the TypeScript patch apply (paths through
  `__proto__`/`constructor`/`prototype`) is closed. See the Security note
  in `CHANGELOG.md`.
- **`confidence` accepts a string** as well as a number, following the same
  rule; routing behaviour is unchanged.
- Publish-readiness fixes: per-package `LICENSE`, `oss@brightbeam.com`
  contact, SPDX license expression, metadata-derived `__version__`, and the
  `@brightbeamai` npm scope.
- `IMPLEMENTATIONS.md` updated with the four new bridges and their tests.

## Upgrade notes

If your envelopes only ever carried integer numbers, there is nothing to
change; the new bridges are opt-in. If you placed non-integer numbers
(decimals, or integers above 2^53) directly in envelope payloads or in
`confidence`, carry them as strings from now on: the coordinator will
otherwise reject them with a clear error. Cross-implementation
verification is unaffected for integer and string payloads.

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

- `@brightbeamai/coordinator`, `@brightbeamai/coordinator-mcp`, `@brightbeamai/coordinator-a2a` (npm)
- `chap-coordinator`, `chap-langgraph`, and the new `chap-pydantic-ai`,
  `chap-ag2`, `chap-llama-index`, `chap-google-adk` (PyPI)

Full detail in [CHANGELOG.md](./CHANGELOG.md).
