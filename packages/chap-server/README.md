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
- `notify`: tell people a review or a question waits, on the console and
  through a webhook (below); `desk_url` is the desk's address its links
  name, by default where the server listens.
- `allowed_hosts`: host names, with their ports, the server answers to
  besides its own loopback names, for a proxy or a Compose service.
- `max_envelope_bytes`: the largest call accepted, 1 MiB by default.
- `oidc`: `{ issuer, client_id }`: people sign in at the desk with this
  OpenID provider, and join only with its ID token (`identity-oidc/1.0`,
  below). A person's entry in `humans` may give `oidc_sub`, the subject the
  provider knows them by.
- `enrolment`: `false` turns off the code a person's first join needs
  (below).
- `mode`, `mode_ceiling`: the workspace's mode under `modes/1.0`.

The environment overrides it: `CHAP_CONFIG` names the file; `PORT`,
`CHAP_HOST` and `CHAP_DB_PATH` replace the port, the host and the store;
`CHAP_REQUIRE_SIGNATURES` (`1`, `true`, `humans`, `0` or `false`),
`CHAP_CHAIN` and `CHAP_MCP` (`1` or `true`) replace the flags; `CHAP_DESK_SIGNING` replaces `desk_signing` and `CHAP_DESK_URL` `desk_url`; `CHAP_ALLOWED_HOSTS` adds host names, comma separated; `CHAP_ENROLMENT_CODE` sets the enrolment code;
`OIDC_ISSUER`, `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET` set the sign-in.

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

## A person's first join

A person's first join holds their name for good, with the key it carries.
So where signatures are required and nobody signs in at an identity
provider, the first join of a person `chap.config.json` names needs the
enrolment code the server prints at start, in the `x-chap-enrolment`
header; without it the join is refused with `-32011` and
`data.reason: "enrolment"`, before the coordinator sees it. The server
prints the code with the people still to join:

```
  enrol    7KQ2M-X9D4T: the code human:you@local joins with the first time, or open http://localhost:8788/#enrol=7KQ2M-X9D4T
```

The desk takes the code from `#enrol=` in its address, and then removes it
from the address, or asks for it when a join is refused for want of it. A
member's later joins, and joins under any name but a configured person's,
need no code. The server makes a new code at each start, or takes
`CHAP_ENROLMENT_CODE`, of 8 letters or digits at least, and keeps it
beside the store in `enrolment-code`, readable by its own user alone. A
server started with `CHAP_ENROLMENT_QUIET=1`, as the code gate's commands
start one, prints where the code is kept and leaves the code itself out of
the log an agent reads; `chapHandler` prints it too, for a
desk mounted in a server of your own. Without the code, a program that
started first could join under a person's name with a key of its own and
approve its own work. Against a program running as the person's own user,
a coding agent with a shell among them, the code is no boundary, since it
reads the file and can change `chap.config.json`: pin the reviewers' keys,
as the code gate's trust policy does, or run the agent as another user.
`"enrolment": false` turns it off, for a deployment that authenticates
joins another way.

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
and so is one under a URI a member holds with no key, since such a member
takes no key later; the desk then signs nothing and says which. A
workspace made with signatures off joined the configured people with no
key, so a store from then is replaced before the desk can sign there. The
desk stops signing, too, when a decision is refused because the workspace
no longer holds its key (`-32070` to `-32072`). A key an earlier desk kept
in `localStorage` moves to IndexedDB on the first visit, under the id it
was registered with.

The desk reads the chain from `GET /api/chain?from_seq=&to_seq=` and its
verification from `GET /api/chain/verify`, which run the coordinator's
`audit.read` and `audit.verify_chain` in the server's process, so a read
needs no signature and the desk signs only what it sends in the reviewer's
name; with a passkey, each such call asks the reviewer. Like the rest of
`/api/`, these answer whoever reaches the server, unless people sign in
(below).

The browser key keeps the reviewer's authority from pages elsewhere, and
from a script that runs on the desk's address and leaves. A program running
as the reviewer's own user, a coding agent with a shell among them, can
have the browser sign with it, or copy it from the browser's files. A
passkey asks the reviewer for each signature, which such a program cannot
give; it can still put a request in front of them, so a passkey prompt the
reviewer did not expect is one to refuse.

## Signing people in

With `oidc` set, a person joins the workspace only with an ID token from
its issuer, for someone `chap.config.json` names, and the desk signs them
in to get one. The server is then reached from other machines, so it
starts only with `"require_signatures": true`, on a store created that way:
under `"humans"` the agent's calls are unsigned, and whoever reaches
`/chap` or `/mcp` could act as the agent. The desk sends the browser to the issuer with the
authorisation code flow and PKCE (S256), its `nonce` the RFC 7638
thumbprint of the key it signs with. The issuer sends it back with a code,
which the desk posts to `POST /api/oidc/token`; the server redeems it at
the issuer's token endpoint, with the client secret where one is set, which
never reaches the browser, checks the ID token and its nonce, and answers
with the token and the participant it is for. The desk then joins with the
token and its key, and the coordinator binds the key the nonce names.

