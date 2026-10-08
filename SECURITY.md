# Security policy and threat model

This document is CHAP's threat model and its policy for reporting
vulnerabilities. It says what the protocol and its coordinators protect, what
they expect the deployment to provide, what they leave open, and how to report
a security issue.

---

## 1. About this document

This is the one threat model for CHAP. It is released with the specification,
under the same version tags. [SPECIFICATION.md](./SPECIFICATION.md) §15 states
the security requirements and points here for the threat model.

It covers the two coordinators in this repository: the TypeScript package
`@brightbeamai/chap-coordinator` in [`packages/coordinator`](./packages/coordinator/)
and the Python package `chap-coordinator` in
[`packages/coordinator-py`](./packages/coordinator-py/). Every statement holds
for both unless it says otherwise. A differential fuzzer in CI sends both the
same generated calls and fails the build if their answers or their chain heads
differ. It does not yet send signed calls, modes or calls to the SCITT profile.

The latest release is 0.2.13. The main branch holds work that ships in 0.3.0,
and this document marks each behaviour that exists only on main with
**(0.3.0)**. Where the specification describes something neither coordinator
builds, this document says so. [ROADMAP.md](./ROADMAP.md) names the milestone
that closes each gap.

Option names are given in their TypeScript form. The Python options have the
same names in snake case, such as `require_signatures`.

---

## 2. Assumptions and deployment duties

### Assumptions

- **The network can be observed and altered.** Transport security comes from
  the deployment.
- **Participants can be compromised one at a time.** With signatures
  required, a stolen key acts in one member's name, in the workspaces that
  hold it, until it is revoked.
- **The coordinator and its store are trusted for the order and completeness
  of the log.** Whoever runs the coordinator, or can write to its store, can
  drop, reorder, rewrite or withhold entries and recompute the chain to match.
  Signatures limit what they can forge: a signed call cannot be altered, and a
  new one cannot be made, without the signer's key. That protection holds for
  a verifier who knows the members' keys from a source outside the
  coordinator, because the coordinator keeps the list of keys itself.
- **Identity providers and verifier hooks are trusted.** Whatever a verifier
  hook accepts is recorded as given.

### What the deployment supplies

The coordinators are libraries. An application passes each request to
`dispatch` and returns the answer over its own transport. The coordinator
authenticates no caller and delivers nothing to other participants. The MCP
and A2A adapters leave authentication to the transport, and the servers under
`reference/` add none. The deployment supplies:

- TLS 1.3 or later on every production transport (SPECIFICATION.md §15.1).
- Caller authentication, and a check that each caller sends only its own
  `from`. This includes `participant.join`, whose sender the coordinators
  never verify.
- Delivery, meaning who sees which output. Delivering shadow-mode output only
  to the workspace's `shadow_observers` is the deployment's job
  (SPECIFICATION.md §15.1).
- Rate limits and timeouts for each participant.
- Storage, with a single coordinator writing each workspace
  (SPECIFICATION.md §10.3).
- Verifier hooks for identity tokens, credentials and SCITT receipts, and a
  SCITT submitter, where it uses those profiles.

Each protection in the coordinator is off until it is configured:

| Protection | Option | Default |
|---|---|---|
| Signature verification | `requireSignatures` | Off |
| Hash chain | `enableChain`, or `audit-scitt/1.0` in a workspace's profiles | Off |
| Step-up on privileged methods | `enforceStepUp` | Off |
| Membership for `workspace.describe` and `audit.read` | `requireReadMembership` | Off |
| Identity verification at join | `verifyOidcToken`, `verifyVc` | None |

With every option at its default, anyone who can reach the coordinator can
join any workspace in any role, act in any member's name and read any
workspace's log.

### Threats and what answers them

