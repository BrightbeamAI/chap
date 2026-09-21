# Profile dispatch-gate conformance vectors

`profile-gate-vectors.json` is the shared golden fixture for SPECIFICATION
§15.4: a Coordinator refuses a method whose owning profile the workspace does
not advertise. Both coordinator test suites replay the same envelopes with
deterministic ids and a deterministic clock, and compare the entire response.

Eight cases, in two halves.

**Four refusals**, one for each shape of unadvertised profile: `control/1.0`,
`whisper/1.0`, `routing/1.0` and `audit-scitt/1.0`. Each answers `-32601` with
the message a Coordinator that never implemented the method would give, and
carries `data: {"profile": …, "advertised": [...]}`.

**Four that pass the gate**, which is the half that makes the fixture worth
having. Two carry a refusal from the handler rather than the gate, and the
distinction between the two is the property under test:

- `control.pause` with `control/1.0` advertised reaches the handler and is
  refused `-32602 Unknown task`.
- `control.pause` with `control/1.1` advertised does the same, because the
  gate matches the profile name rather than an exact version, so a minor
  release is not a break.
- `workspace.describe` answers with the descriptor. A read is never gated.
- `participant.revoke_key` reaches the handler and is refused `-32071` for an
  absent key. The key lifecycle belongs to Core, so an operator's response to
  a compromised key does not depend on a profile entry.

Run them:

```bash
npx tsx --test packages/coordinator/tests/profile_gate_vectors.test.ts
python -m pytest -q packages/coordinator-py/tests/test_profile_gate_vectors.py
```
