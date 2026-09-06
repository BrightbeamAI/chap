# CHAP 0.2.12: review rules, modes gating, and an envelope ceiling

Read the next section before upgrading if you use `modes/1.0`.

## If you are upgrading

**`task.complete` on a trial-mode task no longer completes it.** Trial mode
forces review, so the call opens a review and the task moves to
`review_requested`. A reviewer decision completes it. Anything expecting
`completed` back from `task.complete` on a trial task now has to follow the
review.

If you never loaded `modes/1.0`, nothing changes for you. That is deliberate:
until this release, mode semantics applied to every workspace whether or not
the profile was loaded, and correcting that is most of what this release is.

## review_required is enforced

`review_required` was set in three places, serialised onto the task, and read
by neither coordinator. A task whose review was mandatory completed with
unreviewed output and no `decide.*` in the chain, which is the failure mode
CHAP exists to prevent.

`task.complete` now opens the review that `review.md` §3.1 always described.
The submitted output becomes the artefact under review, and only a reviewer
decision takes the task to `completed`.

The implicit review is addressed to the members who are neither the completer
nor the assignee. Without that, a single-member workspace would have the
producer approving its own work and the chain would carry a `decide.approve`
that looked like oversight. Where nobody qualifies, the completion is refused
with `-32011` rather than opening a review only its author could decide. An
explicit `review.request` keeps whatever `to` it was given.

`decide.approve` also evaluates `review.rule` now, rather than completing on
the first approval whatever the rule said.

## Modes stops applying to workspaces that never asked for it

Enforcing `review_required` exposed why nobody had noticed it was inert. New
workspaces defaulted to `mode: "trial"`, and the trial forcing ran with no
check that `modes/1.0` was loaded. Every default workspace, including a
Core-only one, silently forced review on every task.

`modes.md` declares the profile "Depends on: Core", and the workspace
descriptor examples in SPECIFICATION.md use `"mode": "production"`, so the
trial default and the ungated forcing were a defect rather than an intended
default. Mode semantics are now gated on the profile being loaded.

## The reference server and the harness

`reference/core-plus-review` is a from-scratch reimplementation that shares no
code with the packages, and it had none of the review-required behaviour. It
now matches the coordinators, including the eligible-reviewer rule and the
`-32011` refusal.

The conformance harness never created a workspace, relying on
`participant.join` to auto-create one, so it inherited each target's defaults.
Those differ: the Python reference loads every profile and defaults to trial,
the Core+Review TypeScript reference has no modes at all. Once trial began
forcing review, `cm-08` failed against one target and passed against the other,
and the two had not been comparable for some time. The harness now pins its
workspace to production mode before any vector runs, and both references pass
all 26.

## Also in this release

A maximum envelope size is enforced and published, with request body size and
JSON depth capped in the reference TypeScript servers. `whisper.*` requires
workspace membership. `superseded` is terminal in `control.pause` and
`control.cancel`. JSON Patch array indices parse through one strict shared
rule. Signature verification fails closed with `SIG_VERIFY_FAILED` on an
exception. The threaded Python reference server serialises dispatch.

## Versions

All nine packages move to 0.2.12 together.

| Package | Version |
|---|---|
| `@brightbeamai/chap-coordinator`, `-mcp`, `-a2a` | 0.2.12 |
| `chap-coordinator` (PyPI) | 0.2.12 |
| `chap-langgraph`, `chap-pydantic-ai`, `chap-llama-index`, `chap-ag2`, `chap-google-adk` | 0.2.12 |

The wire format is unchanged. Both coordinators remain at parity, and both
references pass the conformance harness.

Full detail in [`CHANGELOG.md`](./CHANGELOG.md).
