# Refusal-recording conformance vectors

`refusal-record-vectors.json` is the shared golden fixture for SPECIFICATION
§10.1: a Coordinator records a member's refused call when it is a governed
attempt, and no other refusal. Both coordinator test suites replay the same
envelopes with deterministic ids and a deterministic clock. Each case sends
one call that is refused and compares the response whole, the length of the
log after it, and the chain head. Where the refusal is recorded, the last
entry of the log is compared whole as well.

A recorded refusal holds the call under `request`, with an `outcome` beside
it, and its chain link hashes `{"outcome": …, "request": …}`. The fixed chain
head pins those bytes. The Python suite also recomputes every head from the
entries with a canonicaliser of its own, so the formula is checked apart from
either coordinator.

Eight cases, in two halves.

**Four refusals that are recorded**, one for each kind of governed attempt:

- `control.pause` with `control/1.0` not advertised: the gate refuses a
  privileged method, `-32601`.
- `decide.approve` by a member the review was not addressed to, `-32011`.
- `decide.approve` by the addressed reviewer with an artefact digest that does
  not match, `-32074` (CEP-001).
- `task.create` on a workspace whose emergency brake is on, `-32063`.

**Four refusals that are not recorded**, which is the half that makes the
fixture worth having:

- `whisper.ask` with `whisper/1.0` not advertised: the gate refusing an
  ordinary method.
- A method that does not exist.
- `task.create` with invalid parameters, `-32602`.
- `control.pause` from a caller who is not a member.

Run them:

```bash
npx tsx --test packages/coordinator/tests/refusal_record_vectors.test.ts
python -m pytest -q packages/coordinator-py/tests/test_refusal_record_vectors.py
```
