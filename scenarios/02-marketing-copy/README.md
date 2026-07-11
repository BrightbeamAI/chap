# Scenario 2: marketing copy with one drafter and one editor

> Narrative: [`IN_PRACTICE.md` §2](../../IN_PRACTICE.md#2-marketing-copy-with-one-drafter-and-one-editor)
> · Profiles: `core/1.0`, `review/1.0`, `audit-scitt/1.0` · No framework · **good first issue**

## The story

A two-person marketing function adds an agent that turns a client brief
into a first draft. The editor edits and approves before the copy ships.
The agent is fine, but the editor keeps making the same kinds of edits,
softening corporate openers most of all, and the team retunes the prompt
every Friday from memory.

With CHAP, every brief is a task, the agent's first draft is an artefact,
and each editor revision is a `decide.override` carrying the diff, a
one-line rationale, and tags from a short controlled vocabulary. After two
months the "what do I keep fixing?" question is a query, not a Friday
guess: the tag histogram shows the opener is where most of the editing
goes, so the next prompt revision bans those patterns by name.

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
   every entry), confirms it is intact, then shows that quietly editing one
   past decision on a copy breaks verification at that entry. The copy that
   ships was approved by a named editor, provably.
2. **Reconstruct one edit.** It pulls the ACME brief back out of the chain
   and shows the exact JSON Patch the editor applied to the opener, the
   rationale, and the tags: not "the editor tweaked it," but precisely what
   and why.
3. **The override learning report.** It tallies every override by tag. The
   punchline: `opener-rewritten` dominates, so the next prompt revision
   names those opener patterns instead of guessing.

### Output shape

```
1. Is this record trustworthy?
   Audit entries on the chain: 58
   Chain verifies (hash-linked, intact): True
   If one decision were quietly edited: verifies = False (breaks at seq 33)

2. Two months later: what did the editor change on the ACME brief?
   edit (JSON Patch): [{'op': 'replace', 'path': '/sections/0/text', 'value': "We help teams ship faster. Here's how."}]
   rationale:         Opener was generic corporate boilerplate.
   tags:              ['opener-rewritten', 'tone-corporate-to-warm']

3. Override Learning Report (wsp_marketing)
   Total overrides: 8
   By tag:
     opener-rewritten        #####    5  (62.5%)
     tone-corporate-to-warm  ##       2  (25.0%)
     passive-to-active       ##       2  (25.0%)
     cliche-cut              ##       2  (25.0%)
```

The sample set is small so the run is instant; the point is the pattern,
not the volume. A real workspace accumulates hundreds of briefs and the
same three queries surface the dominant edit.

## CHAP methods used

| Method | Role in the story |
|---|---|
| `workspace.create` | Open the workspace with `core/1.0` + `review/1.0` + `audit-scitt/1.0` (the last adds the hash-linked chain). |
| `participant.join` | Join the editor (`human:editor@studio.com`) and the agent (`agent:copybot@studio.com`). |
| `task.create` | Open a `copy_draft` task for a brief, assigned to the agent. |
| `task.update` | The agent reports `in_progress`. |
| `task.complete` | The agent records its first draft as the task's output artefact. |
| `review.request` | The agent asks the editor to review, addressed `to` the editor. |
| `decide.approve` / `decide.reject` / `decide.override` | The editor's decision; overrides carry `diff`, `rationale`, `tags`. |
| `audit.read` | Read the chain, filter to `decide.override` and `review.request`, and verify the links. |

Note the authorisation shape every scenario respects: the editor is joined
before they decide, and the review is addressed to the editor who then
decides it. A decision from a non-member, or from a member who was not an
addressed reviewer, is refused (`-32011`).
