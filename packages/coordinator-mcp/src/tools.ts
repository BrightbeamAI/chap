/**
 * @brightbeamai/chap-coordinator-mcp/tools
 *
 * Tool-level descriptions and behavioural annotations for each CHAP
 * method exposed over MCP. A client shows these in its UI and reads
 * them when choosing a tool.
 *
 * A description says what the method does, how it differs from its
 * nearest sibling, and the consequence a caller most needs to know
 * before calling. It does not restate the parameter schema: every
 * parameter is already described in schemas.ts, and repeating that
 * here costs length and adds no meaning.
 *
 * The annotations carry what prose states poorly.
 *
 * `readOnlyHint` is not a judgement. It is exactly the coordinator's
 * own READ_ONLY_METHODS set, the list that decides whether an envelope
 * is recorded on the audit chain, and a test holds the two together.
 * Anything absent from that set appends an entry, which is a change of
 * state whatever else it does.
 *
 * `idempotentHint` was derived by calling each method twice with
 * identical arguments and comparing workspace state, and is claimed
 * only where the second call both succeeds and leaves that state
 * untouched. Where a repeat is refused rather than absorbed, the hint
 * is absent: an agent must not read an error as a safe retry. It is
 * also absent from `task.create`, which is idempotent only when the
 * caller supplies `idempotency_key` and creates a second task when
 * they do not.
 *
 * `destructiveHint` marks the methods that overwrite or remove state
 * that was there before, or settle it somewhere it cannot come back
 * from. Most of CHAP is additive and carries `false`.
 */

