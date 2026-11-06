# create-chap-app

Generate a CHAP project that runs against your own agent and your own data,
with a review desk, one profile setting and a `diff-profiles` command that
shows what each profile changes.

```bash
npx create-chap-app my-gate
cd my-gate && npm install && npm run demo
```

Three questions: the template, the profiles and the project name. The
prompt says which templates run on Node.js and which on Python, and what
each profile adds. Each question has a default, and the flags answer them
without a prompt:

```bash
npx create-chap-app my-desk --template support-desk --profiles core/1.0,review/1.0,modes/1.0 --yes
```

The generator copies the template, fills in the names, and prints how to run
the project, in commands that work in PowerShell and cmd as well as in a
POSIX shell. It installs nothing. A Node.js project depends on
`@brightbeamai/chap-server`, which holds the server and the desk, and the
code gate on `@brightbeamai/chap-code-gate` too, so `npm update` brings a
fix to them; the project keeps its configuration, its sample data, its
agent and the small entry points that start them. A Python project pins
`chap-coordinator`. Each says in its README what to install.

The reviewer is named by `--human` (a participant URI, `human:you@local` by
default), `--human-name` and `--human-email`, which default to `git config
user.name` and `user.email`; a reviewed commit names its reviewer by that
name and email. `--agent` names the agent's URI. For the code gate,
`--review-at push` reviews an agent's branch before it is pushed, where the
default, `commit`, reviews each change before it is committed.
`npx create-chap-app --help` lists every flag.

## Upgrading a project

```bash
npx create-chap-app@latest upgrade my-gate --dry-run
npx create-chap-app@latest upgrade my-gate
```

Upgrade brings a project this generator wrote, from 0.3.1 on, up to this
release, and leaves the project its own:

- The CHAP packages move forward: the `@brightbeamai/chap-*` dependencies
  in `package.json`, or the `chap-` lines of `requirements.txt`, take the
  ranges the template pins now, and the packages it has gained are added.
- A file the template ships, or a `package.json` script, is replaced only
  where it is still as a release wrote it, by its hash, and a file the
  template has gained is added. A file a release wrote that the template no
  longer ships is removed on the same condition: a project from 0.3.1 to
  0.3.4 carried the server, the desk and the gate's library as copies, which
  `@brightbeamai/chap-server` and `@brightbeamai/chap-code-gate` hold now.
  `hooks/` and `gate-hook.mjs` stay whatever they hold, since a repository
  hooked up before 0.3.5 runs them by path.
- Every file kept because it differs is named, and `--diff` shows how with
  `git diff`. `chap.config.json` keeps its settings; upgrade brings its
  `template` stamp up to date and names each setting the template now
  writes differently.

A project from before the stamp is recognised by the released copies it
holds; `--template` names its template where none are left. `--dry-run`
says what would change and changes nothing.

## The templates

| Template | Runs on | What it does |
|---|---|---|
| `code-gate` | Node.js | Every change a coding agent makes in a git repository is proposed as a patch, reviewed as a diff, signed and committed only once approved, or, for an agent that commits on its own, its branch is reviewed commit by commit before it is pushed. Each governed commit names its reviewers by name and email and the model that wrote it, with the evidence beside it, a verifier for CI, and insights on what reviewers correct. Claude Code, Cursor, the built-in agent or any agent that can run a command. `npm run demo` runs it all. |
| `mcp-gate` | Node.js | Claude Desktop, Cursor or Claude Code connects to an MCP server over HTTP. Every call that changes state is recorded, and work that requires review waits for you in the desk. No API key needed. |
| `support-desk` | Python | Tickets from a CSV. The agent drafts a reply to each, the draft waits for your decision, and the reply as you decided it is written to `replies/`. |
| `outbound-approval` | Python | Drafts are held until the named approver decides, and only an approved message reaches `outbox/`. Trial mode requires review whatever the agent says, and a pause stops the agent. |
| `production` | Node.js | The Handbook's production set: signed calls, a token presented at join verified against its issuer, the chain on, SQLite on a volume. A coordinator service, an agent service with its own key, the desk signing in the browser, and a `doctor` that checks the setup. |

`npx create-chap-app --list` prints them.

## What every project has

- `README.md`: how to run it, how to attach your own agent and data, what
  each chosen profile changes in this project's own chain, and what the
  deployment supplies.
- `chap.config.json`: the workspace, the participants, the profiles and the
  store path. A profile change is one edit. Its `template` entry names the
  template and the create-chap-app release that wrote the project.
- A desk: one process owns the SQLite store, serves the review desk and
  answers CHAP calls at `POST /chap`. The agent is a separate process. The
  desk has a review view shaped by the artefact (a diff for code, a letter
  for a message), the chain as activity, and insights on what reviewers
  accept, edit and send back.
- `analytics.py`: the `chap-analytics` report, the evaluation cases and a
  refinement page from the project's store, linked from the desk.
- An agent: yours, through an MCP client, a framework bridge or `POST /chap`.
  Where a template drafts text, it uses the model named in the environment
  (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY` or `OLLAMA_URL`) and a scripted
  agent when none is named.
- Tests that run with no model and no network.
- `diff-profiles`: the project's workload under two profile sets, side by
  side, with the code of each refusal. `docs/profile-explorer.md` in the
  CHAP repository collects these rows from every template.

The decision is always yours, in the desk. The tests and `diff-profiles`
script decisions because nobody is at the desk when they run; the project
itself never does.

[DESIGN.md](./DESIGN.md) is the design and the rules the templates keep to.
