# Working in this repository through the CHAP code gate

You are `__AGENT_URI__` in the workspace `__WORKSPACE__`. Every change you
make here is reviewed by a person before it is committed, or, when this gate
reviews at push, before it is pushed. One command proposes your work: it
starts the gate if it is not running, brings the review to the reviewer's
screen, and waits for their decision. The rules:

1. **Never run `git commit`, `git push`, `git merge` or `git rebase` yourself**,
   unless this gate reviews at push (rule 8). The repository's hooks refuse
   a commit that has no approved proposal behind it, and a commit made
   another way is caught by the verifier.
2. **Work in the working tree as usual.** Edit files, run the tests, read
   what you need. Keep one change per task: the proposal is the whole
   working tree against HEAD.
3. **Write a context note for the reviewer** in `.git/CHAP_CONTEXT.md`
   (inside `.git`, so it is never committed), in Markdown, short:

   ```
   ## What was asked
   ## What changed, and why
   ## How it was tested
   ## Look closely at
   ```

   The desk shows it above your change. Say what you ran and what it showed.
4. **When the change is ready, propose it:**

   ```
   node "__PROJECT_DIR__/propose.mjs" "what the change does, in one line" --by claude-code --context .git/CHAP_CONTEXT.md --wait --commit --timeout 9
   ```

   Run it with the longest time limit your command tool allows; with
   `--timeout 9` it stops waiting within ten minutes. The commit names the
   model that wrote it, beside whoever approved it: the gate reads the model
   from your session. If the command says no model is named, run it again
   with `--model` and your model's name as people say it, such as
   "Claude Opus 5.5". Where the gate is committed in this repository as
   `tools/chap-gate`, the command is `node tools/chap-gate/propose.mjs` with
   the same arguments.
5. **Read the outcome by its exit code.**
   - **0**: approved and committed. If the reviewer edited your change,
     their version is committed, and the output holds a prompt that shows
     what they changed and why: make the same correction anywhere else it
     applies.
   - **3**: the reviewer asked for changes. The output holds their review as
     a prompt, between the lines `---- The review, for the agent to act on
     ----` and `---- end ----`: their note, their comments on lines with
     the code each is about, and the command to run again. Do what it
     asks, update the context note, and run the same command again; the
     same review carries on, and the reviewer sees what changed since they
     looked.
   - **4**: the reviewer has not decided yet. Run the same command again to
     keep waiting. Say nothing to the user about it unless they ask.
   - **2**: rejected. The output holds their reasons. Take the change out of
     the working tree and stop; ask before trying another approach.
6. **Keep to one change on one base.** An approval covers the change on the
   commit it was proposed against. Do not commit, rebase or merge anything
   else while a change waits; if the branch moves on, propose again.
7. **Do not propose what you were not asked for.** A reviewer who sees an
   unrelated change in the diff will send it back.
8. **When this gate reviews at push** (`"review_at": "push"` in its
   chap.config.json), you may commit as you go, with a message that says
   what each commit does. Keep the Co-Authored-By line naming your model
   that you add to a commit: the gate reads the model from it. Never push
   yourself. When the work is ready, write
   the context note as in rule 3 and propose the branch, which the reviewer
   reads commit by commit:

   ```
   node "__PROJECT_DIR__/propose-branch.mjs" origin/main..HEAD --by claude-code --context .git/CHAP_CONTEXT.md --wait --timeout 9
   ```

   Add `--push origin` when the person you work for asks for an approval to
   publish the branch. The exit codes are those of rule 5: on 3, amend the
   commits as the review asks (rebase, fix up, reword) and run the same
   command again; on 0, your commits are sealed with the review, and pushed
   with `--push`.
9. The gate also answers as an MCP server at `http://127.0.0.1:8791/mcp`
   with the `chap.*` tools, for reading your tasks and the chain. Reading
   is free; writing happens through the commands above.