export const TOOL_DESCRIPTIONS: Record<string, string> = {
  // Core
  "chap.workspace.create":
    "Create a workspace: the container for the participants, tasks and audit log of one piece of " +
    "collaborative work. Returns the workspace id, which every later call carries.",

  "chap.workspace.describe":
    "Report the current state of a workspace: its members, enabled profiles, audit length, and task and " +
    "override counts.",

  "chap.workspace.set_profiles":
    "Replace the whole set of profiles enabled on a workspace. The list is not merged: any profile absent " +
    "from it is dropped, so send the complete set you want. Enabling audit-scitt/1.0 on a workspace that " +
    "already has entries starts the chain from that point and leaves the earlier entries permanently outside " +
    "it, which chap.audit.verify_chain then reports as not_evaluated. Prefer setting profiles at " +
    "chap.workspace.create, where no entries exist yet to strand.",

  "chap.participant.join":
    "Add a participant to a workspace. The type given, human, agent, service, group or workspace, decides " +
    "whether they are eligible to review work that requires it.",

  "chap.participant.leave":
    "Remove a participant from a workspace. Entries they have already written stay in the audit log and their " +
    "past signatures still verify, so leaving is not a way to withdraw a decision. They can no longer be " +
    "assigned tasks or act as a reviewer, and returning means a fresh chap.participant.join.",

  "chap.task.create":
    "Create a task: a unit of work assigned to one participant. Set review_required to make completion depend " +
    "on a reviewer decision rather than on the assignee. Returns a task_id that the rest of the lifecycle " +
    "takes. Supplying idempotency_key is what makes a retry safe: a repeat carrying a key already seen " +
    "returns the original task, while a repeat without one creates a second task. Assigning to a paused " +
    "member is refused.",

  "chap.task.update":
    "Move a task to a new state. Only the transitions in the specification's lifecycle table are accepted, " +
    "and a task that requires review cannot be completed here.",

  "chap.task.complete":
    "Submit a task's output. A task that requires review does not complete: the output is held as the " +
    "artefact under review, the task moves to review_requested, and a reviewer decision completes it. Any " +
    "other task completes immediately.",

  "chap.audit.read":
    "Read entries from a workspace's audit log, optionally within a sequence range and filtered by method, " +
    "sender or task. This is the query surface for everything the workspace has recorded, decisions and " +
    "overrides included. Reads only: it records nothing, so it never appears in its own output. Entries come " +
    "back as recorded, without aggregation, so grouping them, by tag for instance, is the reader's job.",

  // review/1.0
  "chap.review.request":
    "Open a review on a task and address it to one or more reviewers. They then call chap.decide.approve, " +
    "chap.decide.reject, chap.decide.override or chap.abstain.declare. Repeating the request with the same " +
    "artefact adds reviewers to the open review.",

  "chap.decide.approve":
    "Approve the artefact under review as it stands. Only a reviewer the review was addressed to may decide, " +
    "and anyone else is refused with -32011. The task completes once the review's rule is satisfied, so under " +
    "all_approve or quorum:N an approval may leave the review open awaiting others. Use chap.decide.override " +
    "instead to accept a corrected version.",

  "chap.decide.reject":
    "Reject the artefact under review. The task is declined, or returns to in_progress when request_revision " +
    "is set, which is the choice between ending the work and sending it back. Only a reviewer the review was " +
    "addressed to may decide. Use chap.decide.override instead to correct the artefact and accept it rather " +
    "than refuse it.",

  "chap.decide.override":
    "Correct the artefact under review with an RFC 6902 JSON Patch and accept the result. The patch, the " +
    "rationale and any tags are recorded together, so the audit log holds what was changed and why, rather " +
    "than only that the work was not accepted as written.",

  "chap.abstain.declare":
    "Stand aside from a review this participant cannot decide, for example on a conflict of interest or for " +
    "want of context. Use it in place of chap.decide.reject, which judges the work; abstaining judges nothing " +
    "and leaves the artefact intact. It requires an open review addressed to the caller and answers -32010 " +
    "otherwise. The task moves to abstained, which is not terminal: chap.escalate.raise or a fresh " +
    "chap.review.request can still carry it forward.",

  "chap.escalate.raise":
    "Hand a task upwards, where the current holder can act but should not decide alone. The original moves to " +
    "escalated and is linked to a new task for whoever takes it on. Use chap.handoff.propose to pass work " +
    "sideways without that link, and chap.control.supersede to replace a task rather than raise it. The " +
    "successor starts with an empty input unless one is supplied, so restate whatever the new assignee needs. " +
    "A completed, cancelled or superseded task cannot be escalated.",

  // whisper/1.0
  "chap.whisper.ask":
    "Put one question to one or more participants, with a deadline and a default. If the deadline passes " +
    "unanswered the default applies, so a task is never blocked waiting on a reply.",

  "chap.whisper.answer":
    "Answer an open whisper. Where the question carried options, the answer must name one of their ids and " +
    "any other value is refused with -32022. A whisper already answered is refused with -32020 and one past " +
    "its deadline with -32021, in which case the default supplied at chap.whisper.ask has already been " +
    "applied.",

  // deliberation/1.0
  "chap.deliberate.open":
    "Open a group decision among several participants under a stated voting rule: any_one_approves, " +
    "all_approve, quorum:N, weighted_vote:T or weighted_vote_with_veto:T. Use it where several people must " +
    "weigh in on one question; chap.review.request is the tool for judging one participant's output. Returns " +
    "a deliberation_id that the comment, vote and close tools all take. The participant list, rule, weights " +
    "and veto map are fixed at open, so the terms are settled before anyone votes. No outcome exists until " +
    "chap.deliberate.close computes one.",

  "chap.deliberate.comment":
    "Record a comment on an open deliberation, so the reasoning behind a vote survives alongside the tally. " +
    "Comments carry no weight in the outcome and may be added by any participant at any point before " +
    "chap.deliberate.close.",

  "chap.deliberate.vote":
    "Cast a yea, nay or abstain in an open deliberation. Each participant votes once and a second attempt " +
    "answers -32031, so a vote cannot be revised. An abstention is recorded but counts as neither side, which " +
    "under all_approve or quorum:N withholds the approval those rules need. The tally is computed later, by " +
    "chap.deliberate.close.",

  "chap.deliberate.close":
    "Close a deliberation and compute its outcome from the votes cast under its rule. Closing is final: " +
    "further comments and votes are refused with -32032. Calling it again on a closed deliberation returns " +
    "the same outcome and changes nothing, so a retry is safe. Votes never cast are simply absent from the " +
    "tally rather than counted as abstentions.",

  // handoff/1.0
  "chap.handoff.propose":
    "Propose handing one or more tasks to another participant or a group, with the context needed to pick " +
    "them up. Every task must currently be assigned to the proposer.",

  "chap.handoff.accept":
    "Accept a proposed handoff. The accepted tasks are reassigned to the accepting participant, which is the " +
    "point at which responsibility actually moves. Omitting accepted_task_ids accepts all of them. Accepting " +
    "resolves the proposal for good: a later accept or decline on the same handoff answers -32051.",

  "chap.handoff.decline":
    "Decline a proposed handoff. The tasks stay with the proposer and nothing is reassigned, which is the " +
    "difference from chap.handoff.accept. Declining resolves the proposal for good: a later accept or decline " +
    "on the same handoff answers -32051, and trying again means a fresh proposal. The reason is recorded so " +
    "the proposer can route the work elsewhere.",

  // control/1.0
  "chap.control.pause":
    "Pause work. Scoped to a task it moves that task to paused; to a participant it stops new tasks being " +
    "assigned to them; to the workspace it refuses every method except describing, reading the audit log, " +
    "joining, leaving and resuming.",

  "chap.control.resume":
    "Resume work paused at the same scope: a task returns to in_progress, a participant can be assigned tasks " +
    "again, a workspace returns to active.",

  "chap.control.cancel":
    "Cancel a task. Cancelled is terminal, and a task that has already settled cannot be cancelled.",

  "chap.control.snapshot":
    "Capture the workspace state as an artefact before a change you may want to undo. Returns a snapshot " +
    "artefact id, which is the only thing chap.control.rollback accepts as a target; the label is for a human " +
    "reading the log and cannot be used to select it. Taking a snapshot changes nothing. Note that a rollback " +
    "restores only the mode ceiling and member roles, so a snapshot is a narrow safety net rather than a " +
    "general undo.",

  "chap.control.rollback":
    "Restore workspace state from a snapshot. The mode ceiling and member roles are restored; the rollback is " +
    "appended to the audit log rather than rewriting it.",

  "chap.control.supersede":
    "Replace a task with a successor in one call. The original moves to superseded and stays linked to its " +
    "replacement.",

  "chap.control.set_mode_ceiling":
    "Set the highest operating mode tasks in this workspace may request. A task above the ceiling is refused.",

  // routing/1.0
  "chap.task.route":
    "Choose an assignee for a task from a list of candidates and reassign the task to the one selected. Use " +
    "it where the choice itself belongs on the record: it writes a route_decision artefact naming the policy, " +
    "the candidate chosen and the alternatives passed over, none of which is captured by setting assignee " +
    "directly on chap.task.create. The default policy takes the first candidate that is a workspace member; " +
    "an operator-supplied policy may choose on any basis.",

  "chap.review.depth":
    "Decide how much review a task warrants, skip, spot_check or full, from its routing hints. Records a " +
    "route_decision artefact giving the rule that produced the answer.",

  "chap.escalate.auto":
    "Evaluate a task's routing hints against the escalation policy and report whether it should be escalated, " +
    "and to whom. It decides only: a route_decision artefact is recorded but the task does not move, so act " +
    "on the answer with chap.escalate.raise. The default policy escalates on criticality critical, or high " +
    "with confidence below 0.6.",

  // security-signed/1.0
  "chap.participant.rotate_key":
    "Retire a participant's signing key and register its replacement. The old key stays in the key history " +
    "with a valid_until timestamp, so envelopes it signed still verify.",

  "chap.participant.revoke_key":
    "Revoke a signing key, for example after a device is lost. Signatures presented with it are refused from " +
    "then on. Revoking another participant's key requires the admin role.",

  // audit-scitt/1.0
  "chap.audit.submit_to_scitt":
    "Build COSE_Sign1-shaped statements for a range of audit entries and submit them to the configured SCITT " +
    "transparency service. Where none is configured the statements are returned for submission out of band.",

  "chap.audit.verify_receipt":
    "Check a receipt returned by an external SCITT transparency service, confirming that service logged the " +
    "statement. This is the external half of verification; chap.audit.verify_chain checks the local prev-hash " +
    "chain instead, and the two answer different questions. Verification is delegated to a hook the " +
    "deployment supplies and fails closed with -32082 when none is configured, so a missing verifier can " +
    "never read as a pass. Reads only: nothing is recorded.",

  "chap.audit.verify_chain":
    "Replay a workspace's prev-hash chain. Only status verified with ok true means the log was checked and is " +
    "intact. Status not_evaluated with ok false means part of the log was never checked, so its integrity is " +
    "unknown and must not be reported as verified; entries_unchecked says how much. An error means the chain " +
    "is broken or absent.",
};

