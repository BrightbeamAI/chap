# Refusal-recording conformance vectors

`refusal-record-vectors.json` is the shared golden fixture for SPECIFICATION
§10.1: a Coordinator records a member's refused call when it is a governed
attempt, and no other refusal. Both coordinator test suites replay the same
envelopes with deterministic ids and a deterministic clock. Each case sends
one call that is refused and compares the response whole, the length of the
log after it, the number of refusal entries on it, and the chain head. Where
the refusal is recorded, the last entry of the log is compared whole as well,
as text, so the order of its keys is pinned too.

A recorded refusal holds the call under `request`, with an `outcome` beside
it, and its chain link hashes `{"outcome": …, "request": …}`. The fixed chain
head pins those bytes. The Python suite also recomputes every head from the
entries with a canonicaliser of its own, so the formula is checked apart from
either coordinator.

A case may carry `refused_setup`: calls sent after the first
`refused_setup_at` setup envelopes, each of which is refused, before the rest
of the setup runs. The signed cases carry a `sig` that neither coordinator
verifies, since the vectors do not require signatures. The rules for a signed
copy key on the presence of a signature.

The cases come in two halves.

**Refusals that are recorded**, examples of governed attempts:

- `control.pause` with `control/1.0` not advertised: the gate refuses a
  privileged method, `-32601`.
- `decide.approve` by a member the review was not addressed to, `-32011`.
- `decide.approve` by the addressed reviewer with an artefact digest that does
  not match, `-32074` (CEP-001).
- `task.create` on a workspace whose emergency brake is on, `-32063`.
- `whisper.answer` with an option that is an object, which is outside the
  option set, `-32022`. The message shows the option as JSON.
- An unsigned `decide.approve` identical to one refused and recorded
  earlier. An unsigned call is not compared with the log, so it is evaluated
  again and refused again, and the second refusal is recorded too.

**Refusals that are not recorded**, the half that shows the rule leaving
them off:

- `whisper.ask` with `whisper/1.0` not advertised: the gate refusing an
  ordinary method.
- A method that does not exist.
- `task.create` with invalid parameters, `-32602`.
- `control.pause` from a caller who is not a member.
- A signed `decide.approve`, a copy of one refused and recorded earlier, sent
  after the review has been re-addressed to its sender. It is answered with
  the recorded refusal and `data.refused_at_seq`, and is neither evaluated nor
  recorded again.
- A signed copy of a `decide.approve` that took effect. The review has
  closed, so the copy is refused `-32010`, and the refusal is not recorded:
  its signer made the call once.

Run them:

```bash
npx tsx --test packages/coordinator/tests/refusal_record_vectors.test.ts
python -m pytest -q packages/coordinator-py/tests/test_refusal_record_vectors.py
```
