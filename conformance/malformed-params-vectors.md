# Malformed-params conformance vectors

`malformed-params-vectors.json` holds calls whose params are of the wrong
type or shape, each answered alike by both coordinators. Every case was found
by the differential fuzzer (`differential/fuzz.py`), which sends each method a
param that is missing, null, or of another type, and compares the two
coordinators' answers and their state after every call.

Before these were fixed the two disagreed in three ways:

- **A refusal in one and a fault in the other.** Python could not look a list
  or an object up in a dict, and raised; TypeScript's Map found nothing and
  refused the call. Some Python handlers changed state and then raised, so the
  change stayed and the call was not recorded.
- **Two readings of one value.** JavaScript reads `[]` and `{}` as true and
  Python reads them as false, so `request_revision: []` asked for a revision
  in one and declined the task in the other. `Number()` and `float()` each
  read strings the other refuses.
- **Two wordings of one refusal.** A value written into a message reads one
  way in each language: `undefined` and `None`, `[object Object]` and
  `{'a': 1}`.

Each case replays `setup` with deterministic ids and clock and sends one
envelope. A case under `refused` is answered with `response` and changes
nothing but the log: a refusal marked `recorded` adds its refusal entry
(SPECIFICATION §10.1), and the rest add none. A case under `accepted` is
answered with `response`. Its `note` says how the two disagreed.

Run them:

```bash
npx tsx --test packages/coordinator/tests/malformed_params_vectors.test.ts
python -m pytest -q packages/coordinator-py/tests/test_malformed_params_vectors.py
```
