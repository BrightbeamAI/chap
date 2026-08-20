# CHAP 0.2.10: MCP registry entry, and two verdicts that changed

CHAP is now listed on the official [MCP Registry](https://registry.modelcontextprotocol.io)
as `io.github.BrightbeamAI/chap`, and the MCP server is something a client can
launch rather than a library you have to wire up yourself.

Read the next section before upgrading. Two calls behave differently from
0.2.9, and the usual pins resolve this release automatically.

## If you are upgrading

`^0.2.9` on npm and `>=0.2.9` on PyPI both resolve 0.2.10, so an ordinary
install picks these up with no prompt.

**`review.request` now refuses a substituted artefact.** Requesting review a
second time on a task that already has an open review, with different content,
returns `-32014` instead of quietly replacing the artefact under review. The
old behaviour discarded a review a human might already have been part-way
through. Requesting again with the *same* artefact still works and widens the
reviewer set, returning `amended: true`. Reported by Iman Schrock (#72).

**`audit.verify_chain` no longer reports a pass over a range it did not
check.** A workspace can enable chaining part-way through its life, and
verification used to replay from the first chained entry and return `ok: true`
with a smaller `entries_checked` beside it. Four entries with three written
before chaining returned a pass having checked one. The verdict is now one of
three terminal outcomes: a broken chain is still an error, a log with entries
outside coverage returns `status: "not_evaluated"` with `ok: false` and
`reason: "unchained_prefix"`, and `verified` requires complete coverage. `ok`
is `true` only alongside `verified`, so code reading `ok` alone now fails
closed where it previously passed (#76).

Both changes fail closed. Neither can silently accept something the old
version rejected.

## Running the MCP server

```bash
npx -y @brightbeamai/chap-coordinator-mcp
```

That serves all 39 CHAP methods as MCP tools over stdio. In a client:

```json
{ "mcpServers": { "chap": {
    "command": "npx",
    "args": ["-y", "@brightbeamai/chap-coordinator-mcp"],
    "env": { "CHAP_DB_PATH": "~/chap.db" } } } }
```

`CHAP_DB_PATH` points at a SQLite file. Without it the coordinator runs in
memory and workspaces are lost when the client exits, which suits a trial and
does not suit real decisions. If the path is set and the store cannot be
opened, the server exits with the reason rather than starting in memory and
discarding what it was asked to keep. `CHAP_PROFILES` overrides the profile
set; new workspaces include `audit-scitt/1.0` by default so their chain starts
at the first entry, because a workspace that adds the profile later can never
chain-verify what came before it.

The package previously exported `makeChapMcpServer` and declared no `bin`, so
`npx` started nothing. Its peer dependencies are now real dependencies, which
is what makes the standalone launch resolve.

## Binding a decision to its content

`decide.approve`, `decide.reject` and `decide.override` accept an optional
`approved_artefact_digest`, the `sha256:` digest over the RFC 8785 (JCS)
canonicalisation of the artefact under review. A mismatch returns `-32074` and
records nothing, so a decision cannot be attributed to content the decider
never saw. Absent, behaviour is unchanged. Shipped as
[CEP-001](./ceps/CEP-001.md), from a conversation with Iman Schrock (#71).

## Also in this release

The store contract says plainly that it is single-writer, because running two
coordinators against one shared store loses entries and the surviving chain
still verifies. Characterisation tests pin that behaviour rather than leave it
to be rediscovered (#69).

`from_seq` and `to_seq` on `audit.verify_chain` are declared and honoured by
neither implementation. Supplying either is now refused rather than silently
answering the whole-log question with whole-log counts.

`AuditVerifyChainResult` described `valid` and `breaks`, neither of which any
handler has ever returned. The declared type now matches the wire.

The error registry, the `identity-vc/1.0` codes, the proposal-process naming
and a missing `MAINTAINERS.md` are all corrected, and the 0.2.7 changelog
section, 218 lines lost between two commits, is restored.

## 0.2.11

`@brightbeamai/chap-coordinator-mcp` only, published immediately after. The
registry grants a GitHub organisation namespace using the organisation's own
casing, `io.github.BrightbeamAI/*`, and proves npm ownership by reading
`mcpName` out of the published package. 0.2.10 carried a lowercase value and
npm does not allow a published version to be replaced. No behaviour change.
The coordinator and A2A packages stay at 0.2.10.

## Versions

| Package | Version |
|---|---|
| `@brightbeamai/chap-coordinator` | 0.2.10 |
| `@brightbeamai/chap-coordinator-mcp` | 0.2.11 |
| `@brightbeamai/chap-coordinator-a2a` | 0.2.10 |
| `chap-coordinator` (PyPI) | 0.2.10 |
| `chap-langgraph`, `chap-pydantic-ai`, `chap-llama-index`, `chap-ag2`, `chap-google-adk` | 0.2.10 |

The wire format is unchanged. Both coordinators remain at parity and answer a
shared probe suite identically. The profile identifier stays `audit-scitt/1.0`:
profile surfaces are expected to move before 1.0, and a bump now would imply a
stability guarantee the 0.x line does not offer.

Full detail in [`CHANGELOG.md`](./CHANGELOG.md).
