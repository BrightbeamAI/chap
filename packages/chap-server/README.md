# @brightbeamai/chap-server

The server a CHAP project runs. One process owns the coordinator and its
store and serves:

| Path | What |
|---|---|
| `GET /` | the review desk, from this package's `desk/` |
| `POST /chap` | CHAP calls as JSON-RPC, for any agent or client |
| `GET /api/...` | what the desk reads that the protocol does not carry: the open reviews, the tasks, a task's evidence, the configuration and the server's health |
| `GET /analytics/...` | the pages `chap-analytics` wrote into the project's `analytics/` |
| `POST /mcp` | an MCP server over streamable HTTP for the project's agent, when `mcp` is on |

Projects made with `create-chap-app` depend on it, so `npm update` brings a
fix to the server and the desk to every project at once.

```bash
npm install @brightbeamai/chap-server
npx chap-server --config chap.config.json
```

## The project

A project is the directory that holds its `chap.config.json`. The server
reads the file, serves the project's `analytics/` and names the directory
to the desk, which uses it in the commands it writes for an agent.

```json
{
  "workspace": "wsp_support",
  "profiles": ["core/1.0", "review/1.0"],
  "store": "./data/chap.db",
  "port": 8788,
  "humans": [{ "uri": "human:you@local", "display_name": "You", "role": "reviewer" }],
  "agent": { "uri": "agent:drafter", "display_name": "The drafter" },
  "mcp": false,
  "require_signatures": false,
  "chain": true
}
```

- `store`: a SQLite file, or `":memory:"` to keep nothing.
- `require_signatures`: whether every call after `participant.join` must be
  signed (`security-signed/1.0`); the desk signs in the browser.
- `chain`: the hash chain over the log; `audit-scitt/1.0` turns it on too.
- `mcp`: serve `/mcp`, which acts as `agent.uri` with the tools the agent's
  own work needs, `AGENT_MCP_TOOLS`.
- `allowed_hosts`: host names, with their ports, the server answers to
  besides its own loopback names, for a proxy or a Compose service.
- `max_envelope_bytes`: the largest call accepted, 1 MiB by default.
- `oidc`: `{ issuer, jwks_url, audience }` verifies an OIDC token presented
  at `participant.join` (`identity-oidc/1.0`).
- `mode`, `mode_ceiling`: the workspace's mode under `modes/1.0`.

The environment overrides it: `CHAP_CONFIG` names the file; `PORT`,
`CHAP_HOST` and `CHAP_DB_PATH` replace the port, the host and the store;
`CHAP_REQUIRE_SIGNATURES`, `CHAP_CHAIN` and `CHAP_MCP` (`1` or `true`)
replace the flags; `CHAP_ALLOWED_HOSTS` adds host names, comma separated;
`OIDC_ISSUER`, `OIDC_JWKS_URL` and `OIDC_AUDIENCE` set the token verifier.

## What it refuses

The server answers under its own host names only (421 otherwise), so a page
that rebinds its own name to this address gets nothing. It refuses a browser
request from another origin (403), takes JSON only on `POST /chap` (415),
and reads no body over the envelope limit (413).

Every answer carries these headers (`SECURITY_HEADERS`):

```
Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
```

The desk runs its own scripts and styles and nothing else, and no page can
frame it to take a click or a key press on it. The pages under
`/analytics/` are written by another program, so they are served with
`Content-Security-Policy: sandbox allow-scripts; frame-ancestors 'none'`:
their scripts run in an origin of their own, cannot read what the desk
keeps in the browser, and a call they make to the server carries a foreign
origin and is refused.

## The reviewer's key

Under `security-signed/1.0` the desk signs each call with an Ed25519 key it
makes in the browser as a key that cannot be exported, kept in IndexedDB for
the desk's address and named by its RFC 7638 thumbprint. Its signing badge
opens a panel with the whole fingerprint. After joining, the desk asks the
workspace with a signed `workspace.describe` whether it holds this key for
the reviewer, and while another key holds the reviewer's URI it signs
nothing and says so. A key an earlier desk kept in `localStorage` moves to
IndexedDB on the first visit, under the id it was registered with.

