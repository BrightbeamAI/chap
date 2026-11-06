# @brightbeamai/chap-server

The server a CHAP project runs. One process owns the coordinator and its
store and serves:

| Path | What |
|---|---|
| `GET /` | the review desk, from this package's `desk/` |
| `POST /chap` | CHAP calls as JSON-RPC, for any agent or client |
| `GET /api/...` | what the desk reads: the open reviews, the tasks, a task's evidence, the members and their keys, the chain and its verification, the configuration and the server's health |
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

- `template`: the template and the create-chap-app release that wrote the
  project, which `create-chap-app upgrade` reads; the server leaves it alone.
- `store`: a SQLite file, or `":memory:"` to keep nothing. A relative path is
  taken from the project's directory, wherever the server is started, and a
  leading `~` is the home directory; the banner prints the path in full.
- `require_signatures`: `"humans"` has every call from a person signed and
  leaves an agent member's unsigned where it chooses, so the desk signs and
  `/mcp`, which acts as the agent, needs no key; `true` has every call after
  `participant.join` signed (`security-signed/1.0`); `false` none. The
  coordinator option `signaturePolicy` carries "humans"; a coordinator from
  before 0.3.5 has no such policy and treats it as `true`. An unsigned call
  is taken only from a member, so under `"humans"` the server joins the
  agent the first time `/mcp` is used, with the public key of its key file
  in `keys/` (or the file `CHAP_AGENT_KEY` names) where there is one, so an
  agent that also signs keeps its key. A workspace keeps the policy it was
  made with: the console says so at start where the store holds one made
  under other settings, and `/api/config` gives the policy it runs.
- `chain`: the hash chain over the log; `audit-scitt/1.0` turns it on too.
- `mcp`: serve `/mcp`, which acts as `agent.uri` with the tools the agent's
  own work needs, `AGENT_MCP_TOOLS`.
- `desk_signing`: how the desk signs where signatures are required,
  `"auto"`, `"webauthn"` or `"browser-key"` (the reviewer's key, below).
- `allowed_hosts`: host names, with their ports, the server answers to
  besides its own loopback names, for a proxy or a Compose service.
- `max_envelope_bytes`: the largest call accepted, 1 MiB by default.
- `oidc`: `{ issuer, jwks_url, audience }` verifies an OIDC token presented
  at `participant.join` (`identity-oidc/1.0`).
- `mode`, `mode_ceiling`: the workspace's mode under `modes/1.0`.

The environment overrides it: `CHAP_CONFIG` names the file; `PORT`,
`CHAP_HOST` and `CHAP_DB_PATH` replace the port, the host and the store;
`CHAP_REQUIRE_SIGNATURES` (`1`, `true`, `humans`, `0` or `false`),
`CHAP_CHAIN` and `CHAP_MCP` (`1` or `true`) replace the flags; `CHAP_DESK_SIGNING` replaces `desk_signing`; `CHAP_ALLOWED_HOSTS` adds host names, comma separated;
`OIDC_ISSUER`, `OIDC_JWKS_URL` and `OIDC_AUDIENCE` set the token verifier.

## What it refuses

The server answers under its own host names only (421 otherwise), so a page
that rebinds its own name to this address gets nothing. It refuses a browser
request from another origin (403), takes JSON only on `POST /chap` (415),
and reads no body over the envelope limit (413).

Every answer carries these headers, which the package exports as
`SECURITY_HEADERS`, and the analytics pages' policy as `ANALYTICS_POLICY`:

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

## Where it listens

On the loopback address, the default, the server listens on 127.0.0.1 and
on ::1 at one port, and prints the desk at `http://localhost:<port>/`. The
desk may sign with a passkey, and WebAuthn takes a domain name for its
relying party, never an IP address, so the desk is opened at `localhost`.
That name resolves to either address first, so the server answers on both,
and no other program can answer on the one it left free; a port taken on
either is named at start. On a wildcard address the desk is printed at
`localhost` too, and on any other host at that host.

## The reviewer's key

Under `security-signed/1.0` the desk signs what it sends in the reviewer's
name. `desk_signing` in `chap.config.json`, or `CHAP_DESK_SIGNING`, says
with what:

- `"webauthn"`: a passkey. The browser's authenticator holds the private
  key, and signs only once the reviewer is verified, by a fingerprint, a
  face or a PIN, each time. The desk makes the passkey when the reviewer
  asks it to, and registers its public key at `participant.join` as a
  P-256 JWK (`kty` EC, `alg` ES256) whose `webauthn` member names the
  relying party, the desk's host, and the one origin assertions may come
  from, the desk's own. It signs each call with
  `navigator.credentials.get`, user verification required, over the
  SHA-256 of the call's canonical form without its `sig`, and the call
  carries the assertion in a `webauthn:` tag, which the coordinator checks
  as a relying party does.
- `"browser-key"`: an Ed25519 key the desk makes in the browser as a key
  that cannot be exported, kept in IndexedDB for the desk's address. It
  signs without asking anyone. This is what a configuration that names
  nothing gets, as before 0.3.5.
- `"auto"`: a passkey where the browser has an authenticator built in
  that verifies its user, such as Touch ID or Windows Hello, and the
  browser key otherwise, with a notice in the desk of what that key cannot
  protect against. The code gate and production templates ship with it.

Each key is named by its RFC 7638 thumbprint, and the signing badge opens
a panel with its kind and whole fingerprint. A passkey is made for a
domain name, never an IP address, so the desk is opened at `localhost`
(above); one opened at 127.0.0.1 that wants a passkey goes there. Under
`"webauthn"`, a reviewer whose name the browser key holds moves it to a
passkey from the desk, with a `participant.rotate_key` the browser key
signs.

The desk reads the members from `GET /api/members` to see whether the
workspace holds its key for the reviewer, and joins only while it does
not. A join under a URI that another key holds is refused with `-32011`,
and the desk then signs nothing and says so; so it does when a decision is
refused because the workspace holds the key no longer (`-32070` to
`-32072`). A key an earlier desk kept in `localStorage` moves to IndexedDB
on the first visit, under the id it was registered with.

The desk reads the chain from `GET /api/chain?from_seq=&to_seq=` and its
verification from `GET /api/chain/verify`, which run the coordinator's
`audit.read` and `audit.verify_chain` in the server's process, so a read
needs no signature and the desk signs only what it sends in the reviewer's
name; with a passkey, each such call asks the reviewer. Like the rest of
`/api/`, these answer whoever reaches the server.

The browser key keeps the reviewer's authority from pages elsewhere, and
from a script that runs on the desk's address and leaves. A program running
as the reviewer's own user, a coding agent with a shell among them, can
have the browser sign with it, or copy it from the browser's files. A
passkey asks the reviewer for each signature, which such a program cannot
give; it can still put a request in front of them, so a passkey prompt the
reviewer did not expect is one to refuse.

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
