# Changelog

`chap-analytics` versions on its own track. It is a reader of the protocol,
so a protocol release moves the coordinator packages together and leaves this
one where it is until the tables change.

## 0.1.0

First release: stage one of the analytics roadmap.

- Eleven documented tables, projected from a CHAP audit chain by replaying
  the envelope stream: `events`, `tasks`, `decisions`, `overrides`,
  `patch_ops`, `participants`, `deliberations`, `votes`, `whispers`,
  `handoffs`, `routing`. Every column declared with its dtype and provenance.
- Four loaders: a SQLite store, a JSON export, an `audit.read` endpoint, and
  a `Coordinator` in the same process.
- Review passes kept apart, so a task sent back for revision is measured from
  each pass's own opening and `outcome` reads from the decision that settled
  the last one.
- The corrected artefact reconstructed from the patch with the coordinator's
  own RFC 6902 semantics, and checked against what the coordinator stored by
  the differential suite.
- Successors minted by escalation and supersession counted as tasks, with
  what they inherit from the original.
- `id_certain` and `assignee_certain`, so a row says which of its values are
  inferred.
- A redactor hook that covers every artefact-bearing field, in both the
  envelope stream and the snapshot.
- `chap_analytics.sample.support_desk()`, a generated week of review work; a
  walkthrough notebook and a printed report over it.
- A differential suite: random workspaces against a live coordinator, read
  both ways and checked against what the coordinator holds.