| Threat | What answers it | Conditions and gaps |
|---|---|---|
| A call made in another member's name | Signature verification against the member's registered keys | Needs `requireSignatures`, and control over who joins under which name (section 4) |
| A captured call sent again | A signed copy of a recorded refusal is answered with that refusal **(0.3.0)**. A repeated `task.create` idempotency key returns the first task. Each entry carries the `seq` and `arrived` the coordinator assigns. | Any other copy of an accepted call is evaluated again ([Envelope id replay](#envelope-id-replay)) |
| An entry altered after it was written | The hash chain, checked by `audit.verify_chain` | Needs the chain switched on, and a head kept where the writer cannot change it |
| Entries removed and the chain re-linked | Nothing inside the log. A SCITT receipt, or an earlier head held elsewhere, shows it. | Needs `audit-scitt/1.0` with a submitter, or another anchor |
| A stolen signing key | Revocation by the key's owner or an admin, effective at once | Needs a revocation in each workspace that holds the key |
| A privileged call from a stale session | Step-up: a recent OIDC `auth_time` for privileged methods | Needs `enforceStepUp` and a token verifier. Applies to humans and OIDC-bound members only. |
| A method the workspace does not offer | The profile gate **(0.3.0)** | Follows the advertised profiles, which an admin can change |
| Shadow output reaching production | Nothing in the coordinator | Needs the deployment's delivery layer |
| An artefact that claims authority | Authority comes from the method and the caller's membership, role and review assignment. Artefact content grants nothing. | Roles are chosen by the joiner (section 6) |
| Oversized or deeply nested requests | A size limit, 1 MiB by default, and a nesting limit of 64 levels **(0.3.0)** | Needs rate limits at the transport |

### Out of scope

CHAP does not defend against:

- **A participant who misbehaves within the rules**, such as a reviewer who
  approves without reading or an agent that invents a citation. The log
  records what was decided. It cannot show whether the decider was careful,
  deceived or manipulated, by social engineering or by prompt injection.
- **False or harmful artefact content.** Content is data to the protocol.
- **Compromise of a participant's own device or key store.**
- **Compromise of an identity provider or of a verifier hook.**
- **Inference from metadata**, such as `routing_hints`, timing and the size of
  requests.
- **Denial of service at the transport.** Rate limits and timeouts belong to
  the deployment.

---

## 3. Messages and signatures

Every message is a JSON-RPC 2.0 request, notification or response. The CHAP
fields `workspace`, `from`, `to` and `ts` travel inside `params`
([core/SPEC.md](./core/SPEC.md) §2). SPECIFICATION.md §4.1 and §5.2 describe
a different envelope, with an `evidence` block, which neither coordinator
uses. Milestone 0.4 settles one format.

Without required signatures, `from` is whatever the caller writes, and nothing
in a request is checked against the caller.

**Signatures.** Signing is off by default. With signatures required:

- Every request except `workspace.create` and `participant.join` must carry a
  top-level `sig`, reads included. A request whose signature or key does not
  check out is refused with `-32070`, `-32071` or `-32072`.
- `sig` has the form `ed25519:<kid>:<signature>`: an Ed25519 signature
  (RFC 8032) in base64, made with the sender's key named by `<kid>`.
- The signature covers the JCS canonical form (RFC 8785) of the whole request
  with its top-level `sig` removed, so `jsonrpc`, `id`, `method` and `params`
  are all signed.
- `workspace.create` and `participant.join` are never checked, because they
  run before the sender has a key on record. A `sig` on either is stored and
  not verified.
- A coordinator that requires signatures adds `security-signed/1.0` to each
  workspace it creates, and a coordinator that does not require them refuses
  a workspace that advertises it **(0.3.0)**.

**Canonical form.** A number anywhere in a request must be an integer no
larger in magnitude than 2^53 - 1 (SPECIFICATION.md §5.2). Other numbers
travel as strings, such as `"0.82"`. Both coordinators also refuse a string
that is not valid Unicode. A request that breaks these rules has no canonical
form, and the coordinator refuses it with `-32602`, signed or not.

**Size.** A request whose canonical form is larger than the limit, 1 MiB by
default and published as `max_envelope_bytes`, is refused with `-32600`. So is
a request nested deeper than 64 levels **(0.3.0)**.

**What is not checked.** Neither coordinator checks that a request `id` is new
or that `ts` is in order. [Envelope id replay](#envelope-id-replay) and
[Sender-declared timestamps](#sender-declared-timestamps) explain what that
leaves open and what a deployment can add.

A signature shows that the holder of a key registered for a member made the
call. Who that holder is depends on how the key was registered.

---

## 4. Keys

Keys belong to a member of one workspace, and each key needs a `kid`. Keys
are first registered at `participant.join`:

- A join that carries an `oidc_token` or a `vc_presentation`, sent to a
  coordinator with the matching verifier hook, is refused if the hook rejects
  it. Otherwise it registers any key the hook confirms: the token's
  `cnf.jwk`, or the key the credential verifier returns. Where the hook
  confirms a key, keys listed in the join's `jwks` are ignored.
- Otherwise the coordinator registers the keys listed in `jwks` as given. The
  join is not signed, so nothing shows that the caller holds those keys or
  owns the name. This is trust on first use.
- A token binds only to the participant it belongs to **(0.3.0)**. A token whose
  `chap_participant_uri` names another participant is refused with `-32404`.
  A later join under an existing member's name keeps the member's role, and
  it is accepted with a token only when the token's subject is the member's
  recorded subject, or, for a member with no recorded subject, when the
  token's `chap_participant_uri` names the member. A presentation is accepted
  on a later join only from the member's recorded holder, and is otherwise
  refused with `-32411`. An accepted later join adds the key the verifier
  confirms and refreshes the member's `auth_time` and `acr`. A later join
  with no token or presentation changes nothing about the member.

**Rotation.** `participant.rotate_key` names the old key (`old_kid`) and the
new one (`new_jwk`). With signatures required it must be signed with the old
key, or it is refused with `-32073`. The old key stops at the moment of
rotation and the new key starts then. There is no grace period.

**Revocation.** `participant.revoke_key` names a member (`target_uri`) and a
key (`kid`). The key's owner can revoke it, and an admin can revoke any
member's key. A revoked key is refused from the moment of revocation, with
`-32072`. Revocation applies in one workspace: the same key registered in
another workspace stays valid there.

**Clocks.** Revocation and expiry are judged by the coordinator's clock. The
sender's `ts` is used only to look the key up: the key named by `kid` must
have been registered at or before `ts`. A backdated `ts` cannot bring back a
rotated or revoked key, and a sender whose clock runs behind the
coordinator's can be refused just after its key is registered. The two
coordinators differ in two details: TypeScript accepts a signature in
URL-safe base64 where Python refuses it, and Python falls back to a top-level
`ts` when choosing a key. Milestone 0.4 settles one rule for both.

**The coordinator's own key.** The coordinator holds no key. It signs no
entry, no checkpoint and no SCITT statement.

**After acceptance.** Neither coordinator checks a signature again once it has
accepted a call, and `audit.verify_chain` does not check signatures.
`workspace.describe` publishes each member's current keys and key history.
Milestone 0.6 adds an export format that carries the key history, and an
offline verifier in each implementation.

---

## 5. The log and the chain

The coordinator records each call it accepts on the log of the workspace the
call names. The reads (`workspace.describe`, `audit.read`,
`audit.verify_chain` and `audit.verify_receipt`) are left off, and so is a
repeated `task.create` answered from its idempotency key.
`audit.submit_to_scitt` is left off as well **(0.3.0)**. A member's refused
attempt at a governed action is recorded too **(0.3.0)**, as
[Refused calls](#refused-calls) describes.

An accepted entry holds the call under `envelope`:

```json
{ "seq": 7, "arrived": "2026-09-30T10:00:00.000Z",
  "envelope": { "...": "the accepted call" },
  "prev_hash": "sha256:..." }
```

A refusal entry holds it under `request`, with an `outcome` beside it:

```json
{ "seq": 8, "arrived": "2026-09-30T10:00:01.000Z",
  "request": { "...": "the refused call" },
  "outcome": { "status": "refused", "code": -32011 },
  "prev_hash": "sha256:..." }
```

`seq` numbers entries from 0 in the order the coordinator records them, and
`arrived` comes from the coordinator's clock. `prev_hash` is present only when
the chain is on.

**The chain.** Where the chain is on, each entry's `prev_hash` is the head
before it, and the head after it is:

```
chain_head = SHA-256( JCS(record) || prev_hash )
```

`record` is the `envelope` of an accepted entry, or
`{"outcome": ..., "request": ...}` for a refusal. `prev_hash` is concatenated
as its full UTF-8 string, prefix included. Every digest is `sha256:` followed
by 64 lowercase hex characters, and the first chained entry's `prev_hash` is
`sha256:` followed by 64 zeros (SPECIFICATION.md §10.1). The workspace
descriptor publishes the head as `evidence_head` and the number of entries as
`audit_count`.

The hash covers `record` and nothing else. `seq` and `arrived` sit outside it,
and so does the workspace state the coordinator keeps beside the log, such as
members, roles and keys.

**When the chain is on.** The chain is optional. It is on for a workspace
created with `audit-scitt/1.0` in its profiles, for every workspace on a
coordinator with `enableChain` set, and from the point where an admin adds
`audit-scitt/1.0` with `workspace.set_profiles`. Entries written before that
point carry no `prev_hash`.

**What `audit.verify_chain` checks.** It replays the chained entries and
checks each link and the stored head. It also reports coverage: where
unchained entries come first, the answer is `not_evaluated` with `ok: false`,
never a pass. It reports an entry as malformed unless the entry holds
exactly one call, an accepted `envelope` or a refused `request` with its
outcome **(0.3.0)**. It does not check signatures, `ts` or `id`, and it
refuses a workspace that has no chain (SPECIFICATION.md §10.2).

**What a pass proves.** A pass shows that the entries agree with the stored
head. It does not show that the log is complete. Whoever can write to the
store can rewrite or remove entries and recompute every later link and the
head, and the result still passes. A head that has been published, or
anchored where that writer cannot change it, pins the log up to that point.
Two or more coordinator instances writing one store lose entries without any
error and re-link the chain, which then passes too, so each workspace needs a
single writer (SPECIFICATION.md §10.3).

**Anchoring.** With `audit-scitt/1.0`, `audit.submit_to_scitt` builds one
statement for each entry in the range it is given, the whole log by default,
with the entry's record as its payload. It passes each statement to the
submitter the deployment supplies. The coordinator does not sign the
statements: the signature is left to the deployment. Without a submitter, the
call returns the statements for the caller to submit.
`audit.verify_receipt` calls the deployment's receipt verifier, and fails
closed without one. A receipt from a transparency service, or a head published
to a store the coordinator's operator does not control, is what shows that an
entry existed independent of the coordinator.

### Envelope id replay

Neither coordinator checks that a request `id` is new, and
`audit.verify_chain` does not look for repeats. Anyone who reads the log can
find a repeated `id` among its entries. Refusing a repeated `id` at acceptance
is a stronger defence. It is a deployment decision: the protocol does not
require it, and no error code is allocated for it.

What a deployment needs to decide before building it:

- **How long to remember.** A set of seen ids grows without end unless it is
  bounded, and the bound is the replay window the deployment accepts. A
  long-running workspace cannot hold every id it has ever seen.
- **Where it lives.** Under the single-writer requirement of SPECIFICATION.md
  §10.3, one coordinator owns each workspace's log, so the set can live with
  it. A failover replica needs the set to survive the failover, or the window
  reopens.
- **What it costs a legitimate client.** A client that retries a timed-out
  request with the same envelope, the ordinary response to an ambiguous
  network failure, is refused. `task.create` carries `idempotency_key` for
  that case, and it is the mechanism to reach for first. It removes
  duplicates for one method, by a key the caller supplies, and it refuses
  nothing.

The coordinators answer replay in these ways:

- A repeated `task.create` carrying an `idempotency_key` the workspace has
  seen is answered with the task the first call created, and is not recorded.
  Each workspace keeps a bounded window of recent keys, so a key that has left
  the window creates a new task.
- A signed copy of a recorded refusal is answered with that refusal
  **(0.3.0)**, as [Refused calls](#refused-calls) describes.
- Each entry carries the `seq` and `arrived` the coordinator assigns, so a
  replayed call is recorded as a new event at the time it arrived, whatever
  `ts` it carries.

Any other copy of an accepted call is evaluated again. The coordinator
computes the chain itself, so a client sends no `prev_hash` for it to check,
and `ts` is not required to increase (see
[Sender-declared timestamps](#sender-declared-timestamps)). Whether a copy
takes effect depends on the state it meets: a second approval of a closed
review is refused, and a second `task.create` without an `idempotency_key`
creates a second task.

### Sender-declared timestamps

`ts` is the sender's clock. Neither coordinator checks it for order or
plausibility. The log is ordered by the coordinator: each entry carries
`arrived` from the coordinator's clock and a `seq` assigned in acceptance
order, and, where the chain is on, a `prev_hash` linking it to the entry
before. A sender cannot reorder the log by lying about `ts`. Where signatures
are required, `ts` is also used to look up the sender's key (section 4).

A `ts` that goes backwards for one `from` is still worth watching, because it
means a clock is wrong, a queue is replaying, or a client is fabricating
times. Refusing such a request is a deployment decision, and these are its
costs:

- **Millisecond precision.** A participant that sends twice inside one
  millisecond produces two equal timestamps, so a strict rule refuses ordinary
  traffic. Non-decreasing is the strongest rule that does not.
- **Clock skew.** A participant on several hosts, or one whose clock is
  corrected by NTP, will step backwards through no fault of its own.
- **What it buys.** The order of the log does not depend on `ts`, so what the
  check adds is a signal. The integrity of the log is the same without it.
  For most deployments an alert is enough.

Neither coordinator implements the check, and no error code is allocated for
it.

### Refused calls

**(0.3.0)** Everything in this section is behaviour on main that ships in
0.3.0. In 0.2.13 a refused call leaves no entry, and a copy of a refused call
is evaluated like any other request.

A member's refused call that is a governed attempt is recorded on the log,
under `request` with an `outcome` giving the code (SPECIFICATION.md §10.1): a
decision on a review addressed to someone else, a pull on an emergency brake
the workspace has switched off, an act on a paused workspace. Where the chain
is on, the entry's link hashes the outcome together with the request, so
altering either half breaks the chain. Moving the record under `envelope`, to
pass the refusal off as a call that took effect, leaves an entry that is not a
JSON-RPC call, and `audit.verify_chain` reports it as malformed.

Some refusals are never recorded: a malformed or invalid request (`-32700`,
`-32600`, `-32602`), a fault in the coordinator (`-32603`), a failed signature
or key check (`-32070` to `-32073`), a refused read or `audit.submit_to_scitt`,
and a `-32601` unless the profile gate refused a privileged method.
SPECIFICATION.md §10.1 fixes the order in which a coordinator makes its
checks, since the first check that fails decides whether the refusal is
recorded.

Anyone who can read the log holds a copy of every signed request on it. Two
rules keep those copies from acting in their signers' names
(SPECIFICATION.md §10.1). Both compare what the signer signed, the request
without its `sig`, so re-encoding a signature does not make a new request:

- A copy of a recorded refusal is answered with that refusal, carrying
  `data.refused_at_seq`. It is not evaluated again. Without this rule, a
  refused request could be sent once the reason for the refusal had passed,
  for instance after the review was re-addressed or the workspace resumed, and
  take effect.
- A copy of a call that took effect is evaluated as any request is, and a
  refusal of it is not recorded, so a copy cannot put refused attempts on the
  log in its signer's name.

A request counts as signed when it carries a top-level `sig` string, whether
or not signatures are required. An unsigned request is not compared with the
log, so each copy of it can be refused and recorded again. A legitimate retry
is a new request with a new `id`. An accepted call sent again can still take
effect again where the method allows it, as described under
[Envelope id replay](#envelope-id-replay).

What stays off the log is chosen so that, where signatures are required,
recording refusals opens no new way to write to the log in someone else's
name. A call from a sender who is not a member is not recorded, and neither is
a call whose signature or key failed, nor a refused `workspace.create` or
`participant.join`, which run before the sender has a key to check. Without
required signatures there is no sender to protect, since anyone can send any
call in any name. Nor is the log closed to outsiders: `participant.join`
admits any sender who asks, and a member can add entries by sending calls in a
loop, refused or accepted. Rate-limit each participant at the transport where
that matters.

A refusal entry keeps the refused request's content for the life of the log,
as an accepted entry does. A call refused because its sender had no authority
still puts its parameters on the log, so a deployment that redacts or expires
content applies the same policy to refusals (section 8).

---

## 6. Authority

**Membership.** SPECIFICATION.md §6.3.1 makes membership the floor. In both
coordinators a caller must be a member to use `task.create`, `task.update`,
`task.complete`, `review.request`, `decide.approve`, `decide.reject`,
`decide.override`, `abstain.declare`, `escalate.raise`,
`workspace.set_profiles`, `participant.leave` and the routing methods
(`task.route`, `review.depth` and `escalate.auto`) **(0.3.0)**, and every
`control.*`, `deliberate.*`, `handoff.*` and `whisper.*` method. A non-member
is refused with `-32011`, or earlier, at the signature check, where
signatures are required, and the refusal is not recorded.
`participant.rotate_key` and `participant.revoke_key` act only on members'
keys. `workspace.create` and `participant.join` admit anyone. The reads need
membership only where read membership or signatures are required, and the
`audit-scitt/1.0` methods do not check it. Without required signatures,
membership itself is a claim, because the caller writes the `from`.

**Roles.** A role is the string a participant names when it joins, or
`participant` if it names none. The coordinators check one role, `admin`:
`workspace.set_profiles` needs it, and so does revoking another member's
key. `workspace.create` does not make its caller a member, so the first
admin is whoever joins as one. Because a joiner names its own role,
`admin` protects nothing until joins are controlled. `control.*` needs
membership only, and `control.rollback` can restore members' roles from a
snapshot. No method-role policy is enforced (SPECIFICATION.md §6.4), and the
`required_scope` that each method declares in the catalogue is not enforced
either. A review decision also needs the caller to be a reviewer the review
was addressed to, and a review addressed to a `workspace:` or `group:` scope
accepts any member.

**Privileged methods and step-up.** The coordinators treat these methods as
privileged: `control.pause`, `control.resume`, `control.cancel`,
`control.supersede`, `control.snapshot`, `control.rollback`,
`control.set_mode_ceiling`, `workspace.set_profiles`,
`participant.rotate_key` and `participant.revoke_key`. Step-up is an option of
its own, `enforceStepUp`. When it is on:

- A privileged call from a human member, or from any other member bound to a
  verified OIDC token, needs an `auth_time` no older than the workspace's
  `step_up_window_sec`, 300 seconds by default. Where the workspace sets a
  `min_acr`, the member's `acr` must equal it. Otherwise the call is refused
  with `-32402`.
- `auth_time` and `acr` come from the token verified at join. A later join
  with a fresh token for the same subject replaces them, and a token with no
  `acr` clears the old one.
- A human member that joined without a verified token has no `auth_time`, so
  every privileged call it makes is refused, until a later join presents a
  token whose `chap_participant_uri` names it.
- Other members without a verified token are not checked. A member's type is
  the one its first join declared, so a caller that joins as an agent without
  a token is never asked for step-up.

The same list decides one recording rule: when the profile gate refuses a
privileged method, the refusal is recorded **(0.3.0)**.

**The profile gate (0.3.0).** A workspace serves the methods of the profiles
it advertises. A method whose owning profile is not advertised is refused with
`-32601`, the same answer as for a method that does not exist, with
`data.profile` and `data.advertised` for operators. Core methods, the reads and
the key methods `participant.rotate_key` and `participant.revoke_key` are
always served. The gate matches the profile name and ignores the version. In
0.2.13 every method is served, whatever the workspace advertises.

**What a workspace advertises.** At `workspace.create` **(0.3.0)**, a
coordinator that requires signatures adds `security-signed/1.0` to the
profiles, and one with a token verifier adds `identity-oidc/1.0`. Advertising
either without the matching enforcement is refused with `-32602`. Advertising a
profile never turns its enforcement on. Afterwards an admin can change the
advertised list at any time with `workspace.set_profiles`, and that call is
not held to the same rule: it can remove `security-signed/1.0` while
signatures stay required, or add it while they are off. So the descriptor
shows what a workspace advertises now, which can differ from what the
coordinator enforces.

**Modes.** A task's mode is a parameter of `task.create`. `task.create` and
`control.supersede` refuse a mode above the workspace's `mode_ceiling` with
`-32040`, whether or not `modes/1.0` is advertised. The ceiling is
`production` unless `workspace.create` sets it. The workspace's own `mode` is
the default for a task that names none, and a task may carry a higher mode as
long as it is within the ceiling. Under `modes/1.0`, a `trial` task made by
`task.create`, `control.supersede` or `escalate.raise` requires review, the
last **(0.3.0)**. The successor that `escalate.raise` makes takes the mode
`new_task.mode` names, or else the original's, and is refused with `-32040`
when that mode is above the current ceiling **(0.3.0)**. A successor that
`escalate.raise` or `control.supersede` makes also requires review when the
task it replaces did **(0.3.0)**, so neither removes a required review.

Any member can move the ceiling up or down with `control.set_mode_ceiling`,
and `control.rollback` can restore a ceiling from a snapshot. Both are
recorded on the log like any accepted call. Neither needs more than
membership, the `control/1.0` profile **(0.3.0)** and, with step-up on, a
recent `auth_time` for a human or OIDC-bound member. `workspace.set_mode` is
not built, and no policy check applies to a change of ceiling.

The coordinators do not filter shadow output. `shadow_observers` is a field
of the workspace descriptor schema that lists participant URIs. Neither
coordinator stores it, and delivering shadow output only to those
participants is the deployment's job.

**Pausing a participant.** `control.pause` with scope `participant` stops new
tasks being assigned to a member. `task.create` and `control.supersede`
refuse it as an assignee with `-32063`. `escalate.raise` refuses it as an
assignee too, `handoff.accept` refuses it as an acceptor, and `task.route`
never assigns it **(0.3.0)**. The pause holds only while the member
cooperates. Any member can lift it with `control.resume`, the paused member
included, and a member that leaves and joins again comes back unpaused. A
paused member can also still act: it can complete the tasks it holds and
decide reviews, and reviews, whispers and deliberations can still be
addressed to it. Where signatures are required, revoking its keys stops its
signed calls until it registers another key, which a join carrying a token or
presentation that the verifier accepts and that binds a key can do.

---

## 7. Reporting a vulnerability

If you believe you have found a security issue in this specification, in
either coordinator or another package in this repository, or in a deployed
CHAP system you operate, please follow **coordinated disclosure**:

1. **Do not** open a public issue.
2. For an issue in a deployed CHAP system, contact the security contact for
   that deployment. For an issue in this repository, use GitHub's private
   vulnerability reporting, under the Security tab of the repository or at
   <https://github.com/BrightbeamAI/chap/security/advisories/new>. If you
   cannot use it, contact the maintainers listed in
   [MAINTAINERS.md](./MAINTAINERS.md).
3. Include enough detail to reproduce: the affected version, the message
   sequence, the environment, and the expected and actual behaviour.
4. Allow at least 90 days for a fix before disclosure, or coordinate a
   shorter timeline if the issue is already being exploited.

We commit to acknowledging reports within 5 business days, providing a
preliminary assessment within 15 business days, and publishing a fix and
advisory within 90 days where feasible.

### Supported versions

| Version | Security fixes |
|---|---|
| main | Unreleased. Fixes land here, and main becomes 0.3.0. |
| 0.2.13 | Yes, until 0.3.0 is released. |
| 0.2.12 and earlier | No. Upgrade to the latest release. |

Fixes land on main, and on the latest release line as a patch release, for
example 0.2.14. The advisory is published after the fixed release.

---

## 8. Known limitations

These are gaps in the current design and code, stated as they stand.
[ROADMAP.md](./ROADMAP.md) tracks each one with the milestone that closes it.

- **Anyone can join.** `participant.join` admits any caller, in any role it
  names, `admin` included, and joining a workspace that does not exist creates
  it. A first join can claim any name nobody holds yet (section 4). Until
  milestone 0.6 defines admission, authenticate joins at the transport and
  bind each `from` to its caller.
- **Without required signatures, anyone can act in any member's name.** A
  refusal entry then names a sender it cannot prove, because the `from` it
  records is the caller's claim.
- **Authority is coarse.** Roles are chosen by the joiner, `admin` guards
  only `workspace.set_profiles` and revoking another member's key, and the
  declared scopes are not enforced (section 6).
- **Reads are open by default, and the log is stored in the clear.**
  `workspace.describe` and `audit.read` answer any caller unless read
  membership or signatures are required, and since anyone can join, neither
  stops a determined reader. The log keeps every request as sent, so identity
  tokens and credentials presented at join can be read by anyone who can read
  the log. CHAP does not encrypt log content. Hold sensitive content in a
  separate store with its own access control, and put its URI and hash in the
  artefact (SPECIFICATION.md §15.3). Encrypted log content is planned as a
  Draft profile after 1.0.
- **The chain is optional and partly hashed.** It is off until it is switched
  on, and `seq`, `arrived` and the workspace state sit outside the hash. Where
  it is on, it shows that the entries agree with a head. It does not show that
  the log is complete, and it needs a single writer for each workspace.
- **Accepted calls can be replayed** where the method allows it. A copy of an
  accepted call is evaluated again, and only `task.create` takes an
  idempotency key.
- **A call can be answered and never stored.** Both coordinators answer a call
  whether or not its write to storage succeeds.
- **Checkpoints, redaction and export are not built.** `audit.checkpoint`,
  `audit.redact` and `audit.export` are specified only, and the coordinator
  holds no key to sign a checkpoint. Once redaction is built, a redacted
  refusal entry must keep what its signer signed, or its digest, because each
  coordinator rebuilds its index of signed calls from the log and compares
  copies with it.
- **The signatures are not quantum-resistant, and revocation stays in one
  workspace.** Ed25519 would not withstand a large quantum computer. A key
  revoked in one workspace stays valid in others unless the deployment passes
  the revocation on. Quantum-resistant signatures and revocation across
  workspaces are planned as Draft profiles after 1.0.
- **Testing covers part of the protocol.** The conformance harness covers Core
  and the review profile. It runs against a server built on the Python
  coordinator and against a standalone TypeScript server, and has yet to run
  against the TypeScript coordinator package. The differential fuzzer leaves
  out signed calls, modes and the SCITT profile.
- **One team wrote both coordinators, and no outside security review has
  taken place.** The external review runs in milestone 0.7, and milestone
  1.0-rc closes its findings.
