"""
chap_coordinator.transports.mcp_tools
======================================

Tool-level descriptions for each CHAP method exposed over MCP. A client
shows these in its UI and reads them when choosing a tool.

Mirrors ``packages/coordinator-mcp/src/tools.ts``. The table below is
generated from that file by ``scripts/sync-mcp-schemas.mjs``; CI fails if
the two drift apart.
"""
from __future__ import annotations

from typing import Any


# --- BEGIN GENERATED DESCRIPTIONS (scripts/sync-mcp-schemas.mjs) ---
# Generated from packages/coordinator-mcp/src/tools.ts. Do not edit by
# hand: edit the TypeScript table and run scripts/sync-mcp-schemas.mjs.
TOOL_DESCRIPTIONS: dict[str, str] = {
    "chap.workspace.create":
        "Create a workspace: the container for the participants, tasks and audit log of one piece of collaborative work. Returns the workspace id, which every later call carries.",
    "chap.workspace.describe":
        "Report the current state of a workspace: its members, enabled profiles, audit length, and task and override counts.",
    "chap.workspace.set_profiles":
        "Replace the whole set of profiles enabled on a workspace. The list is not merged: any profile absent from it is dropped, so send the complete set you want. Enabling audit-scitt/1.0 on a workspace that already has entries starts the chain from that point and leaves the earlier entries permanently outside it, which chap.audit.verify_chain then reports as not_evaluated. Prefer setting profiles at chap.workspace.create, where no entries exist yet to strand.",
    "chap.participant.join":
        "Add a participant to a workspace. The type given, human, agent, service, group or workspace, decides whether they are eligible to review work that requires it.",
    "chap.participant.leave":
        "Remove a participant from a workspace. Entries they have already written stay in the audit log and their past signatures still verify, so leaving is not a way to withdraw a decision. They can no longer be assigned tasks or act as a reviewer, and returning means a fresh chap.participant.join.",
    "chap.task.create":
        "Create a task: a unit of work assigned to one participant. Set review_required to make completion depend on a reviewer decision rather than on the assignee. Returns a task_id that the rest of the lifecycle takes. Supplying idempotency_key is what makes a retry safe: a repeat carrying a key already seen returns the original task, while a repeat without one creates a second task. Assigning to a paused member is refused.",
    "chap.task.update":
        "Move a task to a new state. Only the transitions in the specification's lifecycle table are accepted, and a task that requires review cannot be completed here.",
    "chap.task.complete":
        "Submit a task's output. A task that requires review does not complete: the output is held as the artefact under review, the task moves to review_requested, and a reviewer decision completes it. Any other task completes immediately.",
    "chap.audit.read":
        "Read entries from a workspace's audit log, optionally within a sequence range and filtered by method, sender or task. This is the query surface for everything the workspace has recorded, decisions and overrides included. Reads only: it records nothing, so it never appears in its own output. Entries come back as recorded, without aggregation, so grouping them, by tag for instance, is the reader's job.",
    "chap.review.request":
        "Open a review on a task and address it to one or more reviewers. They then call chap.decide.approve, chap.decide.reject, chap.decide.override or chap.abstain.declare. Repeating the request with the same artefact adds reviewers to the open review.",
    "chap.decide.approve":
        "Approve the artefact under review as it stands. Only a reviewer the review was addressed to may decide, and anyone else is refused with -32011. The task completes once the review's rule is satisfied, so under all_approve or quorum:N an approval may leave the review open awaiting others. Use chap.decide.override instead to accept a corrected version.",
    "chap.decide.reject":
        "Reject the artefact under review. The task is declined, or returns to in_progress when request_revision is set, which is the choice between ending the work and sending it back. Only a reviewer the review was addressed to may decide. Use chap.decide.override instead to correct the artefact and accept it rather than refuse it.",
    "chap.decide.override":
        "Correct the artefact under review with an RFC 6902 JSON Patch and accept the result. The patch, the rationale and any tags are recorded together, so the audit log holds what was changed and why, rather than only that the work was not accepted as written.",
    "chap.abstain.declare":
        "Stand aside from a review this participant cannot decide, for example on a conflict of interest or for want of context. Use it in place of chap.decide.reject, which judges the work; abstaining judges nothing and leaves the artefact intact. It requires an open review addressed to the caller and answers -32010 otherwise. The task moves to abstained, which is not terminal: chap.escalate.raise or a fresh chap.review.request can still carry it forward.",
    "chap.escalate.raise":
        "Hand a task upwards, where the current holder can act but should not decide alone. The original moves to escalated and is linked to a new task for whoever takes it on. Use chap.handoff.propose to pass work sideways without that link, and chap.control.supersede to replace a task rather than raise it. The successor starts with an empty input unless one is supplied, so restate whatever the new assignee needs. A completed, cancelled or superseded task cannot be escalated.",
    "chap.whisper.ask":
        "Put one question to one or more participants, with a deadline and a default. If the deadline passes unanswered the default applies, so a task is never blocked waiting on a reply.",
    "chap.whisper.answer":
        "Answer an open whisper. Where the question carried options, the answer must name one of their ids and any other value is refused with -32022. A whisper already answered is refused with -32020 and one past its deadline with -32021, in which case the default supplied at chap.whisper.ask has already been applied.",
    "chap.deliberate.open":
        "Open a group decision among several participants under a stated voting rule: any_one_approves, all_approve, quorum:N, weighted_vote:T or weighted_vote_with_veto:T. Use it where several people must weigh in on one question; chap.review.request is the tool for judging one participant's output. Returns a deliberation_id that the comment, vote and close tools all take. The participant list, rule, weights and veto map are fixed at open, so the terms are settled before anyone votes. No outcome exists until chap.deliberate.close computes one.",
    "chap.deliberate.comment":
        "Record a comment on an open deliberation, so the reasoning behind a vote survives alongside the tally. Comments carry no weight in the outcome and may be added by any participant at any point before chap.deliberate.close.",
    "chap.deliberate.vote":
        "Cast a yea, nay or abstain in an open deliberation. Each participant votes once and a second attempt answers -32031, so a vote cannot be revised. An abstention is recorded but counts as neither side, which under all_approve or quorum:N withholds the approval those rules need. The tally is computed later, by chap.deliberate.close.",
    "chap.deliberate.close":
        "Close a deliberation and compute its outcome from the votes cast under its rule. Closing is final: further comments and votes are refused with -32032. Calling it again on a closed deliberation returns the same outcome and changes nothing, so a retry is safe. Votes never cast are simply absent from the tally rather than counted as abstentions.",
    "chap.handoff.propose":
        "Propose handing one or more tasks to another participant or a group, with the context needed to pick them up. Every task must currently be assigned to the proposer.",
    "chap.handoff.accept":
        "Accept a proposed handoff. The accepted tasks are reassigned to the accepting participant, which is the point at which responsibility actually moves. Omitting accepted_task_ids accepts all of them. Accepting resolves the proposal for good: a later accept or decline on the same handoff answers -32051.",
    "chap.handoff.decline":
        "Decline a proposed handoff. The tasks stay with the proposer and nothing is reassigned, which is the difference from chap.handoff.accept. Declining resolves the proposal for good: a later accept or decline on the same handoff answers -32051, and trying again means a fresh proposal. The reason is recorded so the proposer can route the work elsewhere.",
    "chap.control.pause":
        "Pause work. Scoped to a task it moves that task to paused; to a participant it stops new tasks being assigned to them; to the workspace it refuses every method except describing, reading the audit log, joining, leaving and resuming.",
    "chap.control.resume":
        "Resume work paused at the same scope: a task returns to in_progress, a participant can be assigned tasks again, a workspace returns to active.",
    "chap.control.cancel":
        "Cancel a task. Cancelled is terminal, and a task that has already settled cannot be cancelled.",
    "chap.control.snapshot":
        "Capture the workspace state as an artefact before a change you may want to undo. Returns a snapshot artefact id, which is the only thing chap.control.rollback accepts as a target; the label is for a human reading the log and cannot be used to select it. Taking a snapshot changes nothing. Note that a rollback restores only the mode ceiling and member roles, so a snapshot is a narrow safety net rather than a general undo.",
    "chap.control.rollback":
        "Restore workspace state from a snapshot. The mode ceiling and member roles are restored; the rollback is appended to the audit log rather than rewriting it.",
    "chap.control.supersede":
        "Replace a task with a successor in one call. The original moves to superseded and stays linked to its replacement.",
    "chap.control.set_mode_ceiling":
        "Set the highest operating mode tasks in this workspace may request. A task above the ceiling is refused.",
    "chap.task.route":
        "Choose an assignee for a task from a list of candidates and reassign the task to the one selected. Use it where the choice itself belongs on the record: it writes a route_decision artefact naming the policy, the candidate chosen and the alternatives passed over, none of which is captured by setting assignee directly on chap.task.create. The default policy takes the first candidate that is a workspace member; an operator-supplied policy may choose on any basis.",
    "chap.review.depth":
        "Decide how much review a task warrants, skip, spot_check or full, from its routing hints. Records a route_decision artefact giving the rule that produced the answer.",
    "chap.escalate.auto":
        "Evaluate a task's routing hints against the escalation policy and report whether it should be escalated, and to whom. It decides only: a route_decision artefact is recorded but the task does not move, so act on the answer with chap.escalate.raise. The default policy escalates on criticality critical, or high with confidence below 0.6.",
    "chap.participant.rotate_key":
        "Retire a participant's signing key and register its replacement. The old key stays in the key history with a valid_until timestamp, so envelopes it signed still verify.",
    "chap.participant.revoke_key":
        "Revoke a signing key, for example after a device is lost. Signatures presented with it are refused from then on. Revoking another participant's key requires the admin role.",
    "chap.audit.submit_to_scitt":
        "Build COSE_Sign1-shaped statements for a range of audit entries and submit them to the configured SCITT transparency service. Where none is configured the statements are returned for submission out of band.",
    "chap.audit.verify_receipt":
        "Check a receipt returned by an external SCITT transparency service, confirming that service logged the statement. This is the external half of verification; chap.audit.verify_chain checks the local prev-hash chain instead, and the two answer different questions. Verification is delegated to a hook the deployment supplies and fails closed with -32082 when none is configured, so a missing verifier can never read as a pass. Reads only: nothing is recorded.",
    "chap.audit.verify_chain":
        "Replay a workspace's prev-hash chain. Only status verified with ok true means the log was checked and is intact. Status not_evaluated with ok false means part of the log was never checked, so its integrity is unknown and must not be reported as verified; entries_unchecked says how much. An error means the chain is broken or absent.",
}