The server reads the issuer's discovery document, which must name the
issuer exactly, and the keys at its `jwks_uri`; an issuer that is not
`https` is refused, unless it is on this machine. A token is checked for
its signature (RS256, ES256 or EdDSA), `iss`, `aud` (the client id, always
required), `azp`, `exp`, `nbf` and `sub`, and bound to a person: the one
whose entry gives its subject as `oidc_sub`, or the one its
`chap_participant_uri` names, for an issuer that knows CHAP. A token for
anyone else is refused, and the desk shows who signed in, with the subject
to add. The coordinator records the join's token as its SHA-256.

With `oidc` set, the read API (`/api/members`, `/api/reviews`,
`/api/tasks` and a task's evidence, `/api/chain` and its verification) and
the pages under
`/analytics/` answer a person signed in at the desk, by the `chap_session`
cookie the sign-in sets or an ID token in `Authorization: Bearer`, or a
member whose key signs the read, and answer anyone else with 401. A program
signs a read with `signedRead` from `@brightbeamai/chap-server/client`:

```js
import { signedRead, signerFromJwk } from "@brightbeamai/chap-server/client";

const signer = await signerFromJwk("agent:drafter", privateJwk);
const path = "/api/tasks";
const authorization = await signedRead(signer, { workspace: "wsp_support", from: "agent:drafter", path });
const tasks = await (await fetch(`http://localhost:8788${path}`, { headers: { authorization } })).json();
```

A signed read names its path and time, and holds for five minutes either
side of the server's clock. `/api/config` and `/api/health` answer
everyone, so the desk can find the issuer before anyone signs in.

`chap-server dev-issuer` serves the whole flow for local runs, on port
8793. Its authorisation endpoint signs in whoever the desk's `login_hint`
names, with no question asked, so it authenticates nobody, and its
discovery document says so, as `chap_development_issuer`; the desk then
sends the participant's URI as the hint, and `/api/config` marks the issuer
`development`. The production template's README has a recipe for a real
provider.

## Taking up a review

The first time a review is opened for the reviewer in a round, the desk
sends `review.acknowledge`, so the record shows when they took it up as
well as when they decided it, and the review shows who has. A passkey asks
for each signature, so with one the desk acknowledges only a review the
reviewer opens from the queue. A coordinator from before 0.3.5 answers
`-32601`, and the desk then sends none.

## Telling people a review waits

`notify` in `chap.config.json` tells the people a review or a question is
addressed to that it waits:

```json
"notify": { "console": true, "webhook": { "url": "https://hooks.example.org/chap", "secret": "..." } }
```

A notice goes out when a review opens, or opens again, by an accepted
`task.complete` or `review.request` that leaves its task under review, and
when a question is asked, by an accepted `whisper.ask`. `console` prints
its summary and link, and `webhook` POSTs it as JSON:

```json
{ "event": "review.requested", "workspace": "wsp_support", "task_id": "tsk_01J...", "kind": "draft",
  "from": "agent:drafter", "to": ["human:you@local"],
  "summary": "agent:drafter asks human:you@local to review draft tsk_01J...",
  "link": "http://localhost:8788/#task=tsk_01J...", "seq": 7, "at": "2026-11-06T09:30:00.000Z" }
```

A question's notice is `"event": "whisper.asked"`, with its `whisper_id`;
the desk shows the task it is about, and the answer is a `whisper.answer`
call. `X-Chap-Signature: sha256=<hex>` is the HMAC-SHA256 of the body
under `secret`: compare it with your own, in constant time, before acting
on a notice. The link opens the desk at the task and grants nothing, and
the summary names the participants, the task's kind and its id, and none of
the agent's text. `desk_url` puts the address people reach the desk at,
through a proxy say, in the link. A notice goes out once the call that
caused it is answered, and one the webhook refuses, or does not take
within ten seconds, is logged, naming the webhook by its origin alone, and
dropped.

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
chap-server dev-issuer [<port>]                 a development OIDC issuer on 8793, for tests and local runs only
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
| `@brightbeamai/chap-server/oidc` | the issuer's discovery document, the ID token check, and the binding of a token to a configured person, with no dependencies |
| `@brightbeamai/chap-server/dev-issuer` | a development OIDC issuer, with the authorisation code flow and PKCE, which authenticates nobody |
| `@brightbeamai/chap-server/desk/*` | the desk's own modules |

## Licence

Apache-2.0
