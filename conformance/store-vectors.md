# Store vectors

`store-vectors.json` holds one workspace as each coordinator writes it to a
store, after the same calls. Both coordinators write the same audit entries,
and keep the workspace's objects in their own shapes:

| | TypeScript | Python |
|---|---|---|
| A collection | a list | an object keyed by id |
| A field with no value | left out | `null` |
| A history entry's sender | `from` | `from_` |
| A route decision's fields of its kind | at the top, beside `kind` | in `extra` |

Each coordinator restores either shape. Both test suites replay
`envelopes` with deterministic ids and clock and the chain on, check that
the snapshot they reach is their own vector, restore the other's, and check
that they hold the same state and carry on alike: the work's author is
refused the review, a person's approval completes it, and the chain heads
agree. The differential fuzzer checks the same both ways on every seed: each
coordinator opens the SQLite file the other wrote and projects the state it
holds (`conformance/differential/README.md`).
