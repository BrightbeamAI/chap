# CHAP 0.2.13: the review gate closed on both routes

Read the next section before upgrading.

## If you are upgrading

**`task.update` no longer completes a task that requires review.** The call is
refused with `-32602` and the message names the way through. Submit the output
with `task.complete`, which opens the review, and let a reviewer decide.

`in_progress → completed` is a legal transition and carried no review check, so
until this release a task marked `review_required` could be finished in one
call: no artefact recorded, no `decide.*` on the chain, and an audit log that
reads as though the work was simply done. That is the single failure
`review_required` exists to prevent. Tasks that need no review are unaffected,
and so is every other `task.update` transition.

**Fractional MCP parameters are decimal strings.** `confidence`,
`max_cost_usd`, `weights` and `weight` were declared `type: "number"`. No
caller could satisfy that: CHAP canonicalisation admits integers only, so any
fractional value was refused at ingress with `-32602`. If you were sending
`confidence: 0.86`, the call was already failing. Send `"0.86"`. The schema now
says so, and the error message tells a client that gets it wrong what to do.

## The other gate

**`review.request` accepts the documented widen path.** Adding a reviewer to an
open review means re-requesting the same artefact. The rule was compared after
the default had been applied, so omitting `rule` on the second request read as
an attempt to change it, and was refused with `-32014` on every review not
opened under `any_one_approves`. Only a rule the caller actually supplied
counts as a change.

## Tool descriptions that match the implementation

0.2.13 finished describing all 195 MCP tool parameters, then audited the
descriptions against both coordinators. Nineteen described behaviour the code
does not have. Among them:

- `control.pause` `in_flight_policy` is recorded and never acted on. Work in
  flight is unaffected by either value.
- `control.pause` `scope: "participant"` stops new tasks being assigned to that
  member. It does not stop what they are already doing.
- `control.rollback` restores `mode_ceiling` and `members`. The other captured
  aspects are held in the snapshot and not reapplied.
- `deliberate.vote` `weight` is recorded but not read; the tally uses the map
  given at `deliberate.open`.
- `deliberate.open` `deadline` is recorded; the coordinator does not close a
  vote on it.
- `audit.read` has no tag filter, so grouping by tag is the reader's job.
- `control.snapshot` `label` is not a rollback target; rollback resolves an
  artefact id.
- `escalate.raise` gives the successor an empty input, not the original's.

Each now states what happens and names the error code where a constraint is
enforced. Every claim is checked against both implementations by a probe that
drives 92 calls through each and compares the output byte for byte.

The tool-level descriptions were rewritten too. `task.complete` still told
callers to follow it with `review.request`, which 0.2.12 made wrong. The Python
copies of both tables are now generated from the TypeScript ones, so the claim
that they mirror each other is enforced rather than asserted.

## Documentation

The decimal-string error ran through the documentation as well as the schema:
a runnable `curl` in the five-minute start, examples in four scenario files,
the routing profile, and the JCS vector in `conformance/test-vectors.md`, whose
sample envelope carried `0.42` and whose stated canonical bytes were neither
key-sorted nor whitespace-free. The vector is recomputed and now agrees byte
for byte across both implementations.

Thirteen scrubbed placeholder addresses are repaired. `SPECIFICATION.md` §8.1
gains the `task.update` refusal, and `profiles/review.md` §3.1 covers both
gates.

## Packaging

`coordinator-mcp` imported `zod` without declaring it, so the bundler inlined
the library into all four entry points: 598 KB each, a 1 MB tarball, and a
second `zod` instance in the process alongside the MCP SDK's own. It is now a
declared dependency on the range the SDK asks for, and external to the bundle.
Entry points are 50 KB; the tarball is 205 KB.

`scripts/check-versions.mjs` holds one release version across the forty-eight
places it is written. It found `cli.ts` carrying a version constant of its own.
CI runs it, so a partial bump cannot reach a tag.

## Versions

All nine packages move to 0.2.13 together.

| Package | Version |
|---|---|
| `@brightbeamai/chap-coordinator`, `-mcp`, `-a2a` | 0.2.13 |
| `chap-coordinator` (PyPI) | 0.2.13 |
| `chap-langgraph`, `chap-pydantic-ai`, `chap-llama-index`, `chap-ag2`, `chap-google-adk` | 0.2.13 |

The wire format is unchanged. Both coordinators remain at parity, and both
references pass the conformance harness.

Full detail in [`CHANGELOG.md`](./CHANGELOG.md).
