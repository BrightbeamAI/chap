# Differential conformance fuzzer

CHAP's headline guarantee is that two independently-authored coordinators
produce byte-identical results. The vectors in `../canonical-number-vectors.json`
and `../json-patch-vectors.json` check that on hand-written cases. This checks it
on random ones, and on whole envelope *sequences* rather than single values.

## What it does

For each seed, `fuzz.py`:

1. Drives the **Python** reference (`chap_coordinator`) with a random sequence of
   legal calls, recording the concrete envelopes it accepts. Ids are
   server-minted, so the sequence cannot be written in advance - it is recorded
   as it is produced.
2. Replays that exact sequence into the **TypeScript** reference
   (`replay.ts`) in a subprocess. A subprocess boundary is used rather than HTTP
   so no serialisation layer masks or invents a difference.
3. Asserts the two agree on **every response** and on the **audit-chain head**.
   The chain head alone is not a sufficient oracle - the link hash covers the
   request envelope, so a divergence in a *response* (for example a differing
   artefact shape) passes the chain check and shows up only on response
   comparison.

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
