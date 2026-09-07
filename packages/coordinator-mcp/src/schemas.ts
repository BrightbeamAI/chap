/**
 * @brightbeamai/chap-coordinator-mcp/schemas
 *
 * JSON Schema definitions for each CHAP method, used as the
 * inputSchema for the corresponding MCP tool. Each schema describes
 * the params object that the CHAP envelope would carry; the tool
 * handler wraps the params in a JSON-RPC 2.0 envelope before dispatch.
 *
 * These schemas are tuned for MCP UX: the descriptions are what an
 * LLM client sees when deciding whether to call the tool, so they're
 * written for that audience as well as for validation.
 *
 * Aligned with CHAP 0.2 (see profiles/*.md) and MCP 2025-11-25.
 */

export interface JsonSchema {
  type?: string;
  description?: string;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  enum?: string[];
  items?: JsonSchema;
  additionalProperties?: boolean | JsonSchema;
  oneOf?: JsonSchema[];
  default?: unknown;
  /** JSON Schema `pattern`, used where a string has a fixed wire shape. */
  pattern?: string;
}

// Common reusable fragments.
const PARTICIPANT_URI: JsonSchema = {
  type: "string",
  description: "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
};

const WORKSPACE_ID: JsonSchema = {
  type: "string",
  description: "Workspace identifier, e.g. 'wsp_techcorp_support'.",
};

const TASK_ID: JsonSchema = {
  type: "string",
  description: "Task identifier returned by chap.task.create.",
};

const ROUTING_HINTS: JsonSchema = {
  type: "object",
  description: "Optional signals the routing/1.0 profile uses to pick an assignee and a review depth. Ignored when that profile is not loaded.",
  properties: {
    criticality: {
      type: "string",
      enum: ["low", "medium", "high", "critical"],
      description: "How much a wrong answer costs. Higher criticality pushes towards a more capable assignee and a fuller review.",
    },
    deadline:    { type: "string", description: "When the work is needed by, as an ISO 8601 timestamp." },
    risk_tier:   { type: "string", description: "Operator-defined risk band, e.g. 'regulated' or 'internal'. Interpreted by your routing policy." },
    max_cost_usd: { type: "number", description: "Budget ceiling for this task in US dollars, for policies that price candidates." },
  },
  additionalProperties: true,
};

/** Free-text justification attached to a decision and kept in the audit log. */
const COMMENT: JsonSchema = {
  type: "string",
  description: "The reviewer's note on this decision, recorded in the audit log alongside it.",
};

/** Workspace-defined labels, the raw material of the override learning report. */
const TAGS: JsonSchema = {
  type: "array",
  items: { type: "string" },
  description: "Workspace-defined labels for this decision, e.g. ['tone', 'unsupported-claim']. Querying the audit log by tag is how recurring correction patterns are found.",
};

// ============================================================
//   Core
// ============================================================

