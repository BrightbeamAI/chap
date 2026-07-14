/**
 * @brightbeamai/coordinator. type vocabulary
 *
 * Wire-shape and in-memory types for the Coordinator. The schemas at
 * schemas/ are the normative wire types; these mirror them in
 * TypeScript for in-process use.
 */

export type ParticipantUri = string;
export type WorkspaceId    = string;
export type TaskId         = string;
export type ArtefactId     = string;

export type ParticipantType = "human" | "agent" | "service" | "group" | "workspace";

export interface Envelope {
  jsonrpc: "2.0";
  id?:     string | number;
  method?: string;
  params?: Record<string, unknown>;
  result?: unknown;
  error?:  { code: number; message: string; data?: unknown };
  /** security-signed/1.0: top-level signature, signed over JCS of the
   *  envelope with this field removed. Form: ``ed25519:<kid>:<b64>``. */
  sig?: string;
}

export type Mode = "shadow" | "trial" | "production";
export type WorkspaceState = "active" | "paused" | "closed";
export type PauseScope = "task" | "participant" | "workspace";

export type TaskState =
  | "created"
  | "in_progress"
  | "review_requested"
  | "completed"
  | "declined"
  | "abstained"
  | "escalated"
  | "superseded"
  | "paused"
  | "cancelled";

// ============================================================
//   Key records (security-signed/1.0)
// ============================================================

export interface Jwk {
  kty: "OKP";
  crv: "Ed25519";
  kid: string;
  x:   string;
  use?: string;
  alg?: string;
}

/** A participant's JWK plus the validity window. Lookup by
 *  (participant, kid, ts) so historical envelopes verify across
 *  rotation per security-signed/1.0 S3-S5. */
export interface KeyRecord {
  jwk:         Jwk;
  kid:         string;
  valid_from:  string;            // ISO timestamp; inclusive
  valid_until?: string;           // ISO timestamp; exclusive; absent = unbounded
  revoked_at?: string;
  revoked_reason?: string;
}

// ============================================================
//   Participants
// ============================================================

export interface Member {
  uri:    ParticipantUri;
  type:   ParticipantType;
  role:   string;
  joined: string;
  display_name?: string;
  capabilities?: Record<string, unknown>;
  scopes?:       string[];
  keys:          KeyRecord[];
  paused:        boolean;         // control.pause scope=participant
  oidc_sub?:     string;
  oidc_auth_time?: number;        // unix seconds
  vc_holder?:    string;
}

// ============================================================
//   Routing hints
// ============================================================

export interface TaskRoutingHints {
  criticality?: "low" | "medium" | "high" | "critical";
  deadline?:    string;
  max_cost_usd?: number;
  risk_tier?:   string;
  [extra: string]: unknown;
}

export interface ArtefactRoutingHints {
  confidence?:        number | string;
  model_id?:          string;
  cost_consumed_usd?: number;
  latency_ms?:        number;
  [extra: string]:    unknown;
}

// ============================================================
//   JSON Patch (RFC 6902)
// ============================================================

export interface JsonPatchOp {
  op:    "add" | "replace" | "remove" | "copy" | "move" | "test";
  path:  string;
  value?: unknown;
  from?: string;
}

// ============================================================
//   Override artefact (review/1.0)
// ============================================================

export interface OverrideArtefact {
  id:                ArtefactId;
  task_id:           TaskId;
  reviewer:          ParticipantUri;
  based_on_artefact: unknown;
  diff:              JsonPatchOp[];
  result:            unknown;
  rationale:         string;
  tags:              string[];
  policy_refs:       string[];
  ts:                string;
  logical_id?:       string;
  instance_id?:      string;
  intent_preserved?: boolean;
}

// ============================================================
//   Route decision artefact (routing/1.0)
// ============================================================

export interface RouteDecisionArtefact {
  id:             ArtefactId;
  kind:           "route_decision";
  decision_type:  "task.route" | "review.depth" | "escalate.auto";
  outcome:        unknown;
  produced_by:    ParticipantUri;
  produced_at:    string;
  task?:          TaskId;
  policy_id?:     string;
  hints_observed: Record<string, unknown>;
  rationale:      string;
  // Auxiliary fields per decision_type:
  // task.route:     alternatives_considered
  // review.depth:   sampling_probability
  // escalate.auto:  escalation_target
  [extra: string]: unknown;
}

// ============================================================
//   Snapshot artefact (control/1.0)
// ============================================================

export interface SnapshotArtefact {
  id:           ArtefactId;
  kind:         "snapshot";
  ts:           string;
  by:           ParticipantUri;
  workspace:    WorkspaceId;
  audit_seq:    number;
  label?:       string;
  include:      string[];
  state:        Record<string, unknown>;
}

// ============================================================
//   Whisper (whisper/1.0)
// ============================================================

