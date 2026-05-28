# CHAP Playground

A runnable demo: two humans (Maya and Sam) and one local LLM agent
(Gemma3 via Ollama) collaborating on a customer-support queue, with
every message a real CHAP envelope, every override a real RFC 6902
JSON Patch, and every routing decision a real `route_decision`
artefact in the evidence chain.

The protocol code is the unmodified `@chap/coordinator` library
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
- **Ollama** installed locally. Get it from <https://ollama.com>.
- The **gemma3:4b** model pulled. Run once:
  ```bash
  ollama pull gemma3:4b
  ```
  This is ~3 GB. If you have more RAM and want better drafts, try
  `gemma3:12b` and set `OLLAMA_MODEL=gemma3:12b` when starting.

If Ollama is not running, the playground still serves the UI and
exposes the JSON-RPC wire, you just won't get bot drafts. Start
Ollama and hit **Reset** in the UI to re-draft.

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
| Protocol code (`@chap/coordinator` library)   | No auth, production: `identity-oidc/1.0`        |
| Envelope wire format on `/rpc`                | Routing policy is in-process, production: `routing/1.0` profile |
| RFC 6902 JSON Patch on overrides              | State persisted to a local JSON file, production: database |
| Evidence chain, persisted across restarts     | No signing, production: `security-signed/1.0`   |
| Real LLM (Gemma3 via Ollama)                  | A single workspace, three participants           |
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
│   ├── ollama-agent.ts          ← bot participant; calls Ollama
│   ├── state-store.ts           ← persistent JSON backend
│   ├── tickets.ts               ← six hand-crafted tickets
│   └── public/
│       ├── index.html
│       ├── playground.js
│       └── playground.css
└── tests/
    └── smoke.test.ts            ← end-to-end tests with mocked Ollama
```

---

## Running the tests

The smoke tests don't need Ollama, they mock the drafter and
exercise the coordinator + routing policy end-to-end:

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
- `OLLAMA_URL`: Ollama base URL (default `http://localhost:11434`)
- `OLLAMA_MODEL`: model name (default `gemma3:4b`)

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