export const SCHEMAS: Record<string, JsonSchema> = {
  "chap.workspace.create": {
    type: "object",
    properties: {
      workspace: { ...WORKSPACE_ID, description: "Workspace id to create. If omitted, one is generated." },
      profiles: {
        type: "array",
        items: { type: "string" },
        description: "Profile identifiers to enable, e.g. ['core/1.0', 'review/1.0'].",
      },
      mode: {
        type: "string",
        enum: ["shadow", "trial", "production"],
        default: "trial",
        description: "Starting mode for tasks here, under modes/1.0: shadow runs without delivering output, trial delivers under mandatory review, production delivers per policy. Inert unless that profile is loaded.",
      },
      mode_ceiling: {
        type: "string",
        enum: ["shadow", "trial", "production"],
        default: "production",
        description: "Highest mode any task in this workspace may use. Raising it later needs elevated privilege, so it is a safety bound rather than a default.",
      },
    },
    additionalProperties: false,
  },

  "chap.workspace.describe": {
    type: "object",
    properties: { workspace: WORKSPACE_ID },
    required: ["workspace"],
  },

  "chap.workspace.set_profiles": {
    type: "object",
    properties: {
      workspace: WORKSPACE_ID,
      profiles: {
        type: "array",
        items: { type: "string" },
        description: "The complete profile set to enable, replacing the current one. Adding audit-scitt/1.0 to a workspace with existing entries leaves those entries unchained and outside chain verification.",
      },
    },
    required: ["workspace", "profiles"],
  },

  "chap.participant.join": {
    type: "object",
    properties: {
      workspace: WORKSPACE_ID,
      from:      PARTICIPANT_URI,
      type: {
        type: "string",
        enum: ["human", "agent", "service", "group", "workspace"],
        description: "What kind of participant this is. It is load-bearing: only 'human' members are eligible for the review a required task opens on completion.",
      },
      role:      { type: "string", description: "Operator-defined role, e.g. 'reviewer' or 'drafter'." },
      display_name: { type: "string", description: "Human-readable name shown in interfaces. Does not affect authorisation." },
    },
    required: ["workspace", "from", "type"],
  },

  "chap.participant.leave": {
    type: "object",
    properties: { workspace: WORKSPACE_ID, from: PARTICIPANT_URI },
    required: ["workspace", "from"],
  },

  "chap.task.create": {
    type: "object",
    properties: {
      workspace: WORKSPACE_ID,
      from:      { ...PARTICIPANT_URI, description: "Delegator URI." },
      kind:      { type: "string", description: "Task kind, e.g. 'draft_response' or 'review'." },
      assignee:  { ...PARTICIPANT_URI, description: "Who the task is assigned to. Must be a workspace member." },
      input:     { type: "object", description: "Task-specific input payload.", additionalProperties: true },
      routing_hints: ROUTING_HINTS,
      mode: {
        type: "string",
        enum: ["shadow", "trial", "production"],
        description: "Mode for this task under modes/1.0, defaulting to the workspace mode. Refused if it exceeds the workspace's mode_ceiling.",
      },
      review_required: {
        type: "boolean",
        description: "When true, chap.task.complete opens a review instead of completing: the output becomes the artefact under review and only a reviewer decision finishes the task.",
      },
      deadline: { type: "string", description: "When the task is due, as an ISO 8601 timestamp." },
      idempotency_key: {
        type: "string",
        description: "Caller-chosen key for safe retries. A repeat carrying a key already seen in this workspace returns the original task rather than creating a second one.",
      },
    },
    required: ["workspace", "from", "kind", "assignee", "input"],
  },

  "chap.task.update": {
    type: "object",
    properties: {
      workspace: WORKSPACE_ID,
      from:      PARTICIPANT_URI,
      task_id:   TASK_ID,
      state: {
        type: "string",
        enum: ["in_progress", "review_requested", "declined", "paused", "cancelled", "completed"],
        description: "The state to move the task to. Only the transitions in SPECIFICATION.md 8.1 are legal from the task's current state; anything else is refused with -32602.",
      },
      progress_note: { type: "string", description: "Short note on what changed, kept in the task's history." },
    },
    required: ["workspace", "from", "task_id", "state"],
  },

  "chap.task.complete": {
    type: "object",
    properties: {
      workspace: WORKSPACE_ID,
      from:      PARTICIPANT_URI,
      task_id:   TASK_ID,
      output:    { description: "The task's output artefact. Pass as a JSON object or array, not a JSON-encoded string." },
      confidence: { type: "number", description: "Self-reported confidence (0-1), where supported." },
      routing_hints: ROUTING_HINTS,
    },
    required: ["workspace", "from", "task_id"],
  },

  "chap.audit.read": {
    type: "object",
    properties: {
      workspace: WORKSPACE_ID,
      range: {
        type: "object",
        description: "Sequence window to return. Omit for the whole log.",
        properties: {
          from_seq: { type: "integer", description: "Start sequence number (inclusive)." },
          to_seq:   { type: "integer", description: "End sequence number (exclusive)." },
        },
      },
      filter: {
        type: "object",
        description: "Narrow the entries returned. Filters combine with AND.",
        properties: {
          method:  { type: "string", description: "Return only entries for this CHAP method, e.g. 'decide.override'." },
          from:    { type: "string", description: "Return only entries whose actor is this participant URI." },
          task_id: { type: "string", description: "Return only entries about this task." },
        },
      },
    },
    required: ["workspace"],
  },

  // ============================================================
  //   review/1.0
  // ============================================================

  "chap.review.request": {
    type: "object",
    properties: {
      workspace: WORKSPACE_ID,
      from:      PARTICIPANT_URI,
      task_id:   TASK_ID,
      to:        {
        oneOf: [PARTICIPANT_URI, { type: "array", items: PARTICIPANT_URI }],
        description: "One or more reviewers. A single URI string, or a JSON array of URI strings (not a JSON-encoded string).",
      },
      rule: {
        type: "string",
        enum: ["any_one_approves", "all_approve", "quorum:2", "quorum:3"],
        default: "any_one_approves",
        description: "How many of the addressed reviewers must approve before the task completes. Fixed for the life of the review: a later request that changes it is refused with -32014.",
      },
      artefact:  { description: "The draft being submitted for review. Pass as a JSON object or array, not a JSON-encoded string." },
      deadline:  { type: "string", description: "When the review is needed by, as an ISO 8601 timestamp." },
    },
    required: ["workspace", "from", "task_id", "to", "artefact"],
  },

  "chap.decide.approve": {
    type: "object",
    properties: {
      workspace: WORKSPACE_ID, from: PARTICIPANT_URI, task_id: TASK_ID,
      approved_artefact_digest: {
        type: "string",
        pattern: "^sha256:[0-9a-f]{64}$",
        description: "Optional. SHA-256 over the JCS canonicalisation of the artefact under review, as `sha256:<hex>`. Binds the decision to the exact content reviewed; refused with -32074 on mismatch.",
      },
      comment: COMMENT,
      tags:    TAGS,
    },
    required: ["workspace", "from", "task_id"],
  },

  "chap.decide.reject": {
    type: "object",
    properties: {
      workspace: WORKSPACE_ID, from: PARTICIPANT_URI, task_id: TASK_ID,
      approved_artefact_digest: {
        type: "string",
        pattern: "^sha256:[0-9a-f]{64}$",
        description: "Optional. SHA-256 over the JCS canonicalisation of the artefact under review, as `sha256:<hex>`. Binds the decision to the exact content reviewed; refused with -32074 on mismatch.",
      },
      comment: COMMENT,
      tags:    TAGS,
      request_revision: { type: "boolean", description: "If true, task returns to in_progress instead of declined." },
    },
    required: ["workspace", "from", "task_id"],
  },

  "chap.decide.override": {
    type: "object",
    properties: {
      approved_artefact_digest: {
        type: "string",
        pattern: "^sha256:[0-9a-f]{64}$",
        description: "Optional. SHA-256 over the JCS canonicalisation of the artefact under review, as `sha256:<hex>`. Binds the decision to the exact content reviewed; refused with -32074 on mismatch.",
      },
      workspace: WORKSPACE_ID, from: PARTICIPANT_URI, task_id: TASK_ID,
      diff: {
        type: "array",
        description: "RFC 6902 JSON Patch operations applied to the artefact under review.",
        items: {
          type: "object",
          properties: {
            op:    { type: "string", enum: ["add", "replace", "remove", "copy", "move", "test"], description: "The patch operation to apply." },
            path:  { type: "string", description: "JSON Pointer into the artefact, e.g. '/body' or '/items/0/severity'." },
            value: { description: "The new value, for add, replace and test." },
            from:  { type: "string", description: "Source JSON Pointer, for copy and move." },
          },
          required: ["op", "path"],
        },
      },
      rationale: { type: "string", description: "Why the override was applied. Required for the structured-override audit trail." },
      tags:      TAGS,
      policy_refs: {
        type: "array",
        items: { type: "string" },
        description: "Identifiers of the policies or guidelines this correction applies, e.g. ['policy:no-delivery-promises']. Lets an auditor trace a decision back to the rule behind it.",
      },
      logical_id: {
        type: "string",
        description: "Durable handle for the thing being decided, shared across revisions and overrides of the same underlying artefact.",
      },
      intent_preserved: {
        type: "boolean",
        description: "True when the edit refines the same decision the draft was making, false when it substitutes a different one. Separates wording fixes from reversals in the override report.",
      },
    },
    required: ["workspace", "from", "task_id", "diff", "rationale"],
  },

  "chap.abstain.declare": {
    type: "object",
    properties: {
      workspace: WORKSPACE_ID, from: PARTICIPANT_URI, task_id: TASK_ID,
      reason:   { type: "string", description: "Why this reviewer is standing aside, recorded in the audit log." },
      category: {
        type: "string",
        enum: ["conflict_of_interest", "insufficient_context", "out_of_scope", "other"],
        description: "The kind of abstention, so recurring gaps in reviewer coverage can be counted rather than read.",
      },
    },
    required: ["workspace", "from", "task_id", "reason"],
  },

  "chap.escalate.raise": {
    type: "object",
    properties: {
      workspace: WORKSPACE_ID, from: PARTICIPANT_URI,
      original_task_id: TASK_ID,
      new_task: {
        type: "object",
        description: "The successor task to open for the escalation target. The original is marked escalated and linked to it.",
        properties: {
          kind:     { type: "string", description: "Task kind for the successor, defaulting to the original's kind." },
          assignee: { ...PARTICIPANT_URI, description: "Who the escalation goes to. Must be a workspace member." },
          input:    { type: "object", additionalProperties: true, description: "Input payload for the successor, typically the original input plus the context that prompted the escalation." },
        },
        required: ["assignee"],
      },
    },
    required: ["workspace", "from", "original_task_id", "new_task"],
  },

  // ============================================================
  //   whisper/1.0
  // ============================================================

  "chap.whisper.ask": {
    type: "object",
    properties: {
      workspace: WORKSPACE_ID, from: PARTICIPANT_URI, task_id: TASK_ID,
      to: { type: "array", items: PARTICIPANT_URI, description: "Who is being asked." },
      question: { type: "string", description: "The single question to put to them, short enough to answer without opening the task." },
      options: {
        type: "array",
        items: {
          type: "object",
          properties: {
            id:    { type: "string", description: "Stable identifier the answer refers to." },
            label: { type: "string", description: "What the option says to the person answering." },
          },
          required: ["id"],
        },
        description: "Optional multiple-choice options. If present, answer_option must be one of these ids.",
      },
      deadline_ms: { type: "integer", description: "Time-to-live in milliseconds from now." },
      default_if_lapsed: { description: "Value applied if the whisper lapses without an answer." },
      urgency: {
        type: "string",
        enum: ["low", "medium", "high"],
        default: "low",
        description: "How hard to push for an answer before the deadline. Advisory: it does not change the lapse behaviour.",
      },
    },
    required: ["workspace", "from", "task_id", "to", "question", "deadline_ms", "default_if_lapsed"],
  },

  "chap.whisper.answer": {
    type: "object",
    properties: {
      workspace: WORKSPACE_ID, from: PARTICIPANT_URI,
      whisper_id: { type: "string", description: "Identifier returned by chap.whisper.ask." },
      answer_option: { type: "string", description: "Option id (required when the whisper has options)." },
      answer:        { type: "string", description: "Free-text answer (when no options)." },
      comment:       { type: "string", description: "Anything the answerer wants on the record beyond the answer itself." },
    },
    required: ["workspace", "from", "whisper_id"],
  },

  // ============================================================
  //   deliberation/1.0
  // ============================================================

  "chap.deliberate.open": {
    type: "object",
    properties: {
      workspace: WORKSPACE_ID, from: PARTICIPANT_URI,
      to: { type: "array", items: PARTICIPANT_URI, description: "Deliberation participants." },
      task_id: TASK_ID,
      rule: {
        type: "string",
        description: "Voting rule. Examples: any_one_approves, all_approve, quorum:N, weighted_vote:T, weighted_vote_with_veto:T.",
      },
      question: { type: "string", description: "What the group is deciding, stated so a yea or nay is unambiguous." },
      weights:  { type: "object", additionalProperties: { type: "number" }, description: "Voter -> weight map (for weighted rules)." },
      veto:     { type: "object", additionalProperties: { type: "boolean" }, description: "Voter -> can-veto map." },
      deadline: { type: "string", description: "When voting closes, as an ISO 8601 timestamp." },
    },
    required: ["workspace", "from", "to", "rule"],
  },

  "chap.deliberate.comment": {
    type: "object",
    properties: {
      workspace: WORKSPACE_ID, from: PARTICIPANT_URI,
      deliberation_id: { type: "string", description: "Identifier returned by chap.deliberate.open." },
      comment: { type: "string", description: "The contribution to record. Comments are part of the audit trail, so the reasoning survives the vote." },
    },
    required: ["workspace", "from", "deliberation_id", "comment"],
  },

  "chap.deliberate.vote": {
    type: "object",
    properties: {
      workspace: WORKSPACE_ID, from: PARTICIPANT_URI,
      deliberation_id: { type: "string", description: "Identifier returned by chap.deliberate.open." },
      vote: { type: "string", enum: ["yea", "nay", "abstain"], description: "This voter's position. Abstain is recorded and does not count towards the rule." },
      weight: { type: "number", description: "Weight to apply, for weighted rules. Defaults to the weight set when the deliberation was opened." },
      comment: { type: "string", description: "Why the vote went this way, recorded with it." },
      veto_invoked: { type: "boolean", description: "Set by a voter with veto rights to block the outcome regardless of the tally. Only honoured under a veto rule." },
    },
    required: ["workspace", "from", "deliberation_id", "vote"],
  },

  "chap.deliberate.close": {
    type: "object",
    properties: {
      workspace: WORKSPACE_ID, from: PARTICIPANT_URI,
      deliberation_id: { type: "string", description: "Identifier returned by chap.deliberate.open." },
    },
    required: ["workspace", "from", "deliberation_id"],
  },

  // ============================================================
  //   handoff/1.0
  // ============================================================

  "chap.handoff.propose": {
    type: "object",
    properties: {
      workspace: WORKSPACE_ID, from: PARTICIPANT_URI,
      to: { type: "string", description: "Recipient (URI or 'group:...')." },
      tasks: {
        type: "array",
        description: "The work being handed over, one entry per task, each carrying the context the recipient needs to pick it up cold.",
        items: {
          type: "object",
          properties: {
            task_id: TASK_ID,
            title:   { type: "string", description: "Short label for the task, so the recipient can scan the list." },
            status_summary: { type: "string", description: "Where the work has got to and anything already tried." },
            next_action:    { type: "string", description: "The one thing the recipient should do first." },
            blockers:       { type: "array", items: { type: "string" }, description: "What is stopping progress, if anything." },
          },
          required: ["task_id"],
        },
      },
      summary: { type: "string", description: "Covering note for the handover as a whole, above the per-task detail." },
      context_links: { type: "array", items: { type: "string" }, description: "URLs to threads, tickets or documents the recipient will need." },
    },
    required: ["workspace", "from", "to", "tasks"],
  },

  "chap.handoff.accept": {
    type: "object",
    properties: {
      workspace: WORKSPACE_ID, from: PARTICIPANT_URI,
      handoff_id: { type: "string", description: "Identifier returned by chap.handoff.propose." },
      accepted_task_ids: { type: "array", items: TASK_ID, description: "If omitted, all proposed tasks are accepted." },
      comment: { type: "string", description: "Anything the recipient wants on the record when taking the work on." },
    },
    required: ["workspace", "from", "handoff_id"],
  },

  "chap.handoff.decline": {
    type: "object",
    properties: {
      workspace: WORKSPACE_ID, from: PARTICIPANT_URI,
      handoff_id: { type: "string", description: "Identifier returned by chap.handoff.propose." },
      reason: { type: "string", description: "Why the handover is being refused, recorded so the proposer can route it elsewhere." },
      suggested_target: { ...PARTICIPANT_URI, description: "Who should take it instead, if the decliner knows." },
    },
    required: ["workspace", "from", "handoff_id"],
  },

  // ============================================================
  //   control/1.0
  // ============================================================

  "chap.control.pause": {
    type: "object",
    properties: {
      workspace: WORKSPACE_ID, from: PARTICIPANT_URI,
      scope: {
        type: "string",
        enum: ["task", "participant", "workspace"],
        default: "task",
        description: "What the control applies to: one task, everything a participant is doing, or the whole workspace.",
      },
      task_id:         TASK_ID,
      participant_uri: { ...PARTICIPANT_URI, description: "Whose work to pause, when scope is 'participant'." },
      in_flight_policy: {
        type: "string",
        enum: ["allow_to_complete", "interrupt"],
        description: "What happens to work already under way: let it finish, or stop it where it stands.",
      },
      reason: { type: "string", description: "Why the pause was applied, recorded in the audit log." },
    },
    required: ["workspace", "from"],
  },

  "chap.control.resume": {
    type: "object",
    properties: {
      workspace: WORKSPACE_ID, from: PARTICIPANT_URI,
      scope: {
        type: "string",
        enum: ["task", "participant", "workspace"],
        default: "task",
        description: "What the control applies to: one task, everything a participant is doing, or the whole workspace.",
      },
      task_id: TASK_ID,
      participant_uri: { ...PARTICIPANT_URI, description: "Whose work to resume, when scope is 'participant'." },
    },
    required: ["workspace", "from"],
  },

  "chap.control.cancel": {
    type: "object",
    properties: {
      workspace: WORKSPACE_ID, from: PARTICIPANT_URI,
      task_id: TASK_ID,
      reason:  { type: "string", description: "Why the task was cancelled, recorded in the audit log." },
    },
    required: ["workspace", "from", "task_id"],
  },

  "chap.control.snapshot": {
    type: "object",
    properties: {
      workspace: WORKSPACE_ID, from: PARTICIPANT_URI,
      label:   { type: "string", description: "Name for this snapshot, so a later rollback can be described in words rather than an id." },
      include: { type: "array", items: { type: "string" }, description: "Aspects to snapshot, e.g. ['members', 'open_tasks', 'mode_ceiling']." },
    },
    required: ["workspace", "from"],
  },

  "chap.control.rollback": {
    type: "object",
    properties: {
      workspace: WORKSPACE_ID, from: PARTICIPANT_URI,
      to_snapshot_artefact_id: { type: "string", description: "Artefact id returned by chap.control.snapshot, naming the state to restore." },
      what_to_restore: { type: "array", items: { type: "string" }, description: "Which aspects of the snapshot to apply, e.g. ['members', 'mode_ceiling']. Omit to restore everything it captured." },
      reason: { type: "string", description: "Why the rollback was performed. The rollback is itself an audit entry; the state it restores is not rewritten." },
    },
    required: ["workspace", "from", "to_snapshot_artefact_id"],
  },

  "chap.control.supersede": {
    type: "object",
    properties: {
      workspace: WORKSPACE_ID, from: PARTICIPANT_URI,
      task_id: TASK_ID,
      successor_task: {
        type: "object",
        description: "The replacement task. The superseded one stays in the chain, linked to this successor, rather than being deleted.",
        properties: {
          kind:     { type: "string", description: "Task kind for the successor." },
          assignee: { ...PARTICIPANT_URI, description: "Who takes the replacement task on. Defaults to the superseded task's assignee." },
          input:    { type: "object", additionalProperties: true, description: "Input payload for the successor." },
        },
        required: ["kind"],
      },
      reason: { type: "string", description: "Why the original is being replaced, recorded in the audit log." },
    },
    required: ["workspace", "from", "task_id", "successor_task"],
  },

  "chap.control.set_mode_ceiling": {
    type: "object",
    properties: {
      workspace: WORKSPACE_ID, from: PARTICIPANT_URI,
      new_ceiling: {
        type: "string",
        enum: ["shadow", "trial", "production"],
        description: "The highest mode tasks in this workspace may use from now on. Raising it is a privileged operation and may require step-up authentication.",
      },
      reason:      { type: "string", description: "Why the ceiling is being changed, recorded in the audit log." },
    },
    required: ["workspace", "from", "new_ceiling"],
  },

  // ============================================================
  //   routing/1.0
  // ============================================================

  "chap.task.route": {
    type: "object",
    properties: {
      workspace: WORKSPACE_ID, from: PARTICIPANT_URI,
      task_id: TASK_ID,
      candidates: { type: "array", items: PARTICIPANT_URI, description: "Candidate assignees." },
    },
    required: ["workspace", "from", "task_id", "candidates"],
  },

  "chap.review.depth": {
    type: "object",
    properties: {
      workspace: WORKSPACE_ID, from: PARTICIPANT_URI,
      task_id: TASK_ID,
      artefact_routing_hints: {
        type: "object",
        description: "Per-artefact signals like confidence, model_id, cost_consumed_usd.",
        additionalProperties: true,
      },
    },
    required: ["workspace", "from", "task_id"],
  },

  "chap.escalate.auto": {
    type: "object",
    properties: {
      workspace: WORKSPACE_ID, from: PARTICIPANT_URI,
      task_id: TASK_ID,
      default_escalation_target: PARTICIPANT_URI,
    },
    required: ["workspace", "from", "task_id"],
  },

  // ============================================================
  //   security-signed/1.0
  // ============================================================

  "chap.participant.rotate_key": {
    type: "object",
    properties: {
      workspace: WORKSPACE_ID, from: PARTICIPANT_URI,
      old_kid: { type: "string", description: "Key id being retired. It stays in the key history so past signatures still verify." },
      new_jwk: { type: "object", additionalProperties: true, description: "The replacement public key as a JWK. The request must be signed with the old key, which is what proves the rotation is genuine." },
    },
    required: ["workspace", "from", "old_kid", "new_jwk"],
  },

  "chap.participant.revoke_key": {
    type: "object",
    properties: {
      workspace: WORKSPACE_ID, from: PARTICIPANT_URI,
      target_uri: { ...PARTICIPANT_URI, description: "Whose key is being revoked." },
      kid: { type: "string", description: "Key id to revoke. Signatures made with it are refused from now on; entries it already signed stay valid." },
      reason: { type: "string", description: "Why the key was revoked, e.g. 'laptop lost'. Recorded in the audit log." },
    },
    required: ["workspace", "from", "target_uri", "kid"],
  },

  // ============================================================
  //   audit-scitt/1.0
  // ============================================================

  "chap.audit.submit_to_scitt": {
    type: "object",
    properties: {
      workspace: WORKSPACE_ID, from: PARTICIPANT_URI,
      range: {
        type: "object",
        description: "Sequence window to anchor. Omit to submit the whole chain.",
        properties: {
          from_seq: { type: "integer", description: "Start sequence number (inclusive)." },
          to_seq:   { type: "integer", description: "End sequence number (exclusive)." },
        },
      },
      issuer: { type: "string", description: "Issuer identifier to put on the SCITT signed statement, identifying who is vouching for the chain." },
    },
    required: ["workspace"],
  },

  "chap.audit.verify_receipt": {
    type: "object",
    properties: {
      workspace: WORKSPACE_ID, from: PARTICIPANT_URI,
      receipt:   { type: "object", additionalProperties: true, description: "The SCITT receipt to check, as returned by the transparency service. Verification fails closed when no verifier is configured." },
    },
    required: ["workspace", "receipt"],
  },

  "chap.audit.verify_chain": {
    type: "object",
    properties: { workspace: WORKSPACE_ID, from: PARTICIPANT_URI },
    required: ["workspace"],
  },
};

