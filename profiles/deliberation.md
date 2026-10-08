# Profile: `deliberation`

**Profile id:** `deliberation/1.0` · **Depends on:** Core

Multi-party voting with quorum, weights, and vetoes. Distinct from
review (single-artefact approval) and whisper (single-person quick
answer), deliberation is "three of us need to agree on this."

---

## 1. New methods

| Method                | Type         | Summary                                              |
|-----------------------|--------------|------------------------------------------------------|
| `deliberate.open`     | request      | Open a thread with a defined rule and participants.  |
| `deliberate.comment`  | notification | Add context to an open deliberation.                 |
| `deliberate.vote`     | request      | Cast a vote.                                         |
| `deliberate.close`    | request      | Close the deliberation; compute the outcome.         |

---

## 2. Decision rules

A deliberation's `rule` field is one of:

| Rule                                  | Behaviour                                          |
|---------------------------------------|----------------------------------------------------|
| `any_one_approves`                    | First "yea" wins.                                  |
| `all_approve`                         | Every named voter must say "yea".                  |
| `quorum:N`                            | At least N voters must vote yea or nay; abstentions do not count towards N. A majority of those votes wins, and a tie is rejected. |
| `weighted_vote:T`                     | Weighted votes; threshold T (e.g. `2.0`).          |
| `weighted_vote_with_veto:T`           | Weighted votes; any veto-holder can block.         |

Custom rules MAY be supported by an implementation; clients MUST handle
`-32033` from `deliberate.open` when their named rule is unknown.

---

## 3. Worked example

```json
{
  "method": "deliberate.open",
  "params": {
    "workspace":      "wsp_release_decisions",
    "from":           "human:lee-eng-lead@example.org",
    "to":             ["human:lee-eng-lead@example.org", "human:morgan-security@example.org", "human:sam-product@example.org"],
    "ts":             "2026-05-17T15:10:01Z",
    "deliberation_id": "del_…",
    "question":       "Ship hotfix v4.2.1 today?",
    "rule":           "weighted_vote_with_veto:2.0",
    "weights":        { "human:lee-eng-lead@example.org": 1.0, "human:morgan-security@example.org": 1.0, "human:sam-product@example.org": 1.0 },
    "veto":           { "human:morgan-security@example.org": true },
    "deadline":       "2026-05-17T16:00:00Z"
  }
}
```

Weights come from the `weights` map set at `deliberate.open`, 1.0 for a
voter it omits. Only the participants named in `to` may vote. The
`deadline` is recorded and never enforced.

Vote:

```json
{
  "method": "deliberate.vote",
  "params": {
    "workspace":       "wsp_release_decisions",
    "from":            "human:sam-product@example.org",
    "deliberation_id": "del_…",
    "vote":            "yea",
    "comment":         "Risk acceptable; pre-approved category.",
    "veto_invoked":    false
  }
}
```

Close. Any workspace member may close a deliberation at any time, and
closing it again returns the stored outcome:

```json
{
  "method": "deliberate.close",
  "params": {
    "workspace":       "wsp_release_decisions",
    "from":            "human:lee-eng-lead@example.org",
    "deliberation_id": "del_…"
  }
}
```

Response, once all three have voted `yea`:

```json
{
  "result": {
    "outcome": "approved",
    "rule":    "weighted_vote_with_veto:2.0",
    "tally":   { "yea": 3, "nay": 0 },
    "vetoes":  []
  }
}
```

---

## 4. Vetoes preserve dissent

A veto with reasoning is permanently part of the audit log. The
minority view is not erased.

---

## 5. Error codes

| Code      | Meaning                                              |
|-----------|------------------------------------------------------|
| `-32030`  | Voter is not in the deliberation's participant list. |
| `-32031`  | Voter has already voted.                             |
| `-32032`  | Deliberation has already closed. The `deadline` is never enforced, so a deliberation does not lapse. |
| `-32033`  | Unknown decision rule.                               |

---

## 6. Worked example

Full walk-through in [`../examples/08-multi-human-deliberation.md`](../examples/08-multi-human-deliberation.md).
