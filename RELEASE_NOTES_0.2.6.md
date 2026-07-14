# CHAP 0.2.6

Follows the 0.2.5 adoption release with a real-world MCP integration fix, a
clearer README walkthrough, and an authorisation tightening reported by a
collaborator. **Backward-compatible on the wire:** no envelope or schema
changes. The authorisation work changes behaviour (it now rejects envelopes
that were silently accepted before), which is why this is a minor version
rather than a patch.

## Highlights

**MCP adapters now handle stringified-JSON arguments.** A real Claude Desktop
integration showed that LLM MCP clients often send structured tool arguments
as JSON-encoded strings instead of native objects or arrays. That left an
artefact stored as a string and crashed a `decide.override` patch with an
internal error. Both the TypeScript and Python MCP adapters now normalise these
at the adapter boundary, before the envelope reaches the protocol core. The
core is untouched and stays strict; the audit log records correctly-typed
artefacts and the override applies on the first try.

**Actor membership is enforced.** Previously only a task's *assignee* was
checked for membership; the *actor* (`from`) of a method was not, so a
decision or completion could be attributed to a participant who never joined.
Every actor-action method in Core and `review/1.0` now verifies that `from` is
a joined member and rejects a non-member with `not_authorised` (-32011). This
makes the audit log's attribution sound. New precondition text at
SPECIFICATION.md §6.3.1.

**Reviewer-set eligibility for decisions.** To act on a review, `decide.*` and
`abstain.declare` now require `from` to be one of the reviewers the review was
addressed to (the `to` set on `review.request`), not merely any member. The
`rule` field governs *how many* reviewers must decide; the `to` set governs
*who is eligible*. A review addressed to a `workspace:<id>` or `group:<id>`
scope admits any member, and a review with no recorded reviewer set falls back
to the membership floor. See profiles/review.md §3.2.

**Dual-language README tour.** The 90-second walkthrough now shows TypeScript
and Python side by side, and the hero GIF was rebuilt with a step indicator and
progress bar so the six-step Core+review flow is legible.

## Upgrade notes

This release tightens enforcement. If you were already joining your
participants before they act, and addressing reviews to the reviewers who
decide them, you need to change nothing: the reference flows, the MCP and A2A
adapters, the langgraph bridge, and the playground are all unaffected. If you
relied on the previously-missing checks, two calls that used to succeed will
now return `-32011`:

- A `decide.*`, `task.complete`, `review.request`, or `abstain.declare` whose
  `from` never joined the workspace. Fix: `participant.join` first.
- A `decide.*` or `abstain.declare` from a member who was not named in the
  review's `to` set. Fix: address the review to that reviewer, or use a
  `workspace:`/`group:` broadcast scope if any member should be able to decide.

There is no break-glass bypass: admitting a new actor (an escalation target or
an emergency approver) is done by joining them first, so the admission is itself
recorded in the audit chain.

## Conformance

- Two new harness vectors: `rv-07` (non-member decision rejected) and `rv-08`
  (member-not-in-reviewer-set rejected; the addressed reviewer still succeeds).
- The harness now runs 23 vectors and passes on both reference implementations.

## Notes

- `escalate.raise` already required its escalation target to be a member, so it
  was unchanged.
- The reference implementations surface the membership and reviewer-set
  conditions with `not_authorised` (-32011) rather than the spec table's
  `unknown_participant` (-32403), because -32403 already denotes
  `OIDC_TOKEN_INVALID` in their private error range. The broader
  spec-versus-implementation error-table reconciliation is tracked separately.
- The MCP coercion fix is scoped to the adapter boundary. The same
  stringified-JSON input reaching the core through a non-adapter path still
  produces -32603, a latent core rough edge left for a separate change.

## Tests

- TypeScript coordinator: 95 (+11 authorisation); MCP adapter: 17 (+9 coercion);
  A2A adapter: 14; playground: 7.
- Python coordinator: 120 (+9 coercion, +11 authorisation); langgraph bridge: 10.
- Conformance harness: 23/23 on both reference implementations.

## Packages

All published at 0.2.6:

- `@brightbeamai/chap-coordinator`, `@brightbeamai/chap-coordinator-mcp`, `@brightbeamai/chap-coordinator-a2a` (npm)
- `chap-coordinator`, `chap-langgraph` (PyPI)

Full detail in [CHANGELOG.md](./CHANGELOG.md).
