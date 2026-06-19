/**
 * @chap/coordinator-mcp/tools
 *
 * One-line descriptions for each CHAP method exposed as an MCP tool.
 * These show up in MCP-client UIs (Claude Desktop, Cursor, etc.) and
 * are what the LLM reads when deciding whether to call the tool.
 * Tuned for clarity over brevity.
 */

export const TOOL_DESCRIPTIONS: Record<string, string> = {
  // Core
  "chap.workspace.create":
    "Create a new CHAP workspace where humans and agents can collaborate on tasks. " +
    "Returns the workspace id.",

  "chap.workspace.describe":
    "Get the current state of a workspace: members, profiles, audit length, task and override counts.",

  "chap.workspace.set_profiles":
    "Change which CHAP profiles are enabled on a workspace.",

  "chap.participant.join":
    "Add a participant (human, agent, or service) to a workspace.",

  "chap.participant.leave":
    "Remove a participant from a workspace.",

  "chap.task.create":
    "Create a new task in a workspace. Tasks are units of work assigned to a participant. " +
    "Pass routing_hints (criticality, deadline) to inform later routing decisions.",

  "chap.task.update":
    "Update a task's state (e.g. in_progress, declined, paused). Only legal transitions are accepted.",

  "chap.task.complete":
    "Mark a task as completed, attaching the output artefact. Use chap.review.request afterwards if review is required.",

  "chap.audit.read":
    "Read entries from a workspace's audit log. Supports a sequence-number range and a filter by method, sender, or task id.",

  // review/1.0
  "chap.review.request":
    "Open a review on a completed task. Pass one or more reviewers in 'to' and the draft artefact. " +
    "The reviewers then call chap.decide.approve / .reject / .override or chap.abstain.declare.",

  "chap.decide.approve":
    "Approve a task that's under review. Resolves the review and marks the task completed.",

  "chap.decide.reject":
    "Reject a task that's under review. Pass request_revision: true to send it back to in_progress instead of declined.",

  "chap.decide.override":
    "Apply an RFC 6902 JSON Patch to the artefact under review and accept the result. " +
    "Carries a structured override artefact: diff + rationale + tags. " +
    "This is the structured-override-as-learning-signal mechanism that distinguishes CHAP from approve/reject-only workflows.",

  "chap.abstain.declare":
    "Decline to review a task with a reason (e.g. conflict_of_interest). The task transitions to abstained.",

  "chap.escalate.raise":
    "Create a new task that supersedes the original, typically assigned to a more senior reviewer. " +
    "Use when the current reviewer cannot decide.",

  // whisper/1.0
  "chap.whisper.ask":
    "Pose a deadline-bound question to one or more participants. Carries a default that's applied if the deadline lapses. " +
    "Use for quick clarifications that shouldn't block a task indefinitely.",

  "chap.whisper.answer":
    "Answer a previously asked whisper. If the whisper had options, answer_option must be one of them.",

  // deliberation/1.0
  "chap.deliberate.open":
    "Open a multi-participant deliberation with a voting rule (any_one_approves, all_approve, quorum:N, weighted_vote:T, weighted_vote_with_veto:T).",

  "chap.deliberate.comment":
    "Add a comment to an open deliberation.",

  "chap.deliberate.vote":
    "Cast a vote (yea / nay / abstain) in a deliberation. Each voter votes at most once.",

  "chap.deliberate.close":
    "Close a deliberation and compute the outcome based on its voting rule.",

  // handoff/1.0
  "chap.handoff.propose":
    "Propose handing off one or more open tasks to another participant. Recipient may be a single URI or a group:... URI.",

  "chap.handoff.accept":
    "Accept a previously proposed handoff. The accepted tasks are reassigned to the accepting participant.",

  "chap.handoff.decline":
    "Decline a previously proposed handoff. Optionally suggest a different target.",

  // control/1.0
  "chap.control.pause":
    "Pause work, scoped to a single task, a participant, or the whole workspace.",

  "chap.control.resume":
    "Resume previously paused work.",

  "chap.control.cancel":
    "Cancel a task that is not yet terminal.",

  "chap.control.snapshot":
    "Capture the current workspace state as an artefact (members, open tasks, mode ceiling, etc.). Used as the rollback target.",

  "chap.control.rollback":
    "Roll the workspace back to a previously captured snapshot. Specify what_to_restore to scope the rollback.",

  "chap.control.supersede":
    "Mark a task superseded by a new successor task in one envelope. Use when a task needs to be replaced wholesale rather than re-routed.",

  "chap.control.set_mode_ceiling":
    "Set the maximum operating mode allowed in the workspace (shadow, trial, production). Tasks requesting higher modes are rejected.",

  // routing/1.0
  "chap.task.route":
    "Pick an assignee for a task from a list of candidates. Produces a route_decision artefact recording the policy used.",

  "chap.review.depth":
    "Decide the review depth for a task (skip / spot_check / full) based on routing hints. Produces a route_decision artefact.",

  "chap.escalate.auto":
    "Evaluate whether a task should be auto-escalated based on its routing hints (criticality, confidence). Produces a route_decision artefact.",

  // security-signed/1.0
  "chap.participant.rotate_key":
    "Rotate a participant's signing key. The old key remains valid for verifying historical envelopes via valid_until.",

  "chap.participant.revoke_key":
    "Revoke a signing key (e.g. on suspected compromise). Envelopes signed with the revoked key after revoke time are rejected.",

  // audit-scitt/1.0
  "chap.audit.submit_to_scitt":
    "Build COSE_Sign1-shaped audit statements for a range of envelopes and submit them to the configured SCITT transparency service.",

  "chap.audit.verify_receipt":
    "Verify a SCITT receipt against the configured verifier.",

  "chap.audit.verify_chain":
    "Verify the local prev-hash chain across a workspace's audit log.",
};
