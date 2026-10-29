# The 90-second tour

A solo developer reviews pull requests with Cursor, and the bot flags a
warning the developer disagrees with. The clip shows the exchange in six
steps, in about 23 seconds, and the code for each step follows, in
TypeScript and Python. [`IN_PRACTICE.md`](../IN_PRACTICE.md#1-the-solo-dev-who-cant-remember-what-they-overrode)
tells the same story at more length.

<p align="center">
  <img src="img/hero.gif" alt="Six-step CHAP Core+Review walkthrough with a progress bar and step indicator across the top. Step 1: Setup (workspace, two participants, a task). Step 2: Drafting (agent drafts a response). Step 3: Pending review (review.request with the draft artefact). Step 4: Override (human disagrees: diff, rationale, tags). Step 5: Audit chain (hash-linked replay, prev_hash continuous). Step 6: Two months in (override learning report shows framework-pattern as the top tag, pointing the next prompt revision at the right problem)." width="100%">
</p>

## 1. Spin up a workspace

A coordinator with SQLite and the hash chain, and two participants.

<table>
<tr><th>TypeScript</th><th>Python</th></tr>
<tr><td valign="top">

```ts
import { Coordinator } from "@brightbeamai/chap-coordinator";
import { SqliteStore } from
  "@brightbeamai/chap-coordinator/storage/sqlite";

const coord = new Coordinator({
  store: new SqliteStore("./chap.db"),
  enableChain: true,
});

coord.api.workspace.create({
  workspace: "wsp_pr_reviews",
  profiles:  ["core/1.0", "review/1.0"],
});

coord.api.participant.join({
  workspace: "wsp_pr_reviews",
  from:      "human:me@local",
  type:      "human",
});

coord.api.participant.join({
  workspace: "wsp_pr_reviews",
  from:      "agent:cursor#v1",
  type:      "agent",
});
```

</td><td valign="top">

```python
from chap_coordinator import Coordinator
from chap_coordinator.storage.sqlite \
    import SqliteStore

coord = Coordinator(
    store=SqliteStore("./chap.db"),
    enable_chain=True,
)

def send(method, params):
    return coord.dispatch({
        "jsonrpc": "2.0", "id": method,
        "method": method, "params": params,
    })

send("workspace.create", {
    "workspace": "wsp_pr_reviews",
    "profiles":  ["core/1.0", "review/1.0"],
})

send("participant.join", {
    "workspace": "wsp_pr_reviews",
    "from":      "human:me@local",
    "type":      "human",
})

send("participant.join", {
    "workspace": "wsp_pr_reviews",
    "from":      "agent:cursor#v1",
    "type":      "agent",
})
```

</td></tr></table>

## 2. The bot drafts, you override

Your Cursor integration emits the envelopes.

<table>
<tr><th>TypeScript</th><th>Python</th></tr>
<tr><td valign="top">

```ts
// The review Cursor returned.
const cursorReview = {
  comments: [{ severity: "warning",
               body: "Unused parameter." }],
};

// The bot's review is the output of a task.
const { task_id } = coord.api.task.create({
  workspace: "wsp_pr_reviews",
  from:      "agent:cursor#v1",
  assignee:  "agent:cursor#v1",
  kind:      "code_review",
  input:     { pr_id: "PR-482" },
});

coord.api.task.complete({
  workspace: "wsp_pr_reviews",
  from:      "agent:cursor#v1",
  task_id,
  output:    cursorReview,
});

coord.api.review.request({
  workspace: "wsp_pr_reviews",
  from:      "agent:cursor#v1",
  task_id,
  artefact:  cursorReview,
  to:        "human:me@local",
});

// You disagree with one comment. Override it.
coord.api.decide.override({
  workspace:        "wsp_pr_reviews",
  from:             "human:me@local",
  task_id,
  intent_preserved: true,
  diff: [{ op: "replace",
           path: "/comments/0/severity",
           value: "info" }],
  rationale: "False positive. Framework " +
             "convention, not a bug.",
  tags: ["false-positive",
         "framework-pattern-misread"],
});
```

</td><td valign="top">

```python
# The review Cursor returned.
cursor_review = {
    "comments": [{"severity": "warning",
                  "body": "Unused parameter."}],
}

# The bot's review is the output of a task.
r = send("task.create", {
    "workspace": "wsp_pr_reviews",
    "from":      "agent:cursor#v1",
    "assignee":  "agent:cursor#v1",
    "kind":      "code_review",
    "input":     {"pr_id": "PR-482"},
})
task_id = r["result"]["task_id"]

send("task.complete", {
    "workspace": "wsp_pr_reviews",
    "from":      "agent:cursor#v1",
    "task_id":   task_id,
    "output":    cursor_review,
})

send("review.request", {
    "workspace": "wsp_pr_reviews",
    "from":      "agent:cursor#v1",
    "task_id":   task_id,
    "artefact":  cursor_review,
    "to":        "human:me@local",
})

# You disagree with one comment. Override it.
send("decide.override", {
    "workspace":        "wsp_pr_reviews",
    "from":             "human:me@local",
    "task_id":          task_id,
    "intent_preserved": True,
    "diff": [{"op":    "replace",
              "path":  "/comments/0/severity",
              "value": "info"}],
    "rationale": "False positive. Framework "
                 "convention, not a bug.",
    "tags": ["false-positive",
             "framework-pattern-misread"],
})
```

</td></tr></table>

> **The two surfaces.** TypeScript ships a typed facade, `coord.api.*`, so every method has autocomplete and compile-time checks. Python keeps the JSON-RPC envelope on the surface, `coord.dispatch({...})`, wrapped here in a `send()` helper, the idiom the Python tests use. Both send the same parameters in the same envelope, so the audit chain reads the same whichever client made the call.

## 3. Two months in

A script groups your overrides by tag, intent and reviewer.

The repository ships the script in both languages. It reads the audit chain over HTTP or straight from your SQLite file:

```bash
# TypeScript reference, against the SqliteStore from step 1:
$ npx tsx reference/core-plus-review/analyze-overrides.ts --db ./chap.db wsp_pr_reviews

# Python reference, same idea:
$ python3 reference/python/analyze_overrides.py --db ./chap.db wsp_pr_reviews

Override Learning Report
========================================
Workspace:       wsp_pr_reviews
Total overrides: 47

By tag:
  false-positive                       ████████████████████   31  (66%)
  framework-pattern-misread            ██████████████          22  (47%)
  cosmetic-pref                        █████                    8  (17%)

Intent breakdown:
  refining (same decision, better wording)   41
  substituting (different decision)          6

Top reviewers:
  human:me@local                            47

Hint: the most common tags are your next prompt revision targets.
```

The most common tags name what the next prompt revision for Cursor should fix.

[`chap-analytics`](../packages/chap-analytics/) reads the whole log back as
tables: [`ABOUT.md`](../ABOUT.md#reading-the-log-back) says how.
