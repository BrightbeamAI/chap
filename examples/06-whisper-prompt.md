# Example 06: Whisper prompt

**Scenario.** Mid-task, an agent hits an ambiguity it cannot resolve
on its own, a customer reply that could mean two different things.
Rather than guess or abstain, it sends a *whisper*: a quick,
deadline-bound, interrupt-style question to a nominated human. The
human answers in seconds. The agent finishes the task.

This example shows:

- `whisper.ask` with a deadline and a `default_if_lapsed`.
- `whisper.answer` from the human.
- What happens when no one answers in time.

Whispers are intentionally lightweight. They are not reviews. They
exist for "I need a one-bit answer, fast" situations that don't justify
the full review machinery.

---

## 6.1 The agent's situation

The agent is drafting a response to a customer who wrote:

> "Cancel order #ORD-91331."

…on a customer record that has two active orders. The agent has the
order numbers but the customer mentioned only one. The agent's
options:

1. Cancel only the named order and reply.
2. Reply asking for confirmation about both.
3. Send a whisper to the on-duty human.

The agent picks (3), the whisper costs little and beats both other
options.

---

## 6.2 The whisper

```json
{
  "chap": "0.2",
  "id": "01HZB7N6K3X8M2V4N6P8R0T8A",
  "ts": "2026-05-17T14:22:01.014Z",
  "workspace": "wsp_support_triage",
  "from": "agent:triage-bot#v3.2",
  "to":   "human:alice@example.org",
  "type": "request",
  "method": "whisper.ask",
  "params": {
    "task_id": "tsk_01HZB7N6K3X8M2V4N6P8R0T8B",
    "question": "Customer asked to cancel order #ORD-91331 but has two active orders. Cancel only the named one, or ask the customer to confirm both?",
    "options": [
      { "id": "cancel_named_only", "label": "Cancel only ORD-91331" },
      { "id": "confirm_with_customer", "label": "Ask the customer about both orders" }
    ],
    "deadline_ms": 60000,
    "default_if_lapsed": "confirm_with_customer",
    "urgency": "medium",
    "context_summary": "Customer record CUST-7K2M9; two active orders ORD-91331 (£87) and ORD-91402 (£212)."
  },
  "evidence": { "prev_hash": "sha256:7485…8596", "sig": "ed25519:k-2026-05-17a:…" }
}
```

The Coordinator routes this with the **`whisper` channel**: typically
an interrupt-style notification in the human's client, distinct from
normal review queues. The deadline starts ticking.

---

## 6.3 The human answers

Alice taps the response within 12 seconds:

```json
{
  "chap": "0.2",
  "id": "01HZB7N6K3X8M2V4N6P8R0T8C",
  "ts": "2026-05-17T14:22:13.224Z",
  "workspace": "wsp_support_triage",
  "from": "human:alice@example.org",
  "to":   "agent:triage-bot#v3.2",
  "type": "request",
  "method": "whisper.answer",
  "params": {
    "whisper_id": "01HZB7N6K3X8M2V4N6P8R0T8A",
    "task_id": "tsk_01HZB7N6K3X8M2V4N6P8R0T8B",
    "answer_option": "cancel_named_only",
    "comment": "Customer was explicit. If the second order matters, they'll tell us."
  },
  "evidence": { "prev_hash": "sha256:8596…9607", "sig": "ed25519:alice-2026-05-17:…" }
}
```

The agent resumes, cancels ORD-91331, and drafts a reply. Both the
whisper and the answer are recorded in the chain.

---

## 6.4 What if no one answers?

Whispers are deadline-bound. If Alice had been away from her desk, at
T+60s the Coordinator would emit:

```json
{
  "chap": "0.2",
  "id": "01HZB7N6K3X8M2V4N6P8R0T8D",
  "ts": "2026-05-17T14:23:01.014Z",
  "workspace": "wsp_support_triage",
  "from": "service:coordinator@example.org",
  "to":   ["agent:triage-bot#v3.2", "human:alice@example.org"],
  "type": "notification",
  "method": "notify.message",
  "params": {
    "kind": "whisper_lapsed",
    "whisper_id": "01HZB7N6K3X8M2V4N6P8R0T8A",
    "task_id": "tsk_01HZB7N6K3X8M2V4N6P8R0T8B",
    "default_applied": "confirm_with_customer"
  },
  "evidence": { "prev_hash": "sha256:…", "sig": "ed25519:coord-2026-05:…" }
}
```

The agent proceeds with the default. The lapse is recorded as a
first-class evidence entry, so the auditor can see exactly when a
human declined to be reachable for a given decision.

---

## 6.5 When to use whispers vs. reviews

| Use a whisper when…                         | Use a review when…                          |
|---------------------------------------------|---------------------------------------------|
| One human's answer is enough.               | Multiple humans must weigh in.              |
| The answer fits in a closed set of options. | The output needs editing/approving.         |
| Latency matters (seconds).                  | Quality matters (minutes-to-hours).         |
| The agent will continue working after.      | The task is essentially done.               |
| No artefact is needed.                      | An artefact is the unit of approval.        |

Whispers compose with reviews. An agent can whisper to disambiguate
early, complete the task, and then have the result go through a normal
review. The whisper appears in the audit chain immediately before the
task's `complete`.

---

## What this gives you

- **A bounded-latency interrupt channel** that is part of the protocol,
  not a side-channel.
- **Defaults that fail safely.** Every whisper carries an explicit
  fallback so the agent never blocks indefinitely on human
  responsiveness.
- **Whisper analytics.** "What fraction of cancellations involve a
  whisper?" is a single audit query.
- **Visible accountability for non-response.** A lapsed whisper is
  recorded with the timestamp and the default applied; nothing
  silently disappears.

Move on to [`07-handoff-shift-change.md`](./07-handoff-shift-change.md)
for transferring an in-progress task between humans.