/** Methods that map to MCP tools. Currently: all 39 CHAP methods. */
export const TOOL_NAMES = Object.keys(SCHEMAS);

/** Get the JSON Schema for a tool, or null if not a known CHAP method. */
export function schemaFor(toolName: string): JsonSchema | null {
  return SCHEMAS[toolName] ?? null;
}

/** Map an MCP tool name back to its CHAP method name. */
export function methodForTool(toolName: string): string | null {
  if (!toolName.startsWith("chap.")) return null;
  return toolName.slice("chap.".length);
}

// ============================================================
//   Stringified-JSON coercion
// ============================================================
//
// LLM MCP clients (Claude Desktop, Cursor, and others) frequently
// serialise structured tool arguments as JSON-encoded *strings* rather
// than as native JSON objects/arrays. For example, an `artefact` that
// should arrive as { "draft": "..." } arrives as the string
// "{\"draft\": \"...\"}", and a `to` that should be ["human:me@local"]
// arrives as "[\"human:me@local\"]".
//
// The CHAP protocol core is deliberately strict: it stores artefacts
// and applies JSON Patches against whatever it receives. A stringified
// object therefore (a) pollutes the audit log with the wrong type and
// (b) makes object-path patches in decide.override impossible, because
// there is no object to traverse.
//
// We fix this at the adapter boundary, leaving the protocol core
// untouched. Before an argument is wrapped in a CHAP envelope, any
// string value whose schema admits a non-string type is JSON-parsed
// when (and only when) it parses cleanly to a type the schema allows.
// Plain strings that the schema accepts as strings (participant URIs,
// task ids, rationales) are never touched, so a bare "human:me@local"
// passed to a string|array `to` field is preserved as-is.

