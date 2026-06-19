# CHAP reference (Python)

The Python reference implementation of CHAP Core plus every profile.
A minimal HTTP server, a demo client, and an override-analytics
tool, built on top of the `chap-coordinator` Python package.

## Install

From this directory (with the repo checked out):

```bash
pip install -e ../../packages/coordinator-py
```

For signed envelopes (`security-signed/1.0`):

```bash
pip install -e "../../packages/coordinator-py[crypto]"
```

## Run

```bash
# Terminal 1: start the server
python server.py
# CHAP reference (Python) on http://127.0.0.1:8080/chap
# Profiles: core/1.0, review/1.0, whisper/1.0, deliberation/1.0,
#           handoff/1.0, control/1.0, routing/1.0, modes/1.0

# Terminal 2: run the demo client
python client.py
```

The client performs the same workflow as
`reference/core-plus-review/client.ts`: two participants, one task,
one override with diff + rationale + tags, audit replay.

## Analytics

After running the client (or a real workload), aggregate the
override patterns:

```bash
python analyze_overrides.py wsp_support_triage
```

Outputs a tag histogram, intent-preserved breakdown, and top
reviewers. The most common tags are the next prompt revision
targets, not guessed but cited from the chain.

## Conformance

The Python server speaks the same JSON-RPC 2.0 wire format as the
TypeScript reference. Run the conformance harness against it:

```bash
cd ../../conformance/harness
npx tsx harness.ts --url=http://127.0.0.1:8080/chap
```

## Server options

```bash
python server.py --port 9000             # custom port
python server.py --host 0.0.0.0          # listen on all interfaces
python server.py --core-only             # advertise only core + review
python server.py --require-signatures    # enforce security-signed/1.0
```
