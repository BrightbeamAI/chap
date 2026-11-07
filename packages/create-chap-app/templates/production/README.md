# __PROJECT_NAME__

The Handbook's production set, running: `core/1.0`, `review/1.0`,
`modes/1.0`, `identity-oidc/1.0` and `security-signed/1.0`, with the chain
on. One coordinator process owns a SQLite store on a volume and refuses an
unsigned call from a person. An agent runs as a second process with its own key,
drafts a message for each row of `messages.csv`, and waits for your decision.
The review desk signs your decisions with a passkey where the browser has
one, and with an OpenID provider configured, signs you in there first. An
approved message is written to `outbox/`; a rejected one is written nowhere.

## Run it

Without Docker:

```
npm install
npm run keys        # the agent's Ed25519 key, written to keys/
npm start           # the coordinator, the desk and POST /chap on port 8790
npm run agent       # in a second terminal: drafts, submits, waits
```

Open the desk at the address on the `enrol` line `npm start` prints,
<http://localhost:8790/> with the enrolment code after `#enrol=`. The desk
joins you with a key it generates in the browser and lists the drafts
waiting for you. Your first join needs the code, and the desk asks for it
where the address lacks it, so the agent cannot join under your name
before you do; once people sign in at an identity provider (below), the
sign-in does that job and the server prints no code. Approve one as written,
edit it and override, or reject it. The agent sees the decision and writes
the approved message to `outbox/<task_id>.json`. Reject with "ask for a
revision" ticked and the agent drafts again with your note and submits the
new draft. Until you have opened the desk the agent holds each draft and
says so once, since a review with nobody to address it to is refused. Each
row of `messages.csv` carries an idempotency key made from its content, so
a restarted agent finds the tasks it opened before and opens no duplicates.

With Docker Compose:

```
npm install
npm run keys
docker compose up --build
```

The image runs Node 24 and installs what `package-lock.json` pins, with
`npm ci`; the `npm install` before it writes that file, and committing it
keeps every later build on the same versions. The coordinator keeps its
store in `./data`, the agent reads its key from `./keys` and writes to
`./outbox`, and the desk is at the same address. The
agent reaches the coordinator by its service name, which
`CHAP_ALLOWED_HOSTS` in the Compose file tells the coordinator to answer to.
Both containers run as the `node` user. The project ships `data/`,
`outbox/` and `analytics/`, each holding an empty `.gitkeep` that git keeps,
so the directories exist and are yours before the first
`docker compose up` mounts them, in a fresh clone too, and the containers
can write to them. `docker compose run --rm agent
node agent.mjs --once` runs the agent for the rows in `messages.csv` and
exits once each is decided.

The agent drafts with the model named by the environment: `ANTHROPIC_API_KEY`
for the Anthropic Messages API, `OPENAI_API_KEY` for the OpenAI Responses
API, `OLLAMA_URL` for a local model, or `CHAP_MODEL_PROVIDER` to choose one
by name. With none set it drafts from the brief with no model, and says so
on its console. Add rows to `messages.csv` to put your own messages through
it: `to`, `subject` and `brief`, one message per row.

## The agent's key

`npm run keys` generates an Ed25519 keypair and writes the private key as a
JWK to `keys/`, in a file named after the agent's URI and readable by its
owner only. It never overwrites a key file. The agent registers the public
half at `participant.join`, which the coordinator accepts unsigned, and
signs every later call. A re-join cannot change a member's key, so a new
key means `participant.rotate_key` signed with the old one, or a new
participant URI.

## The reviewer's key

Under `security-signed/1.0` the desk signs your decisions in the browser.
With `"desk_signing": "auto"`, the setting here, it signs with a passkey
where the browser has an authenticator built in that verifies you, such as
Touch ID or Windows Hello: the authenticator holds the private key and asks
for your fingerprint, face or PIN for each signature, so nothing signs in
your name without you. The desk makes the passkey when you ask it to, at
`http://localhost:8790/`, since a passkey is made for a name and never an
IP address, and registers its public key at `participant.join`. Where the
browser has no such authenticator, the desk makes an Ed25519 key in the
browser as a key that cannot be exported, keeps it in IndexedDB for the
desk's address, and says in a notice what that key cannot protect against:
a program running as you on this machine, an agent with a shell included,
can have the browser sign with it, or copy it from the browser's files.
`"webauthn"` asks for a passkey always, a security key or a phone included,
and `"browser-key"` for the kept key. chap-server's README says how each
signs.

Each key's id is its RFC 7638 thumbprint, and the signing badge opens a
panel with its kind and whole fingerprint. The desk reads the members the
server lists to see whether the workspace holds this key for you, and joins
you only while it does not; a join under a URI another key holds is
refused (`-32011`), and the desk then signs nothing and says so. A desk
from before 0.3.5 kept the key in `localStorage`, at `127.0.0.1`; open the
desk there and it moves the key into IndexedDB under the id it was
registered with, and goes on signing with it.

