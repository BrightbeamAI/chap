# Canonical control.snapshot conformance vectors

`control-snapshot-vectors.json` is the shared golden fixture for issue #148.
Both coordinator test suites replay the same envelopes with deterministic IDs,
a deterministic clock, and audit-chain recording enabled. They compare the
entire final response and chain head, including the exact content hash, against
committed values. No normalization hides missing fields or alternate nesting.

The nine cases cover default and individual slice selection, all five slices,
Unicode labels, empty-field omission, cancelled-task exclusion, and the small
member/task projections. Expected projections were checked independently and
the fixture hashes checked with SHA-256 over compact, sorted-key UTF-8 JSON
(all object keys in these vectors are ASCII and all numbers are safe integers).

Run the fixtures and snapshot lifecycle regressions:

```bash
npx tsx --test packages/coordinator/tests/snapshot_conformance.test.ts
python -m pytest -q packages/coordinator-py/tests/test_snapshot_conformance.py
```

The normal TypeScript/Python workspace test commands also run these files.
Additional lifecycle tests cover all terminal task states, response/live-state
isolation, rollback followed by another mutation, canonical store restart, and
legacy store-record normalization. The differential fuzzer now includes
snapshot and rollback actions; empty-selection behavior remains separate in
#151 and #152.