export interface WhisperPrompt {
  id:                ArtefactId;
  task_id:           TaskId;
  asker:             ParticipantUri;
  askee:             ParticipantUri[];
  question:          string;
  options?:          Array<{ id: string; label?: string }>;
  asked_at:          string;
  deadline_ms:       number;
  default_if_lapsed: unknown;
  urgency:           "low" | "medium" | "high";
  state:             "pending" | "answered" | "lapsed";
  answered_at?:      string;
  answered_by?:      ParticipantUri;
  answer_option?:    string;
  answer_text?:      string;
  comment?:          string;
  default_applied?:  unknown;
}

// ============================================================
//   Deliberation (deliberation/1.0)
// ============================================================

export interface DeliberationVote {
  ts:            string;
  voter:         ParticipantUri;
  vote:          "yea" | "nay" | "abstain";
  weight?:       number;
  comment?:      string;
  veto_invoked?: boolean;
}

export interface Deliberation {
  id:           string;
  task_id?:     TaskId;
  opener:       ParticipantUri;
  participants: ParticipantUri[];
  rule:         string;        // raw rule string
  question?:    string;
  weights?:     Record<ParticipantUri, number>;
  veto?:        Record<ParticipantUri, boolean>;
  deadline?:    string;
  state:        "open" | "closed" | "lapsed";
  comments:     { ts: string; from: ParticipantUri; text: string }[];
  votes:        DeliberationVote[];
  outcome?:     {
    outcome:  "approved" | "rejected" | "error";
    rule:     string;
    tally?:   { yea: number; nay: number };
    vetoes?:  ParticipantUri[];
    reason?:  string;
  };
  opened_at:    string;
}

// ============================================================
//   Handoff (handoff/1.0)
// ============================================================

export interface HandoffTaskItem {
  task_id:         TaskId;
  title?:          string;
  status_summary?: string;
  next_action?:    string;
  blockers?:       string[];
}

export interface Handoff {
  id:           string;
  proposer:     ParticipantUri;
  recipient:    ParticipantUri;     // single URI or "group:..."
  proposed_at:  string;
  tasks:        HandoffTaskItem[];
  summary?:     string;
  context_links: string[];
  state:        "proposed" | "accepted" | "declined";
  resolved_at?: string;
  accepted_by?: ParticipantUri;
  accepted_task_ids?: TaskId[];
  accept_comment?:    string;
  decline_reason?:    string;
  decline_suggested_target?: ParticipantUri;
}

// ============================================================
//   Review decision (review/1.0)
// ============================================================

export interface ReviewDecision {
  reviewer: ParticipantUri;
  kind:     "approve" | "reject" | "override" | "abstain";
  ts:       string;
  comment?: string;
  tags?:    string[];
  override_artefact_id?: ArtefactId;
  abstain_category?:     string;
}

// ============================================================
//   Task
// ============================================================

export interface Task {
  id:           TaskId;
  kind:         string;
  state:        TaskState;
  assignee:     ParticipantUri;
  delegator:    ParticipantUri;
  input:        Record<string, unknown>;
  output?:      unknown;
  confidence?:  number | string;
  deadline?:    string;
  created_at:   string;
  updated_at:   string;
  mode:         Mode;
  review_required?: boolean;
  routing_hints?:           TaskRoutingHints;
  artefact_routing_hints?:  ArtefactRoutingHints;
  review?: {
    requested_at: string;
    requested_to: ParticipantUri[];
    rule:         string;
    deadline?:    string;
    decisions:    ReviewDecision[];
  };
  history:      { ts: string; from: ParticipantUri; state: TaskState; note?: string }[];
  supersedes?:  TaskId;
  superseded_by?: TaskId;
  parent?:      TaskId;
  paused:       boolean;
  /** Transient: holds the artefact passed to review.request so
   *  decide.override can use it as the patch base. */
  pending_artefact?: unknown;
}

// ============================================================
//   Audit
// ============================================================

export interface AuditEntry {
  seq:      number;
  arrived:  string;
  envelope: Envelope;
  prev_hash?: string;
}

// ============================================================
//   Workspace
// ============================================================

export interface Workspace {
  id:           WorkspaceId;
  created:      string;
  state:        WorkspaceState;
  profiles:     string[];
  mode:         Mode;
  mode_ceiling: Mode;
  routing_policy_uri?: string;
  step_up_window_sec: number;
  members:         Map<ParticipantUri, Member>;
  tasks:           Map<TaskId, Task>;
  overrides:       Map<ArtefactId, OverrideArtefact>;
  whispers:        Map<string, WhisperPrompt>;
  deliberations:   Map<string, Deliberation>;
  handoffs:        Map<string, Handoff>;
  snapshots:       Map<ArtefactId, SnapshotArtefact>;
  route_decisions: Map<ArtefactId, RouteDecisionArtefact>;
  audit:           AuditEntry[];
  chain_head?:     string;
  chain_enabled:   boolean;
}

/**
 * Listener callback invoked synchronously when an envelope is appended
 * to the workspace audit log.
 */
export type AuditListener = (workspace: Workspace, entry: AuditEntry) => void;

/** True iff mode `a` is <= ceiling. */
export function modeLE(a: Mode, ceiling: Mode): boolean {
  const order: Record<Mode, number> = { shadow: 0, trial: 1, production: 2 };
  return order[a] <= order[ceiling];
}
