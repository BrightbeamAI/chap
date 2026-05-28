# Example 05: Override capture

**Scenario.** A code-review agent has drafted PR review comments. A
senior engineer reviewing the bot's output agrees with most of it but
softens the tone on one comment and adds a compensating-control note
to another. The protocol captures her edits as a structured override
artefact (diff, rationale, tags) so the bot can learn from the
correction without anyone having to write a custom UI hook.

This example shows:

- The original draft artefact.
- A `decide.override` carrying a JSON-Patch diff with rationale and tags.
- The resulting override artefact.
- How override patterns are queried over time.

---

## 5.1 The original draft

The agent has produced PR review comments and called `task.complete`:

```json
{
  "id": "art_01HZB4M5K3X8M2V4N6P8R0T7A",
  "kind": "draft",
  "produced_by": "agent:code-reviewer#v1.4",
  "produced_at": "2026-05-17T13:45:01.221Z",
  "task": "tsk_01HZB4M5K3X8M2V4N6P8R0T7B",
  "content": {
    "pr_id": "repo/example-service#742",
    "summary": "Three style issues, one potential null-pointer in handler.go:142, and a missing test for the new branch.",
    "comments": [
      {
        "file": "handler.go",
        "line": 142,
        "severity": "error",
        "text": "This will dereference a nil pointer if `cfg.RetryPolicy` is unset. Add a nil check or initialise a default."
      },
      {
        "file": "handler.go",
        "line": 88,
        "severity": "warning",
        "text": "This function is doing too many things. Split it before it grows further."
      },
      {
        "file": "handler_test.go",
        "line": 1,
        "severity": "warning",
        "text": "No test for the new retry branch added in this PR. Coverage will regress."
      }
    ],
    "overall_recommendation": "request_changes"
  },
  "citations": [
    {
      "kind": "mcp_tool_invocation",
      "server": "mcp+https://tools.example.org/static-analysis",
      "tool": "lint",
      "call_id": "call_01HZB4M5K3X8M2V4N6P8R0T7C",
      "input_hash":  "sha256:1829…2937",
      "output_hash": "sha256:2937…3041"
    }
  ],
  "confidence": 0.84,
  "content_hash": "sha256:3041…4152"
}
```

---

## 5.2 The reviewer overrides

Maya, a staff engineer, agrees with the nil-pointer comment but
softens the "too many things" comment to be more constructive and
adds a note that the missing test is acceptable here because the
new branch is exercised by an existing integration test.

```json
{
  "chap": "0.2",
  "id": "01HZB4M5K3X8M2V4N6P8R0T7D",
  "ts": "2026-05-17T13:51:32.090Z",
  "workspace": "wsp_code_review",
  "from": "human:maya@example.org",
  "to":   "service:coordinator@example.org",
  "type": "request",
  "method": "decide.override",
  "params": {
    "task_id": "tsk_01HZB4M5K3X8M2V4N6P8R0T7B",
    "based_on_artefact_id": "art_01HZB4M5K3X8M2V4N6P8R0T7A",
    "override": {
      "diff": [
        {
          "op": "replace",
          "path": "/comments/1/text",
          "from": "This function is doing too many things. Split it before it grows further.",
          "to":   "Consider splitting this function   it's handling routing, validation, and dispatch, and a smaller decomposition would make the retry logic easier to test. Not blocking for this PR."
        },
        {
          "op": "replace",
          "path": "/comments/1/severity",
          "from": "warning",
          "to":   "info"
        },
        {
          "op": "replace",
          "path": "/comments/2/text",
          "from": "No test for the new retry branch added in this PR. Coverage will regress.",
          "to":   "The new retry branch is exercised by `integration_test.go::TestRetryWithBackoff`   confirmed by inspection. Coverage report misses cross-file flow. OK to merge."
        },
        {
          "op": "replace",
          "path": "/comments/2/severity",
          "from": "warning",
          "to":   "info"
        },
        {
          "op": "replace",
          "path": "/overall_recommendation",
          "from": "request_changes",
          "to":   "comment"
        }
      ],
      "rationale": "Nil-pointer comment is correct and well-targeted. Tone on the splitting comment was over-strong for a non-blocking suggestion. The 'missing test' was a false positive   there is integration coverage; the bot's lint tool doesn't see across files.",
      "tags": [
        "tone-softened",
        "severity-downgraded",
        "false-positive-corrected",
        "cross-file-coverage"
      ],
      "policy_refs": [
        "code-review-tone-guidelines-v2"
      ]
    }
  },
  "evidence": { "prev_hash": "sha256:4152…5263", "sig": "ed25519:maya-2026-05-17:…" }
}
```

The Coordinator validates and produces the override artefact:

