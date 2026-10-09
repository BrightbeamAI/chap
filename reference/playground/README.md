# CHAP Playground

A runnable demo: two humans (Maya and Sam) and one agent collaborating on
a customer-support queue, with
every message a real CHAP envelope, every override a real RFC 6902
JSON Patch, and every routing decision a real `route_decision`
artefact in the evidence chain.

The protocol code is the unmodified `@brightbeamai/chap-coordinator` library
imported from `packages/coordinator/`. This is not a simulation; the
wire format is the same one a production CHAP deployment would use.

---

## What it shows

- **Two humans, different roles.** Maya is front-line, she reviews
  every bot draft. Sam is senior, he sees only what the routing
  policy escalates.
- **Real CHAP envelopes.** Every action becomes a JSON-RPC envelope
  hitting `POST /rpc`. Open the **Show the wire** panel to watch the
  evidence chain accumulate in real time.
- **Real overrides.** Edit a draft; the diff is computed as RFC 6902
  JSON Patch in the browser; it's sent to the coordinator and stored
  as an override artefact with `rationale`, `tags`, and the full diff.
- **Real routing hints.** Every task carries `routing_hints`
  (criticality, deadline, risk_tier). The bot's drafts carry their
  own (confidence, model_id, latency_ms). A policy reads them and
  decides review depth + escalation.
- **Real two-way live updates.** Open Maya and Sam in two tabs. When
  Maya escalates, Sam's queue updates. When Sam overrides, Maya sees
  it. The transport is Server-Sent Events from the coordinator.
- **Real dividends.** Both Maya and Sam get their own override-tag
  aggregation, the protocol's tuning-data dividend, emerging from
  the user's own actions.

---

## Requirements

- **Node 20 or later.**
- A model is optional. With nothing configured, a scripted agent drafts
  every ticket, so the playground runs on a fresh machine. To draft with a
  model, set one of these before `npm start`:

  | Variable | Provider | Model |
  |---|---|---|
  | `ANTHROPIC_API_KEY` | Anthropic Messages API | `ANTHROPIC_MODEL`, default `claude-haiku-5-5` |
  | `OPENAI_API_KEY` | OpenAI Responses API | `OPENAI_MODEL`, default `gpt-5.5` |
  | `OLLAMA_URL` | A local Ollama server | `OLLAMA_MODEL`, default `gemma3:4b` |

  `CHAP_MODEL_PROVIDER` (`anthropic`, `openai`, `ollama` or `scripted`)
  picks one when more than one is set. No vendor SDK is installed; each
  provider is one HTTP call in `src/providers.ts`.

If the model cannot be reached, the playground still serves the UI and
exposes the JSON-RPC wire, you just won't get bot drafts. Fix the provider,
or unset it to use the scripted agent, and hit **Reset** in the UI.

---

## Install and run

```bash
cd reference/playground
npm install
npm start
```

That starts the coordinator on <http://localhost:7777>.

Now open **two browser tabs**:

- <http://localhost:7777/#maya> → enter as Maya
- <http://localhost:7777/#sam>  → enter as Sam

Or click "Enter as Maya / Sam" from the role picker at the root URL.

The bot will draft all six tickets in the background. As each draft
completes, it appears in Maya's queue. High-criticality items
auto-escalate to Sam.

---

## What to try

0. **First time? Take the guided walkthrough.** A "Take the guided
   tour →" button on the role picker (and the "↻ Guided tour" button
   in the status bar) runs a 90-second narrated walkthrough that
   drives the protocol through one full ticket cycle, then a
   high-criticality auto-escalation. The walkthrough fires real
   envelopes against `/rpc`: you can open DevTools and watch them go.
1. **Edit a draft in Maya's tab.** Watch the live diff update under
   the textarea. Add tags and a rationale. Hit **Override & send**.
2. **Watch Sam's tab**: high-criticality items appear there
   automatically. Click one and see the lineage badge (bot →
   Maya → Sam).
3. **Open the protocol view** (the "Open protocol view" button in the
   status bar, or click the strip at the bottom). Every envelope on
   the chain shows up here with its sequence number, method, and a
   one-line summary. Routing-decision envelopes are amber-bordered;
   override envelopes are ember-bordered. Click any entry to expand
   the full JSON.
4. **Override two or more drafts.** A dividend chart appears
   showing your tag distribution, that's the override-as-data
   signal the protocol is designed to capture.
5. **Reset.** Top-right button. Wipes state, re-drafts every
   ticket from scratch.

---

## What's real, what's simplified

The README and the in-UI footer make the boundary explicit, but for
the record:

| Real                                          | Simplified                                       |
|-----------------------------------------------|--------------------------------------------------|
| Protocol code (`@brightbeamai/chap-coordinator` library)   | No auth, production: `identity-oidc/1.0`        |
| Envelope wire format on `/rpc`                | Routing policy is in-process, production: `routing/1.0` profile |
| RFC 6902 JSON Patch on overrides              | State persisted to a local JSON file, production: database |
| Evidence chain, persisted across restarts     | No signing, production: `security-signed/1.0`   |
| A real model where one is configured          | A single workspace, three participants           |
| SSE-based live updates                        |                                                  |

---

## Files

```
reference/playground/
├── README.md                    ← you are here
├── package.json
├── tsconfig.json
├── data/state.json              ← created on first run; survives restarts
├── src/
│   ├── server.ts                ← HTTP + JSON-RPC + SSE
│   ├── ollama-agent.ts          ← bot participant; drafts through a provider
│   ├── providers.ts             ← Anthropic, OpenAI, Ollama or scripted
│   ├── state-store.ts           ← persistent JSON backend
│   ├── tickets.ts               ← six hand-crafted tickets
│   └── public/
│       ├── index.html
│       ├── playground.js
│       └── playground.css
└── tests/
    └── smoke.test.ts            ← end-to-end tests with a scripted drafter
```

---

## Running the tests

The smoke tests need no model; they script the drafter and exercise the
coordinator + routing policy end-to-end:

```bash
npm test
```

You should see six passing tests covering low/critical/high
criticality routing, override capture, audit chain ordering, and
ticket-catalogue integrity.

---

## Configuration

Environment variables:

- `PORT`: HTTP port (default `7777`)
- `CHAP_HOST`: interface to bind (default `127.0.0.1`). The playground has no
  authentication; it warns when bound to a non-loopback host, and the Docker
  image sets `0.0.0.0` because the container's published port is mapped to
  `127.0.0.1` on the host by `docker-compose.yml`. Only expose it on a trusted
  network.
- The model variables listed under Requirements. `CHAP_NO_LLM=1` still
  selects the scripted agent, as it did before the providers were added.

---

## Talking to the wire directly

The `/rpc` endpoint is the real CHAP wire. You can poke it with
curl just like any other CHAP-aware client would:

```bash
# Describe the workspace
curl -s -X POST http://localhost:7777/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":"1","method":"workspace.describe",
       "params":{"workspace_id":"wsp_techcorp_support"}}' | jq .

# Read the audit log
curl -s -X POST http://localhost:7777/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":"2","method":"audit.read",
       "params":{"workspace_id":"wsp_techcorp_support","from_seq":0,"limit":50}}' | jq .
```

The HTML UI is one client. You can write another one.