/** MCP tool annotations (2025-03-26 onwards). Every field is a hint. */
export interface ToolAnnotations {
  /** Human-readable title, shown by a client instead of the raw tool name. */
  title: string;
  /** Does not modify state. Mirrors the coordinator's own read-only set. */
  readOnlyHint: boolean;
  /** May overwrite or remove existing state, or settle it irreversibly. */
  destructiveHint: boolean;
  /** A repeat with identical arguments succeeds and changes nothing further. */
  idempotentHint: boolean;
  /** Reaches a system outside this coordinator. */
  openWorldHint: boolean;
}

/**
 * Read-only, and so never recorded on the chain. This is the coordinator's own
 * READ_ONLY_METHODS set, the list that decides whether an envelope is
 * recorded. It is repeated here rather than imported so the adapter does not
 * depend on a coordinator export at runtime, and annotations.test.ts reads
 * that source and fails if the two ever diverge.
 */
const READ_ONLY = new Set([
  "chap.workspace.describe", "chap.audit.read", "chap.audit.verify_chain", "chap.audit.verify_receipt",
]);

/** Overwrites, removes, or settles state irreversibly. */
const DESTRUCTIVE = new Set([
  "chap.workspace.set_profiles",   // replaces the profile set; can strand entries outside the chain
  "chap.participant.leave",        // removes a member
  "chap.participant.revoke_key",   // invalidates a key from then on
  "chap.control.cancel",           // cancelled is terminal
  "chap.control.supersede",        // superseded is terminal
  "chap.control.rollback",         // overwrites the mode ceiling and member roles
]);