/** True if the schema admits `object` as a value type. */
function admitsType(schema: JsonSchema | undefined, t: "object" | "array"): boolean {
  if (!schema) return false;
  if (schema.type === t) return true;
  if (schema.oneOf) return schema.oneOf.some((s) => admitsType(s, t));
  // A field declared with neither `type` nor `oneOf` (e.g. `output`,
  // `artefact`) is an opaque payload: it admits any JSON value, so we
  // allow coercion to both object and array for it.
  if (schema.type === undefined && !schema.oneOf && !schema.enum) return true;
  return false;
}

/**
 * Coerce a single argument value against its parameter schema. Returns
 * the parsed value when the string is stringified JSON the schema
 * accepts; otherwise returns the value unchanged.
 */
function coerceValue(value: unknown, schema: JsonSchema | undefined): unknown {
  if (typeof value !== "string") return value;
  const trimmed = value.trim();
  if (trimmed.length === 0) return value;
  const looksLikeObject = trimmed.startsWith("{");
  const looksLikeArray  = trimmed.startsWith("[");
  if (!looksLikeObject && !looksLikeArray) return value;

  const wantObject = looksLikeObject && admitsType(schema, "object");
  const wantArray  = looksLikeArray  && admitsType(schema, "array");
  if (!wantObject && !wantArray) return value;

  try {
    const parsed = JSON.parse(value);
    // Only accept the parse if its type is one the schema admits. This
    // guards against, say, a string that happens to start with "[" but
    // is meant to be a literal string in a string-only field.
    if (Array.isArray(parsed) && admitsType(schema, "array")) return parsed;
    if (parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)
        && admitsType(schema, "object")) return parsed;
    return value;
  } catch {
    // Not valid JSON: leave it for the coordinator to validate/reject.
    return value;
  }
}

/**
 * Normalise the arguments object for a tool call, coercing any
 * stringified-JSON values whose parameter schema admits a structured
 * type. Unknown keys (not in the schema) are passed through untouched.
 *
 * This is a pure function: it returns a new object and does not mutate
 * its input. Coordinator dispatch sees correctly-typed params, so the
 * audit log records the right shapes and decide.override patches apply
 * against real objects.
 */
export function coerceToolArgs(
  toolName: string,
  args: Record<string, unknown>,
): Record<string, unknown> {
  const schema = schemaFor(toolName);
  const props = schema?.properties;
  if (!props) return args;
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(args)) {
    out[key] = coerceValue(value, props[key]);
  }
  return out;
}
