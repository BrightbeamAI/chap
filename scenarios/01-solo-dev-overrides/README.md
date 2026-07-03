# Scenario 1: the solo dev who can't remember what they overrode

> Narrative: [`IN_PRACTICE.md` §1](../../IN_PRACTICE.md#1-the-solo-dev-who-cant-remember-what-they-overrode)
> · Profiles: `core/1.0`, `review/1.0`, `audit-scitt/1.0` · No framework · **good first issue**

## The story

You ship to GitHub and a code-review bot comments on every pull request.
You accept most of its comments, reject some, and rewrite a few before
merging. Three months in you have a vague sense the bot is "pretty good"
but couldn't tell a colleague which specific things it gets wrong.

With CHAP, every decision is a recorded envelope on a hash-linked audit
chain. When you edit a comment before merging, that is a `decide.override`
carrying the diff, a one-line rationale, and tags. Months later the
override history is a query, not a feeling, and because the chain is
hash-linked it is tamper-evident: nobody can quietly rewrite what was
decided.

## Run it

```bash
python3 scenario.py
```

That is the whole setup. The script has **no dependencies beyond the
standard library**: from a clone of the repo it imports the in-repo
`coordinator-py` automatically, and once `chap-coordinator` is on PyPI,
`pip install chap-coordinator` works with the same script. No network, no
services, no config.

## What you'll see

The script prints three things, each a distinct reason CHAP is worth more
than a log file:

1. **Is this record trustworthy?** It re-walks the hash chain the way an
   auditor would (recomputing `sha256(JCS(envelope) || prev_hash)` for
   every entry) and confirms it is intact, then shows that quietly editing
   one past decision on a copy breaks verification at that entry. A
   spreadsheet gives you no such guarantee.
2. **Reconstruct one override.** It pulls PR-472 back out of the chain and
   shows the exact JSON Patch you applied, the rationale, and the tags: not
   "the human changed something," but precisely what and why.
3. **The override learning report.** It tallies every override by tag and
   prints the breakdown. The punchline: two thirds of your edits are the
   same framework-signature false positive, so the next prompt you ship for
   the bot names that pattern instead of guessing.

### Output shape

```
1. Is this record trustworthy?
   Audit entries on the chain: 43
   Chain verifies (hash-linked, intact): True
   If one decision were quietly edited: verifies = False (breaks at seq 18)

2. Three months later: what did I change on PR-472, and why?
   edit (JSON Patch): [{'op': 'replace', 'path': '/comments/0/severity', 'value': 'info'}]
   rationale:         Bot flags unused parameter on every event handler...
   tags:              ['false-positive', 'framework-pattern-misread']

3. Override Learning Report (wsp_my_reviews)
   Total overrides: 4
   By tag:
     false-positive             ###      3  (75.0%)
     framework-pattern-misread  ###      3  (75.0%)
     cosmetic-pref              #        1  (25.0%)
```

The sample set is small so the run is instant; the point is the pattern,
not the volume. A real workspace accumulates hundreds of decisions and the
same three queries surface the dominant failure modes.

## CHAP methods used

| Method | Role in the story |
|---|---|
| `workspace.create` | Open the workspace with `core/1.0` + `review/1.0` + `audit-scitt/1.0` (the last adds the hash-linked chain). |
| `participant.join` | Join the developer (`human:me@local`) and the bot (`agent:cursor@local`). |
| `task.create` | Open a `code_review` task for a PR, assigned to the bot. |
| `task.update` | The bot reports `in_progress`. |
| `task.complete` | The bot records its review as the task's output artefact. |
| `review.request` | The bot asks the developer to review, addressed `to` the developer. |
| `decide.approve` / `decide.reject` / `decide.override` | The developer's decision; overrides carry `diff`, `rationale`, `tags`. |
| `audit.read` | Read the chain, filter to `decide.override` and `task.create`, and verify the links. |

Note the authorisation shape every scenario must respect: the developer is
joined before they decide, and the review is addressed to the developer who
then decides it. A decision from a non-member, or from a member who was not
an addressed reviewer, is refused (`-32011`).
