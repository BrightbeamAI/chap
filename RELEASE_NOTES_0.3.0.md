# CHAP 0.3.0: documents that match the code, refused calls on the record, and review that holds

CHAP 0.3.0 is the first release under the versioning rules in
[ROADMAP.md](./ROADMAP.md#version-numbers): a minor release may break things,
and the [changelog](./CHANGELOG.md) lists every break under "Breaking" with a
migration. The protocol packages are 0.3.0 and implement specification 0.3.

## If you are upgrading

Five changes are most likely to reach you.

**Advertise the profiles you call.** A workspace serves the methods of the
profiles it advertises, together with the reads and the key methods. A call to
a method of a profile the workspace does not advertise is refused with
`-32601`. Add the profile at `workspace.create`, or with
`workspace.set_profiles`.

**Expect refused calls on the log.** A member's refused attempt at a governed
action, such as a decision on a review addressed to someone else, is recorded.
The entry holds the call under `request` with an `outcome` beside it, so a
reader that takes `envelope` from every entry has to allow for entries without
one. `audit.read` takes `filter.outcome: "accepted"` to read accepted calls
alone. Back up a Python store before going back to an earlier release, which
cannot read a log that holds a refusal.

**Escalating or superseding keeps the review.** The successor of a task that
requires review requires it too. Completing it opens a review addressed to the
human members other than its assignee and completer, so a workspace needs such
a member, or an explicit `review.request` naming the reviewers.

**Membership and pauses hold on more methods.** `task.route`, `review.depth`,
`escalate.auto` and `participant.leave` need a member. A paused participant is
given no task through `task.route`, `escalate.raise`, `control.supersede` or
`handoff.accept`.

**`task.update` opens no review and reaches no pause.** Use `review.request`
or `task.complete` to open a review, and `control.pause` to pause.

The changelog covers the rest: the order in which a call is checked, the
`control.snapshot` result shape, `control.resume` restoring the state held at
the pause, explicitly empty selection lists, fields of the wrong type, how
`whisper.answer` and `audit.submit_to_scitt` appear on the log, and the
version moving to 0.3 in the schema identifiers and in the content type of
SCITT statements.

## Security fixes

Each of these affects 0.2.13, and 0.2.13 receives no further fixes. Upgrade to
0.3.0.

- **Taking over a member by joining again.** Where a deployment configures an
  identity verifier, anyone holding a token or presentation the verifier
  accepted could join under an existing member's name, add a key and sign as
  that member. A token or presentation now binds only to its own participant.
- **Completing reviewed work without review.** `escalate.raise` and
  `control.supersede` made a successor that needed no review, so a task
  awaiting review could be replaced and completed unreviewed. The successor
  keeps the requirement.
- **Acting without membership.** A non-member could reassign a task with
  `task.route`, record routing decisions, and write a leave to the log under
  any name. These methods now need a member.
- **Work reaching a paused participant.** `task.route`, `escalate.raise`,
  `control.supersede` and `handoff.accept` assigned tasks to a paused
  participant. They no longer do.
- **A crash on a malformed request.** The Python coordinator raised out of
  `dispatch` on a list or object `from`, or a non-string `ts` on a signed call.
  Both coordinators answer these with an error.

[SECURITY.md](./SECURITY.md) is the threat model for this release, with the
supported versions and how to report a vulnerability.

## The documents

The specification and the project documents describe what both coordinators
do in this release:

- **The specification, core/SPEC.md, the profiles and the conformance
  checklist.** Keys, signatures and step-up are described as both
  coordinators apply them, and the catalogue's `privileged` flags match. The
  membership rule and its exemptions are listed. `review/1.0` names the rules
  it supports, what abstaining does and how an override patch applies.
  Routing decisions are held in workspace state and returned to the caller.
  The threat text in §15.4 describes replay, downgrade, forks and a
  compromised coordinator as they stand. `test-vectors.md` carries the RFC 8032
  signature and a chain both coordinators record, and the conformance tests
  check those values. SPECIFICATION §4.1 and §5.2 point to core/SPEC.md §2 for
  the messages the coordinators send, and `profiles/audit-scitt.md` cites
  RFC 9943.
- **SECURITY.md** is the one threat model the specification points to:
  assumptions and what the deployment supplies, messages and signatures, keys,
  the log and the chain, authority, reporting, and the known limitations.
- **Project documents.** `LICENSE-SPEC.md` licenses the specification text
  under CC BY 4.0 and keeps everything else under Apache 2.0. The versioning
  rules are in the changelog, GOVERNANCE.md and CONTRIBUTING.md.
  IMPLEMENTATIONS.md labels the packages Beta and names the run behind each
  claim. GOVERNANCE.md and CONTRIBUTING.md point to MAINTAINERS.md for how
  decisions are made today.
- **The roadmap** names no foundation as the long-term home, and says what a
  chain head shows.
- **The guides.** README, ABOUT, FAQ, GLOSSARY, ARCHITECTURE, HANDBOOK,
  IN_PRACTICE and RELATIONSHIP-TO-OTHER-STANDARDS describe the coordinators as
  they behave and mark what is specified and not yet built, and the samples
  they show run as written.

Where a coordinator falls short of a rule, the rule stays and the text says so.
The gaps that later milestones close are listed in ROADMAP.md, "What still
needs work".

## Known issues

These are open, and none is new in this release:

- An explicit `review.request` can name its own sender, so a member can
  address a required review to itself and approve it.
- A participant pause binds a participant that cooperates. Any member can
  resume it, and leaving and joining again clears it.
- A review reopened after `decide.reject` with `request_revision` keeps its
  earlier decisions, so under `quorum:<n>` an approval of the earlier artefact
  counts towards the revision.
- A `workspace.create` that omits `workspace` records no entry.
- Identity tokens and presentations are stored on the log as presented.
- Under required signatures, the Python coordinator chooses the signing key
  by a top-level `ts` when `params.ts` is absent, and the TypeScript
  coordinator uses its own clock. Send `ts` in `params`, as core/SPEC.md §2
  describes.
- A few malformed inputs still get different answers from the two
  coordinators. The numbered examples in `examples/` and the envelopes in
  `integrations/CHAP-with-A2A.md` use the older message format.

## Packages

| Registry | Package | Version |
|---|---|---|
| npm | `@brightbeamai/chap-coordinator` | 0.3.0 |
| npm | `@brightbeamai/chap-coordinator-mcp` | 0.3.0 |
| npm | `@brightbeamai/chap-coordinator-a2a` | 0.3.0 |
| PyPI | `chap-coordinator` | 0.3.0 |
| PyPI | `chap-langgraph`, `chap-pydantic-ai`, `chap-ag2`, `chap-llama-index`, `chap-google-adk` | 0.3.0 |
| PyPI | `chap-analytics`, on its own version track | 0.2.1 |
| MCP Registry | `io.github.BrightbeamAI/chap` | 0.3.0 |
