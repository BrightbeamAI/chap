# Working in this repository through the CHAP code gate

You are `__AGENT_URI__` in the workspace `__WORKSPACE__`. Every change you
make here is reviewed by a person before it is committed, or, when this gate
reviews at push, before it is pushed. The rules:

1. **Never run `git commit`, `git push`, `git merge` or `git rebase` yourself**,
   unless this gate reviews at push (rule 7). The repository's hooks refuse
   a commit that has no approved proposal behind it, and a commit made
   another way is caught by the verifier.
2. **Work in the working tree as usual.** Edit files, run the tests, read
   what you need. Keep one change per task: the proposal is the whole
   working tree against HEAD.
3. **When the change is ready, propose it:**

   ```
   node "__PROJECT_DIR__/propose.mjs" "what the change does, in one line" --by claude-code --model "the model you run as" --wait --commit
   ```

   Name your model as people say it, such as "Claude Opus 5.5": the commit
   carries it, beside the name and email of whoever approved the change.

   Where the gate is committed in this repository as `tools/chap-gate`, the
   command is `node tools/chap-gate/propose.mjs` with the same arguments.

   It opens a task as you, submits the patch for review, waits for the
   decision, and commits an approved change through the hooks, so the
   commit carries the CHAP trailers and the evidence note. It prints the
   address of the review.
4. **Read the outcome.** Exit code 0 means approved and committed; the
   reviewer may have edited the patch, and the working tree then holds
   their version. Exit code 3 means the reviewer asked for a revision and
   printed a note: act on the note, then propose again with the same
   summary, which goes to the same task. Exit code 2 means rejected: take
   the change out of the working tree and stop.
5. **Keep to one change on one base.** An approval covers the change on the
   commit it was proposed against. Do not commit, rebase or merge anything
   else while a change waits; if the branch moves on, propose again.
6. **Do not propose what you were not asked for.** A reviewer who sees an
   unrelated change in the diff will send it back.
7. **When this gate reviews at push** (`"review_at": "push"` in its
   chap.config.json), you may commit as you go, with a message that says
   what each commit does. Never push. When the work is ready, propose the
   branch, which the reviewer reads commit by commit:

   ```
   node "__PROJECT_DIR__/propose-branch.mjs" origin/main..HEAD --by claude-code --model "the model you run as" --wait
   ```

   Exit code 0 means approved: your commits are sealed with the review and
   the person who runs the gate pushes them. Exit code 3 means a revision
   was asked for: amend the commits as the note says (rebase, fix up,
   reword), then run the same command again. Exit code 2 means rejected:
   stop.
8. The gate also answers as an MCP server at `http://127.0.0.1:8791/mcp`
   with the `chap.*` tools, for reading your tasks and the chain. Reading
   is free; writing happens through `propose.mjs`.