Run an agent with a shell as another user, in a container or in a sandbox
all the same: a passkey prompt you did not expect is one to refuse.
Clearing the browser's storage loses a browser key, and with it the member's
key: the desk then needs a new participant URI, or a
`participant.rotate_key` signed with the old key before it is cleared.

## Signing in with OIDC

With an issuer set, people sign in at the desk, and a person joins the
workspace only with an ID token from that issuer, for someone
`chap.config.json` names; the workspace advertises `identity-oidc/1.0`.
Without one the profile is left out, and the console says so, because the
coordinator refuses a workspace that advertises a check it does not make.

```json
"oidc": { "issuer": "https://login.example.org", "client_id": "chap-desk" }
```

or `OIDC_ISSUER` and `OIDC_CLIENT_ID` in the environment, as the Compose
file passes them. The sign-in:

1. The desk sends you to the issuer with the authorisation code flow and
   PKCE (S256), and with a `nonce` that is the RFC 7638 thumbprint of the
   key the desk signs with, your passkey or the browser key.
2. The issuer sends you back to the desk with a code. The server redeems it
   at the issuer's token endpoint, checks the ID token, and gives it to the
   desk.
3. The desk joins you with the token and that key. The coordinator binds
   the key whose thumbprint is the token's nonce, so the key is yours
   because your sign-in names it, and records the token as its SHA-256,
   never the token itself.

The server reads the issuer's discovery document,
`<issuer>/.well-known/openid-configuration`, which must name the issuer
exactly, and the keys it names (`jwks_uri`). A token must be signed with
RS256, ES256 or EdDSA by one of those keys, carry the issuer as `iss`, be
issued to the client id (`aud`, and `azp` where it names one), be inside
its validity window and name a subject. It must also be for a person
`chap.config.json` names: one whose entry gives the token's subject as
`oidc_sub`, or the one its `chap_participant_uri` names, for an issuer that
puts the participant's URI in its tokens. An account at the provider is
not by itself a way in: the first time you sign in, the desk shows the
subject the issuer gave you, to add to your entry in `humans`:

```json
"humans": [{ "uri": "human:you@example.org", "display_name": "You", "role": "reviewer", "oidc_sub": "248289761001" }]
```

Once a provider is set, the read API answers a person signed in at the
desk, or a member whose key signs the read, and refuses anyone else with
401. A person's join with no token, or with a token that fails a check, is
refused with `-32403`; one whose token is for someone other than the join
names, with `-32404`. A key rotation at the issuer costs one refused join:
the server fetches the keys again when a token names one it does not have.

**At the provider,** register a client for the desk:

- the redirect URI: the desk's address as people open it,
  `http://localhost:8790/` on this machine, or the `https://` address your
  proxy serves it at;
- a public client, with PKCE (S256), and the `openid` and `email` scopes;
- no client secret. Where a provider gives a web redirect only to a client
  with a secret, set it as `OIDC_CLIENT_SECRET`: the server sends it with
  the code, and the browser never sees it.

Notes from the providers' documentation:

- **Google.** The issuer is `https://accounts.google.com`. For a desk on
  this machine, a "Desktop app" client: Google takes a loopback redirect
  on any port, and the client secret it gives such a client is optional at
  the token endpoint. For a desk at its own `https://` address, a "Web
  application" client with the redirect URI registered, and its secret as
  `OIDC_CLIENT_SECRET`. Google writes its issuer into a token as
  `https://accounts.google.com` or `accounts.google.com`, and the server
  takes either.
- **Microsoft Entra ID.** The issuer is your tenant's,
  `https://login.microsoftonline.com/<tenant id>/v2.0`, since the server
  checks it against the discovery document exactly. Register the redirect
  URI under "Mobile and desktop applications", a public client, where
  `http://localhost` is accepted whatever its port, or under "Web" with a
  client secret. The server redeems the code, and Entra takes a
  single-page application's code from the browser alone, so that platform
  does not work here.

For local runs, `npx chap-server dev-issuer` starts a development issuer on
port 8793 and prints the two settings. It signs in whoever the desk asks
it to, with no question asked: it authenticates nobody, and its discovery
document and console say so. `tests/production.test.mjs` runs the whole
path against it, from the sign-in to a signed decision.

## doctor

```
npm run doctor
```

connects to `CHAP_URL` (default `http://127.0.0.1:8790/chap`) and prints one
line per check, `ok` or `FAIL`, exiting non-zero when any check fails. Run it
with the same `CHAP_DB_PATH` as the coordinator, since the store check reads
the file the coordinator writes. The checks:

- the server answers `/api/health`;
- `/api/config` says signatures are required, the chain is on, and the
  profiles include `security-signed/1.0`, and `identity-oidc/1.0` when an
  issuer is set;
- an unsigned `decide.approve` in the reviewer's name is refused with
  `-32070`;
- `workspace.describe`, signed as the agent with the key file, advertises the
  profiles the coordinator enforces and publishes a chain head;
- `audit.verify_chain` answers `verified` with `ok: true`;
- the coordinator reports a persistent store, and the store file exists and
  changes after a call (skipped with a note when `CHAP_DB_PATH` is
  `:memory:`);
