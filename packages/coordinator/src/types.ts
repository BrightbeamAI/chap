/**
 * @chap/coordinator — type vocabulary
 *
 * These types are the shared shape of CHAP messages and entities,
 * used both by the Coordinator and by any application building on
 * top of it.
 */

export type ParticipantUri = string;
export type WorkspaceId    = string;
export type TaskId         = string;
export type ArtefactId     = string;

export type ParticipantType = "human" | "agent" | "service" | "group" | "workspace";

export interface Envelope {
  jsonrpc: "2.0";
  id?: string | number;
  method?: string;
  params?: Record<string, unknown>;
  result?: unknown;
  error?:  { code: number; message: string; data?: unknown };
}

export interface Member {
  uri:    ParticipantUri;
  type:   ParticipantType;
  role:   string;
  joined: string;
  display_name?: string;
  capabilities?: Record<string, unknown>;
}

export type TaskState =
  | "created"
  | "in_progress"
  | "review_requested"
  | "completed"
  | "declined"
  | "abstained"
  | "escalated"
  | "superseded";

export interface JsonPatchOp {
  op: "add" | "replace" | "remove" | "copy" | "move" | "test";
  path: string;
  value?: unknown;
  from?: string;
}

/**
 * Routing hints (signals only — see profiles/routing.md for the
 * profile that defines what to *do* with them). Carried on Task
 * inputs and on the artefacts produced for a task.
 */
export interface TaskRoutingHints {
  criticality?: "low" | "medium" | "high" | "critical";
  deadline?:    string;
  max_cost_usd?: number;
  risk_tier?:   string;
  // operator-defined fields allowed
  [extra: string]: unknown;
}

export interface ArtefactRoutingHints {
  confidence?:        number;   // [0, 1], model-specific calibration
  model_id?:          string;
  cost_consumed_usd?: number;
  latency_ms?:        number;
  [extra: string]:    unknown;
}

export interface OverrideArtefact {
  id:                ArtefactId;
  task_id:           TaskId;
  reviewer:          ParticipantUri;
  based_on_artefact: unknown;       // The draft being overridden (snapshot)
  diff:              JsonPatchOp[]; // RFC 6902
  result:            unknown;       // The diff applied to the draft
  rationale:         string;
  tags:              string[];
  policy_refs:       string[];
  ts:                string;
  // CHAP 0.2.1 — optional artefact-identity fields (SPEC §9.2.1, §9.4).
  // logical_id: durable handle for the thing the artefact is about,
  //             shared across revisions/overrides/supersessions.
  // instance_id: stable handle for this specific version; when present,
  //              must equal content_hash or be derived from it.
  // intent_preserved: when based_on carries a logical_id, true means
  //                   the override refines the same underlying decision;
  //                   false means a different decision substituted.
  logical_id?:       string;
  instance_id?:      string;
  intent_preserved?: boolean;
}

export interface RouteDecisionArtefact {
  id:            ArtefactId;
  task_id:       TaskId;
  decision_type: "task.route" | "review.depth" | "escalate.auto";
  outcome:       unknown;
  policy_id:     string;
  hints_observed: Record<string, unknown>;
  rationale:     string;
  ts:            string;
}

export interface ReviewDecision {
  reviewer: ParticipantUri;
  kind:     "approve" | "reject" | "override" | "abstain";
  ts:       string;
  comment?: string;
  tags?:    string[];
  override_artefact_id?: ArtefactId;
  abstain_category?: string;
}

export interface Task {
  id:           TaskId;
  kind:         string;
  state:        TaskState;
  assignee:     ParticipantUri;
  delegator:    ParticipantUri;
  input:        Record<string, unknown>;
  output?:      unknown;
  confidence?:  number;
  deadline?:    string;
  created_at:   string;
  updated_at:   string;
  routing_hints?: TaskRoutingHints;
  artefact_routing_hints?: ArtefactRoutingHints;
  review?: {
    requested_at:  string;
    requested_to:  ParticipantUri[];
    rule:          string;
    deadline?:     string;
    decisions:     ReviewDecision[];
  };
  history:      { ts: string; from: ParticipantUri; state: TaskState; note?: string }[];
  supersedes?:  TaskId;
  route_decisions?: RouteDecisionArtefact[];
}

export interface AuditEntry {
  seq:      number;
  arrived:  string;
  envelope: Envelope;
}

export interface Workspace {
  id:        WorkspaceId;
  created:   string;
  state:     "active" | "paused" | "closed";
  members:   Map<ParticipantUri, Member>;
  tasks:     Map<TaskId, Task>;
  overrides: Map<ArtefactId, OverrideArtefact>;
  route_decisions: Map<ArtefactId, RouteDecisionArtefact>;
  audit:     AuditEntry[];
  profiles:  string[];
}

/**
 * Listener callback invoked synchronously when an envelope is appended
 * to the workspace audit log. Use this to fan out notifications,
 * trigger downstream agents, or stream events to SSE clients.
 */
export type AuditListener = (workspace: Workspace, entry: AuditEntry) => void;
