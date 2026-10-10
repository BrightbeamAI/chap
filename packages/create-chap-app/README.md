# create-chap-app

Generate a CHAP project that runs against your own agent and your own data,
with a review desk, one profile setting and a `diff-profiles` command that
shows what each profile changes.

```bash
npx create-chap-app my-gate
cd my-gate && npm install && npm run demo
```

Three questions: the template, the profiles and the project name. Each has a
default, and the flags answer them without a prompt:

```bash
npx create-chap-app my-desk --template support-desk --profiles core/1.0,review/1.0,modes/1.0 --yes
```

The generator copies the template, fills in the names, and prints how to run
the project. It installs nothing. Each project pins the published
`chap-coordinator` packages and says in its README what to install.

## The templates

| Template | Language | What it does |
|---|---|---|
| `code-gate` | TypeScript | Every change a coding agent makes in a git repository is proposed as a patch, reviewed as a diff, signed and committed only once approved, with the evidence beside the commit, a verifier for CI, and insights on what reviewers correct. Claude Code, Cursor, the built-in agent or any agent that can run a command. `npm run demo` runs it all. |
| `mcp-gate` | TypeScript | Claude Desktop, Cursor or Claude Code connects to an MCP server over HTTP. Every call that changes state is recorded, and work that requires review waits for you in the desk. No API key needed. |
| `support-desk` | Python | Tickets from a CSV. The agent drafts a reply to each, the draft waits for your decision, and the reply as you decided it is written to `replies/`. |
| `outbound-approval` | Python | Drafts are held until the named approver decides, and only an approved message reaches `outbox/`. Trial mode requires review whatever the agent says, and a pause stops the agent. |
| `production` | TypeScript | The Handbook's production set: signed calls, a token presented at join verified against its issuer, the chain on, SQLite on a volume. A coordinator service, an agent service with its own key, the desk signing in the browser, and a `doctor` that checks the setup. |

`npx create-chap-app --list` prints them.

## What every project has

- `README.md`: how to run it, how to attach your own agent and data, what
  each chosen profile changes in this project's own chain, and what the
  deployment supplies.
- `chap.config.json`: the workspace, the participants, the profiles and the
  store path. A profile change is one edit.
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