- with an issuer set, a person's join with no token, and one with a garbage
  token, are each refused with `-32403`;
- with the development issuer, a token for the reviewer whose nonce names
  another key than the join brings binds nothing: the join is refused with
  `-32011`, and the reviewer's keys stay as they were. It needs the
  reviewer to have signed in at the desk, and is skipped with a real
  provider, where a token needs a person to sign in.

## The desk, insights and analytics

The desk has three views. Review shows each artefact by its shape, with
Approve, Request changes, Reject, and Edit, which approves your version and
records it as an override with your rationale. The first time a review is
opened for you in a round, the desk records that you have taken it up
(`review.acknowledge`), and the review shows who has. Activity reads the
chain with what each call did. Insights counts what you did with the agent's work: how
often it was accepted as written, edited, sent back and rejected, the time
to a first decision, and your own notes and rationales, each linked to its
task. `j` and `k` move through the queue, `a` (once you confirm), `r` and `x` decide, and `?`
lists the keys.

`npm run analytics` goes further with `chap-analytics`, the package that
reads a CHAP chain into documented tables. It reads the store under `data/`
and writes the package's interactive report, the evaluation cases and a
refinement page with the corrections ranked to `analytics/`, which the desk
links from Insights. It needs Python 3.10 or later and `pip install
chap-analytics`; `--watch 300` keeps the pages current.

## What each profile changes here

- `core/1.0`: the workspace, the members, the tasks and the chain they are
  recorded on.
- `review/1.0`: a draft waits for a decision, the agent cannot approve its
  own work (`-32011`), and an edit at the desk is recorded as an override
  with its patch and rationale.
- `modes/1.0`: the workspace runs in trial mode, so every task requires
  review whatever the agent asks for. The ceiling is `trial` as well, so a
  task that asks for `production` is refused (`-32040`); the ceiling is
  enforced on every workspace, and the profile adds the trial rule.
- `identity-oidc/1.0`: a person joins only with an ID token from the issuer
  for someone `chap.config.json` names, and the key the token's nonce names
  becomes their signing key (`-32403`, `-32404`).
- `security-signed/1.0`: a call that carries a signature is checked against
  the sender's registered key, and refused when it does not verify
  (`-32070`). `"require_signatures": "humans"` in `chap.config.json`, the
  default here, has every call from a person signed, so nobody decides as
  the reviewer without the desk's key, and leaves an agent member's call
  unsigned where it chooses; the agent here signs every call all the same.
  `true` has every call after the join signed; `false` none. A coordinator
  from before 0.3.5 has no such policy and treats "humans" as `true`.

The chain is on through the coordinator's option, so `audit.verify_chain`
checks it and `workspace.describe` publishes its head, without
`audit-scitt/1.0` in the profile list.

## diff-profiles

```
npm run diff-profiles -- --against core/1.0,review/1.0
```

runs the same workload under the configured profiles and under the set
given, in-process and with no model, and prints each call with its outcome
under both. The rows marked `differs` are what the profiles change.
`identity-oidc/1.0` is left out of the comparison, because the workload
presents no token and a workspace cannot advertise the profile without a
verifier; the tests cover what it changes. The decisions in it are scripted,
because it is a comparison and nobody is at the desk.

## Upgrading

`npx create-chap-app@latest upgrade` in this directory brings the project up
to the latest release. The CHAP packages move forward, and a file or a
script the template ships is replaced only where it is still as a release
wrote it. Every file kept because it differs is named, `--diff` shows how,
and `--dry-run` changes nothing.

## What the deployment supplies

- TLS in front of the coordinator, on every production transport
  (SPECIFICATION.md section 15.1). The coordinator answers under its own
  host names only, so the name the proxy passes goes in `allowed_hosts` in
  `chap.config.json` or in `CHAP_ALLOWED_HOSTS`; a browser request from
  another origin is refused.
- An OpenID provider people sign in at, registered as above. With it, the
  read API under `/api/`, which serves every task, the members and the
  chain, answers only a person signed in at the desk or a member whose key
  signs the read, as `agent.mjs` and `doctor.mjs` sign theirs; without it,
  the read API answers whoever reaches it, so put a login in front.
- Delivery beyond a webhook, and showing shadow-mode output only to its
  observers. `notify` in `chap.config.json` prints a line and POSTs a notice,
  signed with HMAC-SHA256, to a webhook when a review opens or a question is
  asked, with `desk_url` for the address reviewers reach the desk at; the
  receiver turns it into a message they read (chap-server's README shows the
  notice).
- Running more than one coordinator process: each workspace has one writer,
  so partition workspaces across processes and start a standby from the
  store on failover.
- Keeping the chain head somewhere the coordinator's operator cannot change
  it, through a SCITT receipt or a head published elsewhere, since whoever
  can write to the store can rewrite the log and recompute the chain
  (SECURITY.md).
- Rate limits and timeouts for each participant.

## Tests

`npm test` runs the whole path in-process on an in-memory store, with the
development issuer for the OIDC part. The tests and `diff-profiles` script
the decisions; the project itself never does.
