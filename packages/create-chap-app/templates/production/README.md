# __PROJECT_NAME__

The Handbook's production set, running: `core/1.0`, `review/1.0`,
`modes/1.0`, `identity-oidc/1.0` and `security-signed/1.0`, with the chain
on. One coordinator process owns a SQLite store on a volume and refuses
every unsigned call. An agent runs as a second process with its own key,
drafts a message for each row of `messages.csv`, and waits for your decision.
The review desk signs in the browser with a key it generates there. An
approved message is written to `outbox/`; a rejected one is written nowhere.

## Run it

Without Docker:

```
npm install
npm run keys        # the agent's Ed25519 key, written to keys/
npm start           # the coordinator, the desk and POST /chap on port 8790
npm run agent       # in a second terminal: drafts, submits, waits
```

Open <http://127.0.0.1:8790/>. The desk joins you with a key it generates in
the browser and lists the drafts waiting for you. Approve one as written,
edit it and override, or reject it. The agent sees the decision and writes
the approved message to `outbox/<task_id>.json`. Reject with "ask for a
revision" ticked and the agent drafts again with your note and submits the
new draft. Until you have opened the desk the agent holds each draft and
says so once, since a review with nobody to address it to is refused. Each
row of `messages.csv` carries an idempotency key made from its content, so
a restarted agent finds the tasks it opened before and opens no duplicates.

With Docker Compose:

```
npm run keys
mkdir -p data outbox analytics
docker compose up --build
```

The coordinator keeps its store in `./data`, the agent reads its key from
`./keys` and writes to `./outbox`, and the desk is at the same address. The
agent reaches the coordinator by its service name, which
`CHAP_ALLOWED_HOSTS` in the Compose file tells the coordinator to answer to.
Both containers run as the `node` user, so the directories you create are
yours and the containers can write to them. `docker compose run --rm agent
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

## The browser key

Under `security-signed/1.0` the desk generates an Ed25519 key in the
browser, keeps it in the browser's storage, registers its public half at
`participant.join`, and signs each decision with it. The private key never
leaves the browser. Clearing the browser's storage loses the key: the member
on record keeps the old public key, so the desk then needs a new participant
URI, or a `participant.rotate_key` signed with the old key before it is
cleared.

## OIDC

With `OIDC_ISSUER` set, the coordinator verifies the token a
`participant.join` presents against the issuer's keys, and the workspace
advertises `identity-oidc/1.0`. Without it the profile is left out, and the
console says so, because the coordinator refuses a workspace that advertises
a verification it does not perform.

```
OIDC_ISSUER=https://idp.example.org             # the token's iss, as the provider states it
OIDC_JWKS_URL=https://idp.example.org/keys      # the jwks_uri from the provider's /.well-known/openid-configuration
OIDC_AUDIENCE=chap-coordinator-prod             # the token's aud; unchecked when unset
```

`OIDC_JWKS_URL` defaults to the issuer followed by `/.well-known/jwks.json`.
Providers differ here, so take the value from `jwks_uri` in the provider's
discovery document.

The verifier in `lib/oidc.mjs` has no dependencies. It fetches the JWKS at
start, accepts RS256, ES256 and EdDSA, checks `iss`, `aud`, `exp` and `nbf`,
and fetches the JWKS again when a token names a key it does not hold, so a
key rotation at the issuer costs one refused join. A token that fails any
check is refused with `-32403`.

The token is an ID token in the shape
[`integrations/CHAP-with-OIDC-OAuth2.md`](https://github.com/BrightbeamAI/chap/blob/main/integrations/CHAP-with-OIDC-OAuth2.md)
describes: `iss`, `sub`, `aud`, `exp`, `auth_time`, `acr`, and for CHAP
`chap_participant_uri`, the URI the token authorises, and `cnf.jwk`, the
participant's Ed25519 public key with a `kid`. The coordinator pins
`cnf.jwk` as the member's signing key and ignores any `jwks` the join also
carries. A token whose `chap_participant_uri` names another participant, or
whose `sub` is not the member's recorded subject, is refused with `-32404`.

The coordinator verifies a token when a join presents one. The desk in this
project has no login and presents none. A deployment's login authenticates
the person, sends the browser key to the provider as `cnf.jwk`, and presents
the token at `participant.join`, as section 1 of the guide above shows.
`lib/dev-issuer.mjs` is a development issuer for the tests and for local
runs only. It signs any claims it is asked to and authenticates nobody.
`node lib/dev-issuer.mjs` starts it and prints the `OIDC_*` lines to export;
`tests/production.test.mjs` shows the whole path from a minted token to a
signed decision.

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
- an unsigned `task.create` as the agent is refused with `-32070`;
- `workspace.describe`, signed as the agent with the key file, advertises the
  profiles the coordinator enforces and publishes a chain head;
- `audit.verify_chain` answers `verified` with `ok: true`;
- the coordinator reports a persistent store, and the store file exists and
  changes after a call (skipped with a note when `CHAP_DB_PATH` is
  `:memory:`);
- with an issuer set, a join with a garbage token is refused with `-32403`.

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
- `identity-oidc/1.0`: a join that presents a token has it verified against
  the issuer's keys, and the token's `cnf.jwk` becomes the member's signing
  key (`-32403`, `-32404`).
- `security-signed/1.0`: every call except `workspace.create` and
  `participant.join` carries a signature that verifies against the sender's
  registered key, or is refused (`-32070`).

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

## What the deployment supplies

- TLS in front of the coordinator, on every production transport
  (SPECIFICATION.md section 15.1). The coordinator answers under its own
  host names only, so the name the proxy passes goes in `allowed_hosts` in
  `chap.config.json` or in `CHAP_ALLOWED_HOSTS`; a browser request from
  another origin is refused.
- Its own login, which authenticates each person, obtains the OIDC token
  with the browser key in `cnf.jwk`, and checks that each caller sends only
  its own `from`.
- Delivery: notifying the people named on a review, and showing shadow-mode
  output only to its observers. The desk polls the coordinator; nothing here
  pushes.
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
