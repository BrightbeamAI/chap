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
   type, for every method, and envelopes that are not a call at all. The seeds
   take five configurations by turns:
   - `plain`: no signatures are checked;
   - `signed`: the workspace advertises `security-signed/1.0`, members sign
     with real Ed25519 keys, rotate and revoke them, and a share of the calls
     is unsigned, does not verify, names a kid the sender does not hold, or
     is signed with another member's key;
   - `humans`: the same under the signature policy `"humans"`;
   - `oidc`: signed, with a stub token verifier and a stub credential
     verifier in both references and step-up enforced, and people joining
     with tokens that name them or another participant and bind their key,
     another key or none;
   - `invite`: the workspace admits by invitation, and both references hold
     reads to the membership floor.

   Every configuration meets the admin's `workspace.invite`, newcomers and
   address scopes joining, reviews opened on a null artefact or none and
   amended, and digits outside ASCII in review rules and patch paths.
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
   even when both answers agree. The projections keep every null a client sent
   and every null the log holds; they leave out a null only in a field of the
   coordinators' own records, which both read as a field left out, and keep a
   whisper's `default_if_lapsed`, whose null is a value. An answer of `-32603`
   from either reference fails the seed, since every refusal of a call has its
   own code.
4. Replays the sequence into both references again, each on a SQLite file,
   restarts each on its file, lapses every pending whisper and restarts it
   again. Each call writes only what it changed, so this checks that nothing
   was missed: a coordinator restarted on the file holds what the one that
   wrote it held, and the two files hold the same audit entries, those the
   lapses wrote after the restart included. Each reference then opens the
   file the other wrote, and must hold there what it holds on its own file.
5. Replays the sequence into both references once more, each with two
   coordinators on one SQLite file taking the calls by turns, while the store
   fails a share of the writes, reloads and version reads, and now and then
   the other coordinator takes the next call between a call's keep-up and its
   write. Both references must give every call the same answer, `-32603`
   with `data: {reason: "store"}` included, give `-32603` for nothing else, and
   leave files that hold the same entries and read as the same state.

Runs are reproducible: a seed plus deterministic ids and clock fully determines
a run, so the seed *is* the regression corpus - a failing seed reproduces its
sequence exactly.

## Running

```bash
python conformance/differential/fuzz.py --seeds 200     # sweep seeds 0..199
python conformance/differential/fuzz.py --seed 42       # one seed
python conformance/differential/fuzz.py --seeds 50 --steps 60
python conformance/differential/fuzz.py --seeds 20 --config signed   # one configuration
python conformance/differential/fuzz.py --first 100 --seeds 50       # seeds 100..149
```

Requires `chap_coordinator` installed with its crypto extra
(`pip install -e 'packages/coordinator-py[crypto]'`), since the signed
configurations sign with real keys, and `npx tsx` on the path. CI runs a
small fixed seed set in the cross-language conformance job; use a larger
`--seeds` sweep locally and nightly.

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
