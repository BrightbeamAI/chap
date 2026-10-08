# Profile: `whisper`

**Profile id:** `whisper/1.0` · **Depends on:** Core

The `whisper` profile adds a deadline-bound interrupt-style question
channel: an agent (or anyone) can ask a quick, narrow question with
a defined default behaviour if no answer arrives in time. Distinct
from review (no artefact required) and from free-form messaging (a
typed answer is expected).

---

## 1. New methods

| Method            | Type    | Summary                                            |
|-------------------|---------|----------------------------------------------------|
| `whisper.ask`     | request | Pose a deadline-bound question with options and default. |
| `whisper.answer`  | request | Answer a whisper.                                  |

---

## 2. `whisper.ask`

```json
{
  "method": "whisper.ask",
  "params": {
    "workspace":         "wsp_support_triage",
    "from":              "agent:triage-bot",
    "to":                "human:alice@example.org",
    "ts":                "2026-05-17T14:22:01Z",
    "task_id":           "tsk_…",
    "question":          "Customer asked to cancel order ORD-91331 but has two active orders. Cancel only the named one, or ask for confirmation?",
    "options": [
      { "id": "cancel_named_only",     "label": "Cancel only ORD-91331" },
      { "id": "confirm_with_customer", "label": "Ask the customer about both orders" }
    ],
    "deadline_ms":       60000,
    "default_if_lapsed": "confirm_with_customer",
    "urgency":           "medium"
  }
}
```

| Field               | Purpose                                                |
|---------------------|--------------------------------------------------------|
| `to`                | Required. The participant or participants asked. A `workspace:` or `group:` URI lets any workspace member answer. |
| `task_id`           | Required. The task the question is raised against, which must exist. |
| `question`          | Required. The plain-English question.                  |
| `options`           | A closed set of typed answers. The answer must match.  |
| `deadline_ms`       | Required. Time budget in milliseconds from the ask. Once it passes, the next lapse check applies the default. |
| `default_if_lapsed` | Required. The option id used if no answer arrives in time. |
| `urgency`           | `low` / `medium` / `high`. Hints UI prioritisation.    |

If no options are provided, the answer is free text, sent in `answer`
(or its alias `answer_text`) on `whisper.answer`. This is discouraged
because it defeats analytical aggregation.

---

## 3. `whisper.answer`

```json
{
  "method": "whisper.answer",
  "params": {
    "workspace":     "wsp_support_triage",
    "from":          "human:alice@example.org",
    "to":            "agent:triage-bot",
    "ts":            "2026-05-17T14:22:13Z",
    "whisper_id":    "01HZ…",
    "task_id":       "tsk_…",
    "answer_option": "cancel_named_only",
    "comment":       "Customer was explicit."
  }
}
```

Only a participant the whisper was addressed to in `to` may answer it.
A `workspace:` or `group:` entry in `to` admits any workspace member.
Any other answerer, and a caller of either method who is not a
workspace member, is refused with `-32011`.

`task_id` identifies the task the whisper was raised against and is
optional here. The Coordinator MUST record the envelope as it received
it, and MUST resolve the answer's task from the `task_id` held on the
whisper identified by `whisper_id`, disregarding any value the caller
supplied. An `audit.read` filtered by `task_id` therefore returns the
answer alongside the ask, so the thread is recoverable without knowing
the whisper id, and an answer cannot be filed against a task it did not
belong to. The Coordinator SHOULD echo the resolved `task_id` in the
response.

Writing the resolved value into the recorded envelope would make the
chained copy differ from the one the client sent, so under
`require_signatures` a `whisper.answer` would no longer verify against
its own signature.

---

## 4. Lapse handling

A Coordinator applies lapses when its lapse check runs, on a schedule
the deployment sets. The check marks each pending whisper whose
`deadline_ms` has passed as lapsed and MUST record a notification of
the lapse and the applied default. An answer that arrives after the
deadline and before the check is accepted. The notification MUST carry
the whisper's `task_id`, so a lapse (where the default is applied with
no human input) is visible on an `audit.read` filtered by task:

```json
{
  "method": "notify.message",
  "params": {
    "workspace": "wsp_support_triage",
    "from":      "service:coordinator@example.org",
    "to":        ["agent:triage-bot", "human:alice@example.org"],
    "ts":        "2026-05-17T14:23:01Z",
    "kind":      "whisper_lapsed",
    "whisper_id": "01HZ…",
    "task_id":    "tsk_…",
    "default_applied": "confirm_with_customer"
  }
}
```

The asker proceeds with the default. A lapsed whisper is itself
audit data, "this human was unreachable for this decision" is now
queryable.

---

## 5. When to use whisper vs. review

| Use whisper when…                            | Use review when…                              |
|----------------------------------------------|-----------------------------------------------|
| One human's answer is enough.                | Multiple humans must weigh in.                |
| The answer fits a closed set of options.     | The output needs editing/approval.            |
| Latency matters (seconds).                   | Quality matters (minutes-to-hours).           |
| The agent will continue working after.       | The task is essentially done.                 |
| No artefact is produced.                     | An artefact is the unit of approval.          |

Whispers compose with reviews: an agent can whisper to disambiguate
early, complete the task, then have it go through a normal review.

---

## 6. Error codes

| Code      | Meaning                                          |
|-----------|--------------------------------------------------|
| `-32011`  | The caller is not a workspace member, or the whisper was not addressed to it. |
| `-32020`  | Whisper has already been answered.               |
| `-32021`  | Whisper has already lapsed.                      |
| `-32022`  | Answer option not in the whisper's option set.   |

---

## 7. Worked example

Full walk-through in [`../examples/06-whisper-prompt.md`](../examples/06-whisper-prompt.md).
