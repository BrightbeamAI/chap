# CHAP Conformance Harness

Runs the canonical CHAP test vectors against any CHAP endpoint and
reports pass/fail. Produces in-toto-compatible attestations on
success.

## What it tests

**Core (mandatory)**: 12 tests covering:

- Wire format: malformed JSON, non-JSON-RPC bodies, unknown methods.
- The seven Core methods: `workspace.describe`, `participant.join`,
  `participant.leave`, `task.create`, `task.update`,
  `task.complete`, `audit.read`.
- State-machine enforcement: illegal transitions rejected.
- Filter correctness: `audit.read` filters work.
- Member enforcement: assigning to non-members fails.

**Review profile (optional)**: 6 tests covering:

- `review.request` transitions task to `review_requested`.
- `decide.override` applies the RFC 6902 JSON Patch and produces an
  override artefact.
- Audit log preserves the structured override (diff, rationale, tags,
  policy_refs).
- `abstain.declare` produces an `abstained` terminal state with a
  category.
- `decide.reject` with `request_revision: true` returns to
  `in_progress`.

## Usage

```bash
npm install

# Test localhost (default   assumes a server on :8080)
npm test

# Test a remote endpoint
tsx harness.ts --url=https://my-chap.example.org/chap

# Core only (no profile tests)
tsx harness.ts --core-only

# Produce an in-toto conformance attestation
tsx harness.ts --attest > attestation.json
```

## Exit codes

| Code | Meaning                                       |
|------|-----------------------------------------------|
| `0`  | All selected tests passed.                    |
| `1`  | One or more tests failed.                     |
| `2`  | Harness error (network, invalid arguments).   |

## Attestation format

`--attest` produces a JSON document conforming to the
[in-toto Statement format](https://github.com/in-toto/attestation/blob/main/spec/v1.0/statement.md):

```json
{
  "_type": "https://in-toto.io/Statement/v1",
  "subject": [{ "name": "...", "digest": { "sha256": "..." } }],
  "predicateType": "https://chap.dev/conformance/v1",
  "predicate": {
    "timestamp": "2026-05-17T12:34:56Z",
    "passed": true,
    "profiles_attested": ["core/1.0", "review/1.0"],
    "tests": [...]
  }
}
```

The attestation can be signed (e.g. with [`cosign`](https://github.com/sigstore/cosign))
and published as proof of conformance.

## What this does not cover

The harness tests **wire-level conformance**: that an implementation
correctly handles the protocol's required behaviours. It does not test:

- Real-world workload performance.
- Security properties beyond protocol correctness.
- Profile-specific cryptographic conformance (which requires
  long-lived test keys; see [`../test-vectors.md`](../test-vectors.md)
  for the cryptographic vectors).

Production implementations should run this harness plus their own
load tests, security reviews, and operational validation.