#: Behavioural hints emitted alongside each tool. See tools.ts for how each
#: field is derived; readOnlyHint in particular is the coordinator's own
#: READ_ONLY_METHODS set and is held to it by a test.
TOOL_ANNOTATIONS: dict[str, dict[str, Any]] = {
    "chap.workspace.create": {
        "title": "Create workspace",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chap.workspace.describe": {
        "title": "Describe workspace",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "chap.workspace.set_profiles": {
        "title": "Replace workspace profiles",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chap.participant.join": {
        "title": "Add participant",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "chap.participant.leave": {
        "title": "Remove participant",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "chap.task.create": {
        "title": "Create task",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chap.task.update": {
        "title": "Change task state",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chap.task.complete": {
        "title": "Submit task output",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chap.audit.read": {
        "title": "Read audit log",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "chap.review.request": {
        "title": "Request review",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chap.decide.approve": {
        "title": "Approve",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chap.decide.reject": {
        "title": "Reject",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chap.decide.override": {
        "title": "Override with a correction",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chap.abstain.declare": {
        "title": "Abstain from review",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chap.escalate.raise": {
        "title": "Escalate task",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chap.whisper.ask": {
        "title": "Ask a whisper",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chap.whisper.answer": {
        "title": "Answer a whisper",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chap.deliberate.open": {
        "title": "Open deliberation",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chap.deliberate.comment": {
        "title": "Comment on deliberation",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chap.deliberate.vote": {
        "title": "Vote in deliberation",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chap.deliberate.close": {
        "title": "Close deliberation",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "chap.handoff.propose": {
        "title": "Propose handoff",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chap.handoff.accept": {
        "title": "Accept handoff",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chap.handoff.decline": {
        "title": "Decline handoff",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chap.control.pause": {
        "title": "Pause work",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "chap.control.resume": {
        "title": "Resume work",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chap.control.cancel": {
        "title": "Cancel task",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chap.control.snapshot": {
        "title": "Snapshot workspace",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chap.control.rollback": {
        "title": "Roll back to snapshot",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chap.control.supersede": {
        "title": "Supersede task",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chap.control.set_mode_ceiling": {
        "title": "Set mode ceiling",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "chap.task.route": {
        "title": "Route task to an assignee",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chap.review.depth": {
        "title": "Decide review depth",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chap.escalate.auto": {
        "title": "Evaluate auto-escalation",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chap.participant.rotate_key": {
        "title": "Rotate signing key",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chap.participant.revoke_key": {
        "title": "Revoke signing key",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "chap.audit.submit_to_scitt": {
        "title": "Anchor audit log in SCITT",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
    "chap.audit.verify_receipt": {
        "title": "Verify SCITT receipt",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
    "chap.audit.verify_chain": {
        "title": "Verify audit chain",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
}
# --- END GENERATED DESCRIPTIONS ---
