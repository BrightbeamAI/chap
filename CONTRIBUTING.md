# Contributing to HAP

Thank you for considering a contribution to the Human-Agent Protocol. HAP is
intended to be an **open, vendor-neutral standard**. Contributions of any size
are welcome — typo fixes, clarifying examples, new test vectors, new transport
bindings, and substantive proposals for the next draft.

This document describes how to propose changes and what kinds of changes are
in scope.

---

## 1. Ways to contribute

| Kind                              | Process                                                |
|-----------------------------------|--------------------------------------------------------|
| Typo or editorial fix             | Pull request directly                                  |
| New worked example                | Pull request directly                                  |
| New transport binding             | RFC-style proposal (see below)                         |
| New method or namespace           | RFC-style proposal (see below)                         |
| Breaking change to envelope or identity | RFC-style proposal + working-group review          |
| Security-sensitive change         | See [SECURITY.md](./SECURITY.md) — do not open public issue |
| New conformance test vector       | Pull request directly                                  |
| New integration document          | Pull request directly                                  |

---

## 2. Proposal format ("HAP Improvement Proposals")

For substantive changes, write a proposal that includes:

1. **Title and one-sentence summary.**
2. **Motivation.** What problem does this solve? Who is asking?
3. **Detailed design.** Wire-level specifics, schema deltas, error codes.
4. **Backwards compatibility.** What breaks? Migration path?
5. **Security considerations.** Threat impact, key-handling impact.
6. **Alternatives considered.** Why this design over the alternatives?
7. **Open questions.**

Proposals are reviewed in a public forum and require **rough consensus**
of the working group before they merge into a draft.

---

## 3. What's in scope

**In scope:**

- The wire format and its evolution.
- The identity and signing model.
- The method catalogue and error codes.
- Transport bindings.
- Composition with MCP, A2A, OIDC, and other open standards.
- Reference implementations in additional languages.
- Conformance tests, test vectors, and interop tooling.
- Deployment patterns and worked examples.

**Out of scope:**

- Vendor- or product-specific extensions. HAP is intentionally
  vendor-neutral. Vendors are welcome to layer their own protocols
  on top of HAP, but the core specification will not encode
  vendor-specific semantics.
- UI conventions. HAP defines a wire format and a method catalogue,
  not a user interface.
- Business processes. HAP is mechanism, not policy.

---

## 4. Style

Specification prose:

- **RFC 2119 terms** (MUST, SHOULD, MAY, MUST NOT, SHOULD NOT) in
  normative sections only. Avoid them in tutorials and explanatory
  material.
- **Active voice.** "The Coordinator verifies the signature" — not
  "the signature is verified by the Coordinator."
- **Concrete over abstract.** Show a wire example whenever you
  introduce a new field or method.
- **Vendor-neutral examples.** Use `example.org`, `example.com`,
  generic role names (`reviewer`, `triage-agent`), and recognisable
  but non-proprietary scenarios.

Reference code:

- **TypeScript** for the canonical reference implementation.
- **Pure functions where possible.** Side-effects belong in transport
  and storage adapters.
- **No dependencies beyond a JCS, an Ed25519, and a JSON Schema
  validator.** Adding a dependency requires justification.

Diagrams:

- **Mermaid** sources committed under `diagrams/` and embedded in the
  prose. Each diagram includes a high-contrast theme block at the top
  so that fonts, line weights, and colours are legible at presentation
  scale.

---

## 5. Versioning and release process

HAP follows SemVer on the `hap` envelope field. Until 1.0, MINOR bumps
may include breaking changes; implementers should pin exact draft
versions. See [CHANGELOG.md](./CHANGELOG.md) for the release history
and the planned items for upcoming drafts.

Drafts are tagged `0.1`, `0.2`, etc. The first stable release will be
`1.0` after at least three independent interoperable implementations
have demonstrated conformance.

---

## 6. Code of conduct

This project follows a standard open-source code of conduct. Be kind,
assume good faith, and disagree on the technical merits. Personal
attacks, harassment, or vendor-pumping are not welcome.

---

## 7. Licensing of contributions

By contributing, you agree that your contribution will be licensed under
the Apache License, Version 2.0 (see [LICENSE](./LICENSE)). If your
contribution includes code or text covered by another licence, please
note this in the pull request and ensure compatibility.
