# Governance

This document describes how the Collaborative Human-Agent Protocol evolves: the
roles, the change process, the versioning policy, the intellectual-
property terms, and the dispute-resolution path. A protocol without
documented governance is unfinished; this document is part of CHAP.

---

## 1. Charter

**Purpose.** CHAP exists to standardise the verbs that humans, agents,
and services use to collaborate on shared work, and to do so in a way
that composes with [MCP](https://modelcontextprotocol.io) and
[A2A](https://a2a.dev).

**Scope.** The wire format, methods, profile catalogue, schemas, and
conformance criteria. Out of scope: agent frameworks, workflow
engines, UI conventions, business policy.

**Principles**, in priority order:

1. **Minimal core.** Adding to Core requires an extraordinary case.
2. **Reuse over reinvention.** New profiles must explicitly justify
   not using an existing standard. See
   [`RELATIONSHIP-TO-OTHER-STANDARDS.md`](./RELATIONSHIP-TO-OTHER-STANDARDS.md).
3. **Multi-vendor by construction.** No change that would only work
   in one vendor's stack.
4. **Backward-compatible by default.** Wire-breaking changes
   require a major-version bump and a documented migration period.
5. **Open by default.** Specs, schemas, reference code, conformance
   suites, meeting notes, and decision records are all public.

---

## 2. Roles

### 2.1 Editors

The Editors maintain the specification text, schemas, and reference
implementation. They merge approved changes, run releases, and
moderate the discussion forum. There are typically two to three
Editors at any time.

Editors are appointed by the Steering Committee for renewable
twelve-month terms. The current Editors are listed in
`MAINTAINERS.md`.

Editors implement consensus; they do not, on their own, make
protocol decisions.

### 2.2 Steering Committee

The Steering Committee is composed of five to nine people drawn from
organisations that ship production CHAP deployments or
implementations. The Committee ratifies major decisions, appoints
Editors, manages the IPR and license, and acts as the final arbiter
on disputes.

Seats are nominated by the existing Committee and confirmed by
majority vote. At least three seats MUST be filled by people not
employed by the largest contributor organisation, to ensure no
single party can unilaterally direct the protocol.

### 2.3 Working Groups

Specific topic areas (security, audit, identity, profiles) may form
Working Groups with their own meeting cadence and mailing list.
Working Groups propose changes and recommend; they do not ratify.

### 2.4 Contributors

Anyone who files an issue, opens a pull request, or participates in
discussion. The barrier to entry is low; the bar for changes
accepted into the specification is high.

---

## 3. Change process

All substantive changes follow this lifecycle. Each step has a
clear artefact and a clear decision-maker.

```
  Idea           Discussion       Proposal         Implementation     Adoption
   │                 │               │                   │               │
   ▼                 ▼               ▼                   ▼               ▼
  Issue ──────► Discussion ────► HEP draft ────► Reference impl ────► Spec merge
              (any forum)     (CHAP Enhancement
                                 Proposal)
```

### 3.1 Issues

Any contributor may file an issue describing a problem, ambiguity,
or feature request. Issues are triaged weekly by an Editor and
labelled:

- `clarification` — needs spec text refinement; no semantic change.
- `bug` — spec says something that doesn't work in practice.
- `enhancement-proposal` — substantive addition; needs a HEP.

### 3.2 HEP — CHAP Enhancement Proposal

A HEP is a structured document proposing a substantive change. It
contains:

| Section            | Content                                              |
|--------------------|------------------------------------------------------|
| Title              | `HEP-N: short title`                                 |
| Status             | Draft / Active / Accepted / Rejected / Withdrawn     |
| Author(s)          | Names and affiliations                               |
| Abstract           | One paragraph                                        |
| Motivation         | Why this matters; what problem it solves             |
| Specification      | Concrete wire-level details                          |
| Compatibility      | What breaks; migration story                         |
| Reference impl     | Link to a working implementation                     |
| Rationale          | Why this design and not alternatives                 |
| Existing standards | What existing standard, if any, this maps to         |
| Security           | Threats introduced, mitigated, or unchanged          |
| Open questions     | Known unresolved items                               |

HEPs are submitted as a pull request to `heps/HEP-NNN.md`. The
Editor assigns the number and triages.

### 3.3 Discussion period

A HEP enters a **public discussion period of fourteen days minimum**.
During this period anyone may comment; the relevant Working Group
(if any) meets at least once; the Steering Committee tracks but
does not decide. After discussion, the author revises and marks the
HEP Active.

### 3.4 Reference-implementation requirement

A HEP cannot be Accepted without a working reference implementation
that passes the conformance tests for the relevant profile. This
requirement is non-negotiable. Speculative specification text is
not accepted.

The implementation may live in [`reference/`](./reference/) for
Core changes, or in any contributor's repository (linked from the
HEP) for profile changes.

### 3.5 Acceptance

Acceptance thresholds:

- **Editorial / clarification changes.** One Editor approves.
- **Core changes.** Super-majority (≥ ⅔) of Steering Committee
  with no veto, after a thirty-day public-comment period.
- **New profile.** Simple majority of Steering Committee with no
  veto, after a fourteen-day public-comment period.
- **Profile revision.** Simple majority of the profile's owning
  Working Group (or Steering Committee if no WG), after a
  seven-day public-comment period.

Steering Committee decisions are public, recorded with vote
tallies, and signed by the chair.

### 3.6 Rejection and withdrawal

A rejected HEP remains in the repository with status Rejected and
a brief rationale. This prevents the same proposal from being
re-litigated; a fresh HEP on the same topic requires new arguments.

A HEP author may withdraw at any time.

---

## 4. Versioning

CHAP follows [Semantic Versioning 2.0](https://semver.org).

- **MAJOR** (`X.0`): wire-breaking changes; old clients cannot
  talk to new servers. A documented migration period of at least
  one calendar year between major versions is required.
- **MINOR** (`X.Y`): additive only. New methods, new optional
  fields, new error codes. Old clients continue to work.
- **PATCH** (`X.Y.Z`): editorial fixes; no semantic change.

Profiles version independently from Core. A workspace declares
both the Core version and each profile version it supports via
`workspace.describe`'s `profiles` field.

---

## 5. Intellectual property and licensing

### 5.1 Specification license

The CHAP specification text is licensed under
[Creative Commons Attribution 4.0 (CC-BY-4.0)](https://creativecommons.org/licenses/by/4.0/).
Anyone can implement, redistribute, or extend CHAP without
permission.

### 5.2 Code license

Reference implementations and conformance code are licensed under
[Apache License 2.0](./LICENSE), which includes an explicit patent
grant.

### 5.3 Contribution terms

By submitting a pull request, a contributor agrees that the
contribution is licensed under the same terms as the file being
modified (CC-BY for spec text, Apache 2.0 for code). For
substantive changes the Editors may request a Developer Certificate
of Origin (DCO) sign-off.

### 5.4 Patents

The Steering Committee commits to CHAP being implementable royalty-
free. Contributors implicitly grant a patent license for any
patents that read on their contributions, as per the Apache 2.0
patent grant for code and a similar good-faith commitment for spec
text. A formal IPR policy mirrors the W3C Royalty-Free Patent
Policy in spirit.

---

## 6. Disputes

When two contributors disagree on a substantive technical point:

1. **Discuss in the open.** Disagreements are not personal; they
   are technical inputs.
2. **If still unresolved after fourteen days,** the relevant
   Working Group (or the Editors, if no WG) issues a non-binding
   recommendation.
3. **If still unresolved after another fourteen days,** the
   Steering Committee makes a binding decision by majority vote.
4. **A contributor who feels a decision was made in bad faith**
   may request an open review by the full Steering Committee at
   the next scheduled meeting. The review is recorded publicly.

Disputes about *conduct* (rather than technical content) go to the
Code of Conduct process in [`CONTRIBUTING.md`](./CONTRIBUTING.md) §8.

---

## 7. Forks

CHAP is a public standard licensed CC-BY and Apache 2.0. Anyone may
fork. Three constraints apply if you do:

1. Use a name clearly distinct from CHAP.
2. Don't claim conformance with CHAP unless you pass the conformance
   suite.
3. Submit changes back upstream if they should be part of CHAP.

---

## 8. Amending this document

Changes to this Governance document go through the HEP process like
any other substantive change, with two additional requirements:

- Governance changes require a twenty-one-day public-comment
  period (versus the usual fourteen or thirty).
- Acceptance requires explicit super-majority approval from the
  Steering Committee.
