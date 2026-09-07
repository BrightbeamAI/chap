/**
 * @brightbeamai/chap-coordinator-mcp/tools
 *
 * Tool-level descriptions for each CHAP method exposed over MCP. A
 * client shows these in its UI and reads them when choosing a tool, so
 * each states what the method does and the one consequence a caller
 * most needs to know before calling it. Parameter-level detail belongs
 * in schemas.ts.
 */

export const TOOL_DESCRIPTIONS: Record<string, string> = {
  // Core
  "chap.workspace.create":
    "Create a workspace: the container for the participants, tasks and audit log of one piece of collaborative work. " +
    "Returns the workspace id, which every later call carries.",

  "chap.workspace.describe":
    "Report the current state of a workspace: its members, enabled profiles, audit length, and task and override counts.",

  "chap.workspace.set_profiles":
    "Replace the set of profiles enabled on a workspace. " +
    "Enabling audit-scitt/1.0 on a workspace that already has entries leaves those entries outside the hash chain.",

  "chap.participant.join":
    "Add a participant to a workspace. The type given, human, agent, service, group or workspace, " +
    "decides whether they are eligible to review work that requires it.",

  "chap.participant.leave":
    "Remove a participant from a workspace. Entries they have already written stay in the audit log.",

  "chap.task.create":
    "Create a task: a unit of work assigned to one participant. " +
    "Set review_required to make the task's completion depend on a reviewer decision rather than on the assignee.",

  "chap.task.update":
    "Move a task to a new state. Only the transitions in the specification's lifecycle table are accepted, " +
    "and a task that requires review cannot be completed here.",

  "chap.task.complete":
    "Submit a task's output. A task that requires review does not complete: the output is held as the artefact " +
    "under review, the task moves to review_requested, and a reviewer decision completes it. " +
    "Any other task completes immediately.",

  "chap.audit.read":
    "Read entries from a workspace's audit log, optionally within a sequence range and filtered by method, sender or task.",

  // review/1.0
  "chap.review.request":
    "Open a review on a task and address it to one or more reviewers. " +
    "They then call chap.decide.approve, chap.decide.reject, chap.decide.override or chap.abstain.declare. " +
    "Repeating the request with the same artefact adds reviewers to the open review.",

  "chap.decide.approve":
    "Approve the artefact under review. The task completes once the review's rule is satisfied.",

  "chap.decide.reject":
    "Reject the artefact under review. The task is declined, or returns to in_progress if request_revision is set.",

  "chap.decide.override":
    "Correct the artefact under review with an RFC 6902 JSON Patch and accept the result. " +
    "The patch, the rationale and any tags are recorded together, so the audit log holds what was changed and why, " +
    "rather than only that the work was not accepted as written.",

  "chap.abstain.declare":
    "Stand aside from a review, giving a reason and a category. The task moves to abstained.",

  "chap.escalate.raise":
    "Hand a task upwards. The original moves to escalated and is linked to a new task opened for whoever takes it on. " +
    "The successor starts with an empty input unless one is supplied.",

  // whisper/1.0
  "chap.whisper.ask":
    "Put one question to one or more participants, with a deadline and a default. " +
    "If the deadline passes unanswered the default applies, so a task is never blocked waiting on a reply.",

  "chap.whisper.answer":
    "Answer an open whisper. Where the question carried options, the answer must name one of them.",

  // deliberation/1.0
  "chap.deliberate.open":
    "Open a deliberation among several participants under a stated voting rule: " +
    "any_one_approves, all_approve, quorum:N, weighted_vote:T or weighted_vote_with_veto:T.",

  "chap.deliberate.comment":
    "Record a comment on an open deliberation, so the reasoning is on the audit log alongside the votes.",

  "chap.deliberate.vote":
    "Cast a yea, nay or abstain in an open deliberation. Each participant votes once.",

  "chap.deliberate.close":
    "Close a deliberation and compute its outcome from the votes cast under its rule.",

  // handoff/1.0
  "chap.handoff.propose":
    "Propose handing one or more tasks to another participant or a group, with the context needed to pick them up. " +
    "Every task must currently be assigned to the proposer.",

  "chap.handoff.accept":
    "Accept a proposed handoff. The accepted tasks are reassigned to the accepting participant.",

  "chap.handoff.decline":
    "Decline a proposed handoff, with a reason and optionally a suggestion of who should take it instead.",

  // control/1.0
  "chap.control.pause":
    "Pause work. Scoped to a task it moves that task to paused; to a participant it stops new tasks being assigned to them; " +
    "to the workspace it refuses every method except describing, reading the audit log, joining, leaving and resuming.",

  "chap.control.resume":
    "Resume work paused at the same scope: a task returns to in_progress, a participant can be assigned tasks again, " +
    "a workspace returns to active.",

  "chap.control.cancel":
    "Cancel a task. Cancelled is terminal, and a task that has already settled cannot be cancelled.",

  "chap.control.snapshot":
    "Capture the workspace state as an artefact and return its id, which chap.control.rollback takes as its target.",

  "chap.control.rollback":
    "Restore workspace state from a snapshot. The mode ceiling and member roles are restored; " +
    "the rollback is appended to the audit log rather than rewriting it.",

  "chap.control.supersede":
    "Replace a task with a successor in one call. The original moves to superseded and stays linked to its replacement.",

  "chap.control.set_mode_ceiling":
    "Set the highest operating mode tasks in this workspace may request. A task above the ceiling is refused.",

  // routing/1.0
  "chap.task.route":
    "Choose an assignee for a task from a list of candidates and record a route_decision artefact " +
    "naming the policy, the candidate chosen, and the alternatives it passed over.",

  "chap.review.depth":
    "Decide how much review a task warrants, skip, spot_check or full, from its routing hints. " +
    "Records a route_decision artefact giving the rule that produced the answer.",

  "chap.escalate.auto":
    "Evaluate a task's routing hints against the escalation policy and report whether it should be escalated, and to whom. " +
    "Records a route_decision artefact.",

  // security-signed/1.0
  "chap.participant.rotate_key":
    "Retire a participant's signing key and register its replacement. " +
    "The old key stays in the key history with a valid_until timestamp, so envelopes it signed still verify.",

  "chap.participant.revoke_key":
    "Revoke a signing key, for example after a device is lost. Signatures presented with it are refused from then on. " +
    "Revoking another participant's key requires the admin role.",

  // audit-scitt/1.0
  "chap.audit.submit_to_scitt":
    "Build COSE_Sign1-shaped statements for a range of audit entries and submit them to the configured SCITT transparency service. " +
    "Where none is configured the statements are returned for submission out of band.",

  "chap.audit.verify_receipt":
    "Verify a SCITT receipt through the configured verifier. Verification fails closed where no verifier is configured.",

  "chap.audit.verify_chain":
    "Replay a workspace's prev-hash chain. Only status verified with ok true means the log was checked and is intact. " +
    "Status not_evaluated with ok false means part of the log was never checked, so its integrity is unknown and must not " +
    "be reported as verified; entries_unchecked says how much. An error means the chain is broken or absent.",
};
