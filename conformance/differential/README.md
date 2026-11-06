# Differential conformance fuzzer

CHAP's headline guarantee is that two independently-authored coordinators
produce byte-identical results. The vectors in `../canonical-number-vectors.json`
and `../json-patch-vectors.json` check that on hand-written cases. This checks it
on random ones, and on whole envelope *sequences* rather than single values.

## What it does

For each seed, `fuzz.py`:

1. Drives the **Python** reference (`chap_coordinator`) with a random sequence of
   calls, recording the concrete envelopes it sends. Ids are server-minted, so
   the sequence cannot be written in advance - it is recorded as it is
   produced. Most calls are legal for the state the workspace is in; a share
   miss, and another share is malformed: a param missing, null or of another
   type, for every method, and envelopes that are not a call at all.
2. Replays that exact sequence into the **TypeScript** reference
   (`replay.ts`) in a subprocess. A subprocess boundary is used rather than HTTP
   so no serialisation layer masks or invents a difference.
3. Asserts the two agree on **every response**, on the **state** each holds
   after every call, and on the **audit-chain head**. The chain head alone is
   not a sufficient oracle - the link hash covers the request envelope, so a
   divergence in a *response* (for example a differing artefact shape) passes
   the chain check and shows up only on response comparison. The state is
   compared as a projection both sides produce, `project()` in `fuzz.py` and
   in `replay.ts`, so a handler that changes state and then refuses shows up
   even when both answers agree. An answer of `-32603` from either reference
   fails the seed, since every refusal of a call has its own code.
4. Replays the sequence into both references again, each on a SQLite file,
   and lapses every pending whisper. Each call writes only what it changed, so
   this checks that nothing was missed: a coordinator restarted on the file
   holds what the one that wrote it held, and the two files hold the same
   audit entries.

Runs are reproducible: a seed plus deterministic ids and clock fully determines
a run, so the seed *is* the regression corpus - a failing seed reproduces its
sequence exactly.

## Running

```bash
python conformance/differential/fuzz.py --seeds 200     # sweep seeds 0..199
python conformance/differential/fuzz.py --seed 42       # one seed
python conformance/differential/fuzz.py --seeds 50 --steps 60
```

Requires `chap_coordinator` installed (`pip install -e packages/coordinator-py`)
and `npx tsx` on the path. CI runs a small fixed seed set in the
cross-language conformance job; use a larger `--seeds` sweep locally and nightly.

A failure prints the seed, the envelope index, and the two differing responses
(or the two chain heads), which is enough to reproduce and minimise by hand.

Tracked in [#124](https://github.com/BrightbeamAI/chap/issues/124).


## Snapshot coverage

The action set includes `control.snapshot` with omitted or non-empty selections
and later `control.rollback` calls. The shared
[`control-snapshot-vectors.json`](../control-snapshot-vectors.json) fixtures
also pin exact wire responses, projections, and hashes. Explicitly empty
selection cases remain separate in #151 and #152. The broader state-aware
review-path and coverage-statistics improvements remain tracked in #156.
