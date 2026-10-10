# Working in this repository through the CHAP code gate

You are `__AGENT_URI__` in the workspace `__WORKSPACE__`. Every change you
make here is reviewed by a person before it is committed. The rules:

1. **Never run `git commit`, `git push`, `git merge` or `git rebase` yourself.**
   The repository's hooks refuse a commit that has no approved proposal
   behind it, and a commit made another way is caught by the verifier.
2. **Work in the working tree as usual.** Edit files, run the tests, read
   what you need. Keep one change per task: the proposal is the whole
   working tree against HEAD.
3. **When the change is ready, propose it:**

   ```
   node __PROJECT_DIR__/propose.mjs "what the change does, in one line" --by claude-code --wait --commit
   ```

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
7. The gate also answers as an MCP server at `http://127.0.0.1:8791/mcp`
   with the `chap.*` tools, for reading your tasks and the chain. Reading
   is free; writing happens through `propose.mjs`.
