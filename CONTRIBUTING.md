# Contributing to CHAP

Thank you for considering a contribution to the Collaborative Human-Agent Protocol. CHAP is
intended to be an **open, vendor-neutral standard**. Contributions of any size
are welcome, typo fixes, clarifying examples, new test vectors, new transport
bindings, and substantive proposals for the next draft.

This document describes how to propose changes and what kinds of changes are
in scope.

> **Editorial note.** This guide mentions Editors and working groups. The
> Editors are the maintainers listed in [MAINTAINERS.md](./MAINTAINERS.md),
> and no working group is active yet. MAINTAINERS.md describes how
> decisions are made today.

---

## Local development

The repository is an npm workspace. After cloning:

```bash
# Install everything (TS workspace + Python packages).
npm install
pip install -e packages/coordinator-py
pip install -e packages/chap-langgraph

# Build the TS packages in dependency order (required before typecheck
# in any downstream package, because each package's exports map points
# at dist/).
npm run build

# Then any of these from the repo root:
npm test                                     # all TS test suites
npm run typecheck                            # all TS packages
npm run check:schemas                        # method-catalogue drift
python3 -m pytest packages/coordinator-py/   # Python reference
python3 -m pytest packages/chap-langgraph/   # langgraph bridge

python3 -m unittest discover -s start-here/tests   # the starter
node --test start-here/tests/render.test.mjs       # the reviewer surface
```

The starter uses `unittest` rather than pytest, which is the odd one out on
purpose. [`START_HERE.md`](./START_HERE.md) promises a new developer that
Python 3.10 and a clone are the only prerequisites, and a suite that needs a
`pip install` to run would quietly make that untrue.

Why the build step matters: each TypeScript package publishes from
`dist/` (not `src/`), so the local file: deps between workspace
packages need the upstream `dist/` to exist. `npm run build` from
the repo root builds them in the right order. The same `prepublishOnly`
script runs the full chain (schemas check, typecheck, tests, build)
before publishing.

---

## 1. Ways to contribute

| Kind                              | Process                                                |
|-----------------------------------|--------------------------------------------------------|
| Typo or editorial fix             | Pull request directly                                  |
| New worked example                | Pull request directly                                  |
| New transport binding             | CEP (see below)                                        |
| New method or namespace           | CEP (see below)                                        |
| Breaking change to envelope or identity | CEP + working-group review                         |
| Security-sensitive change         | See [SECURITY.md](./SECURITY.md), do not open public issue |
| New conformance test vector       | Pull request directly                                  |
| New integration document          | Pull request directly                                  |

---

## 2. Proposal format: CHAP Enhancement Proposals (CEPs)

For substantive changes, write a proposal that includes:

1. **Title and one-sentence summary.**
2. **Motivation.** What problem does this solve? Who is asking?
3. **Detailed design.** Wire-level specifics, schema deltas, error codes.
4. **Backwards compatibility.** What breaks? Migration path?
5. **Security considerations.** Threat impact, key-handling impact.
6. **Alternatives considered.** Why this design over the alternatives?
7. **Open questions.**

Submit a CEP as a pull request adding `ceps/CEP-NNN.md`; the Editor assigns
the number. [`ceps/CEP-001.md`](./ceps/CEP-001.md) is a worked example, and
[`GOVERNANCE.md`](./GOVERNANCE.md) §3.2 defines the required sections; §3.3
and §3.5 set the comment periods.

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

- Vendor- or product-specific extensions. CHAP is intentionally
  vendor-neutral. Vendors are welcome to layer their own protocols
  on top of CHAP, but the core specification will not encode
  vendor-specific semantics.
- UI conventions. CHAP defines a wire format and a method catalogue,
  not a user interface.
- Business processes. CHAP is mechanism, not policy.

---

## 4. Style

Specification prose:

- **RFC 2119 terms** (MUST, SHOULD, MAY, MUST NOT, SHOULD NOT) in
  normative sections only. Avoid them in tutorials and explanatory
  material.
- **Active voice.** "The Coordinator verifies the signature", not
  "the signature is verified by the Coordinator."
- **Concrete over abstract.** Show a wire example whenever you
  introduce a new field or method.
- **Vendor-neutral examples.** Use `example.org`, `example.com`,
  generic role names (`reviewer`, `triage-agent`), and recognisable
  but non-proprietary scenarios.

Reference code:

- **TypeScript and Python** for the two reference coordinators. They are
  peers, and a change in behaviour goes into both.
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

## 5. Versioning and releases

Version numbers follow
[ROADMAP.md, section "Version numbers"](./ROADMAP.md#version-numbers).
Before 1.0, a minor release may break things, and a patch release only
brings an implementation into line with the specification or corrects
documentation.

If your change breaks an existing client or a stored log, say so in
[CHANGELOG.md](./CHANGELOG.md) under "Unreleased", with a migration. It
ships in the next minor release, which lists it under "Breaking".

Releases are tagged `vX.Y.Z`. The protocol packages share one version,
which CI checks with `scripts/check-versions.mjs`.

[ROADMAP.md](./ROADMAP.md#what-10-promises) sets out what 1.0 requires.
Until a CEP settles the number in milestone 0.5, 1.0 needs at least three
independent, interoperable implementations that pass the conformance tests.

---

## 6. Code of conduct

This project follows the code of conduct in
[CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md), adapted from the Contributor
Covenant 2.1. It also says how to report a problem. Be kind, assume good
faith, and disagree on the technical merits. Personal attacks,
harassment, or vendor-pumping are not welcome.

---

## 7. Licensing of contributions

CHAP has two licences. The specification text, listed in
[LICENSE-SPEC.md](./LICENSE-SPEC.md), is licensed under Creative Commons
Attribution 4.0 (CC BY 4.0). Everything else, including all code, is
licensed under the Apache License, Version 2.0 (see [LICENSE](./LICENSE)).

By contributing, you agree that your contribution is licensed under the
licence of the file it changes. The same list decides the licence of a new
file: a new file in `profiles/` or `ceps/`, or a new
`conformance/*-vectors.md` file, is specification text, and any other new
file is under Apache 2.0.

If your contribution includes code or text under another licence, say so
in the pull request, name the licence and the source, and check that it is
compatible with the licence of the file it joins.

For a substantive change, the maintainers may ask for a Developer
Certificate of Origin sign-off ([GOVERNANCE.md](./GOVERNANCE.md) §5.3).