A program running as the reviewer's own user can drive their browser and
have it sign; a key that needs a gesture for each signature is what stops
that.

## What the desk marks

A character a reader cannot see, or one that changes how the text around it
reads, is shown as a marker naming its code point wherever the agent's text
is shown: every default-ignorable code point (zero-width characters, the
bidirectional controls, the soft hyphen, the Hangul fillers, variation
selectors, tag characters), every format character, line and paragraph
separators, control characters other than a tab, spaces other than the
ASCII space, and U+2800. A character outside ASCII in a file name, a branch
name or a changed line is outlined, unless it is a Latin-1 letter or common
punctuation such as curly quotes, dashes and the euro sign. Either kind
puts a warning above the artefact.

## In a server of your own

`chapHandler(options)` builds the coordinator from the configuration and
gives back a handler `(req, res, next)` for Node's `http` request and
response. `basePath` serves the desk, `/chap`, `/api/...`, `/analytics/...`
and `/mcp` under a prefix, so the application keeps the rest of its paths;
the desk's own paths are relative, so it works under any prefix. A path the
handler does not serve goes to `next`, and the host and origin checks apply
to the handler's own paths only.

Node's `http`:

```js
import { createServer } from "node:http";
import { chapHandler } from "@brightbeamai/chap-server";

const chap = await chapHandler({ configPath: "chap.config.json", basePath: "/review" });
createServer((req, res) => chap(req, res, () => myApp(req, res))).listen(3000);
```

Express, which takes the prefix off the path itself:

```js
app.use("/review", await chapHandler({ configPath: "chap.config.json" }));
```

Fastify, with the raw request and response; the handler reads the body
itself, so the routes take no body parser:

```js
const chap = await chapHandler({ configPath: "chap.config.json", basePath: "/review" });
app.register(async (scope) => {
  scope.removeAllContentTypeParsers();
  scope.addContentTypeParser("*", (req, payload, done) => done(null));
  const desk = (req, reply) => { reply.hijack(); return chap(req.raw, reply.raw); };
  scope.all("/review", desk);
  scope.all("/review/*", desk);
});
```

`createChapServer(options)` gives back `{ config, coord, handler, server,
listen(), close() }`, for a process that wants the coordinator beside the
handler. Both take `config` (what `loadConfig` returns) or `configPath`,
`coordinator` to serve a coordinator the caller made, and `basePath`. The
desk's page sits at the prefix with a slash, `/review/`; the prefix alone is
sent there.

## The bin

```
chap-server [--config <file>]                   the server for a project
chap-server keys [--uri <uri>] [--config <file>]  the agent's signing key, in keys/ beside the config
chap-server dev-issuer [<port>]                 a development OIDC issuer, for tests and local runs only
```

`chap-server keys` writes an Ed25519 key as a JWK readable by its owner
only, prints the public half the agent registers at `participant.join`,
and writes the same key in OpenSSH's format for signing git commits. It
never overwrites a key file.

## Modules

| Import | What |
|---|---|
| `@brightbeamai/chap-server` | `createChapServer`, `chapHandler`, `loadConfig`, `serve`, `isMain` and the pieces they are made of |
| `@brightbeamai/chap-server/client` | the desk's CHAP client: `makeClient`, signing, `contentHash`, `canonicalize`, `jsonPatch`. It runs in a browser and under Node. |
| `@brightbeamai/chap-server/keys` | the agent's key file and its OpenSSH form |
| `@brightbeamai/chap-server/providers` | model providers, one HTTP call each, and a scripted drafter |
| `@brightbeamai/chap-server/profiles-diff` | a workload under two profile sets, side by side |
| `@brightbeamai/chap-server/oidc` | an OIDC token verifier with no dependencies |
| `@brightbeamai/chap-server/dev-issuer` | a development OIDC issuer |
| `@brightbeamai/chap-server/desk/*` | the desk's own modules |

## Licence

Apache-2.0