```json
{
  "id": "art_01HZB4M5K3X8M2V4N6P8R0T7E",
  "kind": "override",
  "produced_by": "human:maya@example.org",
  "produced_at": "2026-05-17T13:51:32.090Z",
  "task": "tsk_01HZB4M5K3X8M2V4N6P8R0T7B",
  "based_on": "art_01HZB4M5K3X8M2V4N6P8R0T7A",
  "logical_id": "lgl_01HZB4M5K3X8M2V4N6P8R0T7A",
  "content": {
    "based_on": "art_01HZB4M5K3X8M2V4N6P8R0T7A",
    "logical_id": "lgl_01HZB4M5K3X8M2V4N6P8R0T7A",
    "intent_preserved": true,
    "diff": [ /* five patch ops as above */ ],
    "rationale": "Nil-pointer comment is correct and well-targeted. Tone on the splitting comment was over-strong for a non-blocking suggestion. The 'missing test' was a false positive   there is integration coverage; the bot's lint tool doesn't see across files.",
    "tags": ["tone-softened", "severity-downgraded", "false-positive-corrected", "cross-file-coverage"],
    "policy_refs": ["code-review-tone-guidelines-v2"]
  },
  "content_hash": "sha256:5263…6374"
}
```

The override carries the same `logical_id` as the original draft.
they are two versions of the same code-review artefact, and
`intent_preserved: true` because the reviewer kept the agent's
overall judgement (review the PR, comment on these three issues)
while refining tone and correcting one false positive. A reviewer
who had instead replaced the agent's "approve with comments" with
"request changes" would set `intent_preserved: false`. Both fields
are optional; they're useful when downstream analytics want to
distinguish *refined-by-human* from *replaced-by-human*.

The task is `completed`. The Coordinator notifies the agent:

```json
{
  "chap": "0.2",
  "id": "01HZB4M5K3X8M2V4N6P8R0T7F",
  "ts": "2026-05-17T13:51:32.180Z",
  "workspace": "wsp_code_review",
  "from": "service:coordinator@example.org",
  "to":   "agent:code-reviewer#v1.4",
  "type": "notification",
  "method": "notify.message",
  "params": {
    "kind": "override_captured",
    "task_id": "tsk_01HZB4M5K3X8M2V4N6P8R0T7B",
    "override_artefact_id": "art_01HZB4M5K3X8M2V4N6P8R0T7E"
  },
  "evidence": { "prev_hash": "sha256:6374…7485", "sig": "ed25519:coord-2026-05:…" }
}
```

---

## 5.3 Learning from overrides

Once a week, an analysis service queries the workspace for override
patterns:

```json
{
  "chap": "0.2",
  "id": "01HZC1AAAAAAAAAAAAAAAAAAA1",
  "ts": "2026-05-24T08:00:00.000Z",
  "workspace": "wsp_code_review",
  "from": "service:override-analyser@example.org",
  "to":   "service:coordinator@example.org",
  "type": "request",
  "method": "audit.read",
  "params": {
    "filter": {
      "artefact_kind": "override",
      "ts_range": { "from": "2026-05-17T00:00:00Z", "to": "2026-05-23T23:59:59Z" }
    },
    "aggregate": {
      "group_by": ["params.override.tags"]
    }
  },
  "evidence": { "prev_hash": "sha256:…", "sig": "ed25519:…" }
}
```

A typical aggregate result:

```json
{
  "result": {
    "ts_range": { "from": "2026-05-17T00:00:00Z", "to": "2026-05-23T23:59:59Z" },
    "total_overrides": 184,
    "by_tag": [
      { "tag": "tone-softened",                "count": 67 },
      { "tag": "false-positive-corrected",     "count": 41 },
      { "tag": "severity-downgraded",          "count": 39 },
      { "tag": "missing-context-added",        "count": 21 },
      { "tag": "cross-file-coverage",          "count": 16 }
    ],
    "by_agent": [
      { "agent": "agent:code-reviewer#v1.4",   "overrides": 184, "tasks": 612, "override_rate": 0.301 }
    ]
  }
}
```

That tells the team: a 30% override rate, mostly tone and severity
adjustments, with a clear false-positive pattern around cross-file
coverage. The next agent version targets exactly those failure modes.
The data was free; the protocol captured it as a side-effect of the
review flow.

---

## What this gives you

- **Structured diffs** instead of "the human changed something."
- **Rationale and tags** carried by the protocol, not buried in a
  UI's free-text field that's hard to reach later.
- **Aggregate override analytics** as a first-class operation.
  Improving the agent stops being a tribal-knowledge exercise.
- **Cryptographic linkage** between the original draft and the
  human-edited final, so a verifier can confirm exactly what was changed.

Move on to [`06-whisper-prompt.md`](./06-whisper-prompt.md) for the
fast, interrupt-style mid-task question.