/** Verified by calling twice with identical arguments and diffing workspace state. */
const IDEMPOTENT = new Set([
  "chap.workspace.describe", "chap.audit.read", "chap.audit.verify_chain", "chap.audit.verify_receipt",
  "chap.participant.join",         // a re-join is additive and adds no duplicate member
  "chap.participant.leave",        // leaving twice leaves the same absence
  "chap.control.pause",            // pausing an already paused target changes nothing
  "chap.control.set_mode_ceiling", // setting the ceiling it already has changes nothing
  "chap.deliberate.close",         // returns the same outcome, recomputing nothing
]);

/** Reaches a transparency service outside this coordinator. */
const OPEN_WORLD = new Set([
  "chap.audit.submit_to_scitt", "chap.audit.verify_receipt",
]);

/**
 * Human-readable titles. A client that shows a title instead of the tool
 * name should still show something a person can read at a glance.
 */
const TITLES: Record<string, string> = {
  "chap.workspace.create": "Create workspace",
  "chap.workspace.describe": "Describe workspace",
  "chap.workspace.set_profiles": "Replace workspace profiles",
  "chap.participant.join": "Add participant",
  "chap.participant.leave": "Remove participant",
  "chap.task.create": "Create task",
  "chap.task.update": "Change task state",
  "chap.task.complete": "Submit task output",
  "chap.audit.read": "Read audit log",
  "chap.review.request": "Request review",
  "chap.decide.approve": "Approve",
  "chap.decide.reject": "Reject",
  "chap.decide.override": "Override with a correction",
  "chap.abstain.declare": "Abstain from review",
  "chap.escalate.raise": "Escalate task",
  "chap.whisper.ask": "Ask a whisper",
  "chap.whisper.answer": "Answer a whisper",
  "chap.deliberate.open": "Open deliberation",
  "chap.deliberate.comment": "Comment on deliberation",
  "chap.deliberate.vote": "Vote in deliberation",
  "chap.deliberate.close": "Close deliberation",
  "chap.handoff.propose": "Propose handoff",
  "chap.handoff.accept": "Accept handoff",
  "chap.handoff.decline": "Decline handoff",
  "chap.control.pause": "Pause work",
  "chap.control.resume": "Resume work",
  "chap.control.cancel": "Cancel task",
  "chap.control.snapshot": "Snapshot workspace",
  "chap.control.rollback": "Roll back to snapshot",
  "chap.control.supersede": "Supersede task",
  "chap.control.set_mode_ceiling": "Set mode ceiling",
  "chap.task.route": "Route task to an assignee",
  "chap.review.depth": "Decide review depth",
  "chap.escalate.auto": "Evaluate auto-escalation",
  "chap.participant.rotate_key": "Rotate signing key",
  "chap.participant.revoke_key": "Revoke signing key",
  "chap.audit.submit_to_scitt": "Anchor audit log in SCITT",
  "chap.audit.verify_receipt": "Verify SCITT receipt",
  "chap.audit.verify_chain": "Verify audit chain",
};

/** Annotations for every tool, derived from the sets above. */
export const TOOL_ANNOTATIONS: Record<string, ToolAnnotations> = Object.fromEntries(
  Object.keys(TOOL_DESCRIPTIONS).map((name) => [name, {
    title:           TITLES[name] ?? name,
    readOnlyHint:    READ_ONLY.has(name),
    // Meaningful only when readOnlyHint is false, per the specification.
    destructiveHint: DESTRUCTIVE.has(name),
    idempotentHint:  IDEMPOTENT.has(name),
    openWorldHint:   OPEN_WORLD.has(name),
  }]),
);
