/**
 * Typed method definitions.
 *
 * Every CHAP method that ships in either reference implementation
 * gets a typed `Params` and `Result` shape here. The Coordinator's
 * typed facade (`coord.workspace.create`, `coord.task.complete`, …)
 * uses these types so callers get autocomplete and compile-time
 * checking without having to remember field names.
 *
 * These types are hand-maintained against the canonical JSON Schemas
 * under `schemas/`. A future revision will generate this file from
 * the schemas automatically; for now the source of truth remains
 * the running tests under `tests/`.
 *
 * Scope: 39 method handlers shipped by both v0.2 reference
 * implementations (Core + every shipped profile). Spec-only methods
 * are excluded - they would mislead callers into thinking the local
 * Coordinator can dispatch them.
 */

import type {
  ArtefactId, JsonPatchOp, ParticipantUri, TaskId,
  Workspace, WorkspaceId,
} from "./types.js";

// ---- shared building blocks ---------------------------------------

export interface WorkspaceParam { workspace: WorkspaceId }
export interface ActorParam     { from: ParticipantUri }
export interface TaskParam      { task_id: TaskId }

export interface OkResult { ok: true }

// ============================================================
//   workspace.*
// ============================================================

export interface WorkspaceCreateParams extends Partial<WorkspaceParam> {
  profiles?:           string[];
  routing_policy_uri?: string;
  mode?:               "shadow" | "trial" | "production";
  mode_ceiling?:       "shadow" | "trial" | "production";
}
export interface WorkspaceCreateResult { workspace: WorkspaceId }

export interface WorkspaceDescribeParams extends WorkspaceParam {}
export interface WorkspaceDescribeResult { workspace: Workspace }

export interface WorkspaceSetProfilesParams extends WorkspaceParam, ActorParam {
  profiles: string[];
}
export type WorkspaceSetProfilesResult = OkResult;

// ============================================================
//   participant.*
// ============================================================

export interface ParticipantJoinParams extends WorkspaceParam, ActorParam {
  type: "human" | "agent" | "service" | "group" | "workspace";
  role?:        string;
  scopes?:      string[];
  capabilities?: Record<string, unknown>;
  oidc_token?:  unknown;
  vc?:          unknown;
  pubkey?:      unknown;
}
export type ParticipantJoinResult = OkResult;

export interface ParticipantLeaveParams extends WorkspaceParam, ActorParam {
  reason?: string;
}
export type ParticipantLeaveResult = OkResult;

export interface ParticipantRotateKeyParams extends WorkspaceParam, ActorParam {
  pubkey: unknown;
  reason?: string;
}
export type ParticipantRotateKeyResult = OkResult;

export interface ParticipantRevokeKeyParams extends WorkspaceParam, ActorParam {
  kid:    string;
  reason?: string;
}
export type ParticipantRevokeKeyResult = OkResult;

// ============================================================
//   task.*
// ============================================================

export interface TaskCreateParams extends WorkspaceParam, ActorParam {
  kind:           string;
  input:          unknown;
  assignee?:      ParticipantUri;
  to?:            ParticipantUri;   // alias for assignee
  routing_hints?: Record<string, unknown>;
  deadline?:      string;
}
export interface TaskCreateResult { task_id: TaskId }

export interface TaskUpdateParams extends WorkspaceParam, ActorParam, TaskParam {
  state?:    string;
  output?:   unknown;
  progress?: Record<string, unknown>;
}
export type TaskUpdateResult = OkResult;

export interface TaskCompleteParams extends WorkspaceParam, ActorParam, TaskParam {
  output?:     unknown;
  confidence?: number;
}
export type TaskCompleteResult = OkResult;

// ============================================================
//   review.* and decide.*
// ============================================================

export interface ReviewRequestParams extends WorkspaceParam, ActorParam, TaskParam {
  artefact:  unknown;
  to:        ParticipantUri | ParticipantUri[];
  rule?:     string;
  deadline?: string;
}
export interface ReviewRequestResult { state: string; review_id: TaskId }

export interface DecideApproveParams extends WorkspaceParam, ActorParam, TaskParam {
  rationale?: string;
}
export type DecideApproveResult = OkResult;

export interface DecideRejectParams extends WorkspaceParam, ActorParam, TaskParam {
  rationale: string;
  category?: string;
}
export type DecideRejectResult = OkResult;

export interface DecideOverrideParams extends WorkspaceParam, ActorParam, TaskParam {
  diff:             JsonPatchOp[];
  rationale:        string;
  tags?:            string[];
  policy_refs?:     string[];
  logical_id?:      string;
  instance_id?:     string;
  intent_preserved?: boolean;
  based_on_artefact?: unknown;
}
export interface DecideOverrideResult {
  state:                "completed";
  override_artefact_id: ArtefactId;
  applied:              unknown;
}

export interface AbstainDeclareParams extends WorkspaceParam, ActorParam, TaskParam {
  category:   string;
  rationale?: string;
}
export type AbstainDeclareResult = OkResult;

export interface EscalateRaiseParams extends WorkspaceParam, ActorParam, TaskParam {
  to?:        ParticipantUri;
  rationale?: string;
}
export interface EscalateRaiseResult { ok: true; to?: ParticipantUri }

// ============================================================
//   whisper.* (whisper/1.0)
// ============================================================

export interface WhisperAskParams extends WorkspaceParam, ActorParam, TaskParam {
  to:                ParticipantUri;
  question:          string;
  options?:          string[];
  default_on_lapse?: string;
  lapse_ms?:         number;
}
export interface WhisperAskResult { whisper_id: string }

export interface WhisperAnswerParams extends WorkspaceParam, ActorParam {
  whisper_id: string;
  answer:     string;
}
export type WhisperAnswerResult = OkResult;

// ============================================================
//   deliberate.* (deliberation/1.0)
// ============================================================

export interface DeliberateOpenParams extends WorkspaceParam, ActorParam, TaskParam {
  rule:        Record<string, unknown>;
  proposition: unknown;
  voters:      ParticipantUri[];
}
export interface DeliberateOpenResult { deliberation_id: string }

export interface DeliberateCommentParams extends WorkspaceParam, ActorParam {
  deliberation_id: string;
  text:            string;
}
export type DeliberateCommentResult = OkResult;

export interface DeliberateVoteParams extends WorkspaceParam, ActorParam {
  deliberation_id: string;
  vote:            "yea" | "nay" | "abstain";
  weight?:         number;
  rationale?:      string;
}
export type DeliberateVoteResult = OkResult;

export interface DeliberateCloseParams extends WorkspaceParam, ActorParam {
  deliberation_id: string;
}
export interface DeliberateCloseResult { outcome: "approved" | "rejected" | "no_quorum" }

// ============================================================
//   handoff.* (handoff/1.0)
// ============================================================

export interface HandoffProposeParams extends WorkspaceParam, ActorParam {
  to:                ParticipantUri;
  task_ids:          TaskId[];
  summary:           string;
  active_constraints?: Record<string, unknown>;
}
export interface HandoffProposeResult { handoff_id: string }

export interface HandoffAcceptParams extends WorkspaceParam, ActorParam {
  handoff_id: string;
}
export type HandoffAcceptResult = OkResult;

export interface HandoffDeclineParams extends WorkspaceParam, ActorParam {
  handoff_id: string;
  reason?:    string;
}
export type HandoffDeclineResult = OkResult;

// ============================================================
//   control.* (control/1.0)
// ============================================================

export interface ControlPauseParams extends WorkspaceParam, ActorParam {
  reason?: string;
}
export type ControlPauseResult = OkResult;

export interface ControlResumeParams extends WorkspaceParam, ActorParam {
  reason?: string;
}
export type ControlResumeResult = OkResult;

export interface ControlCancelParams extends WorkspaceParam, ActorParam, TaskParam {
  reason?: string;
}
export type ControlCancelResult = OkResult;

export interface ControlSupersedeParams extends WorkspaceParam, ActorParam, TaskParam {
  replacement:        unknown;
  logical_id?:        string;
  intent_preserved?:  boolean;
  reason?:            string;
}
export interface ControlSupersedeResult { artefact_id: ArtefactId }

export interface ControlSnapshotParams extends WorkspaceParam, ActorParam {
  label?: string;
}
export interface ControlSnapshotResult { snapshot_id: string }

export interface ControlRollbackParams extends WorkspaceParam, ActorParam {
  snapshot_id: string;
  reason?:     string;
}
export type ControlRollbackResult = OkResult;

export interface ControlSetModeCeilingParams extends WorkspaceParam, ActorParam {
  ceiling: "shadow" | "trial" | "production";
}
export type ControlSetModeCeilingResult = OkResult;

// ============================================================
//   routing.* (routing/1.0)
// ============================================================

export interface TaskRouteParams extends WorkspaceParam, ActorParam, TaskParam {
  candidates: ParticipantUri[];
}
export interface TaskRouteResult {
  selected:  ParticipantUri;
  rationale: Record<string, unknown>;
}

export interface ReviewDepthParams extends WorkspaceParam, ActorParam, TaskParam {
  artefact?: unknown;
  hints?:    Record<string, unknown>;
}
export interface ReviewDepthResult {
  depth: "skip" | "spot_check" | "full" | "escalated";
  sampling_probability?: number;
  rationale: Record<string, unknown>;
}

export interface EscalateAutoParams extends WorkspaceParam, ActorParam, TaskParam {
  hints?: Record<string, unknown>;
}
export interface EscalateAutoResult {
  escalated:        boolean;
  to?:              ParticipantUri;
  triggered_rule?:  Record<string, unknown>;
}

// ============================================================
//   audit.* (Core + audit-scitt/1.0)
// ============================================================

export interface AuditReadParams extends WorkspaceParam {
  from_seq?: number;
  limit?:    number;
}
export interface AuditReadResult { entries: unknown[] }

export interface AuditSubmitToScittParams extends WorkspaceParam, ActorParam {
  seq:        number;
  statement?: Record<string, unknown>;
}
export interface AuditSubmitToScittResult { receipt: Record<string, unknown> }

export interface AuditVerifyReceiptParams extends WorkspaceParam {
  receipt: Record<string, unknown>;
}
export interface AuditVerifyReceiptResult { valid: boolean }

export interface AuditVerifyChainParams extends WorkspaceParam {
  from_seq?: number;
  to_seq?:   number;
}
export interface AuditVerifyChainResult {
  valid:    boolean;
  breaks?:  number[];
}

// ============================================================
//   Method-name → {params, result} map (single source of truth)
// ============================================================

export interface MethodTable {
  "workspace.create":          { params: WorkspaceCreateParams,       result: WorkspaceCreateResult       };
  "workspace.describe":        { params: WorkspaceDescribeParams,     result: WorkspaceDescribeResult     };
  "workspace.set_profiles":    { params: WorkspaceSetProfilesParams,  result: WorkspaceSetProfilesResult  };

  "participant.join":          { params: ParticipantJoinParams,       result: ParticipantJoinResult       };
  "participant.leave":         { params: ParticipantLeaveParams,      result: ParticipantLeaveResult      };
  "participant.rotate_key":    { params: ParticipantRotateKeyParams,  result: ParticipantRotateKeyResult  };
  "participant.revoke_key":    { params: ParticipantRevokeKeyParams,  result: ParticipantRevokeKeyResult  };

  "task.create":               { params: TaskCreateParams,            result: TaskCreateResult            };
  "task.update":               { params: TaskUpdateParams,            result: TaskUpdateResult            };
  "task.complete":             { params: TaskCompleteParams,          result: TaskCompleteResult          };

  "review.request":            { params: ReviewRequestParams,         result: ReviewRequestResult         };
  "decide.approve":            { params: DecideApproveParams,         result: DecideApproveResult         };
  "decide.reject":             { params: DecideRejectParams,          result: DecideRejectResult          };
  "decide.override":           { params: DecideOverrideParams,        result: DecideOverrideResult        };
  "abstain.declare":           { params: AbstainDeclareParams,        result: AbstainDeclareResult        };
  "escalate.raise":            { params: EscalateRaiseParams,         result: EscalateRaiseResult         };

  "whisper.ask":               { params: WhisperAskParams,            result: WhisperAskResult            };
  "whisper.answer":            { params: WhisperAnswerParams,         result: WhisperAnswerResult         };

  "deliberate.open":           { params: DeliberateOpenParams,        result: DeliberateOpenResult        };
  "deliberate.comment":        { params: DeliberateCommentParams,     result: DeliberateCommentResult     };
  "deliberate.vote":           { params: DeliberateVoteParams,        result: DeliberateVoteResult        };
  "deliberate.close":          { params: DeliberateCloseParams,       result: DeliberateCloseResult       };

  "handoff.propose":           { params: HandoffProposeParams,        result: HandoffProposeResult        };
  "handoff.accept":            { params: HandoffAcceptParams,         result: HandoffAcceptResult         };
  "handoff.decline":           { params: HandoffDeclineParams,        result: HandoffDeclineResult        };

  "control.pause":             { params: ControlPauseParams,          result: ControlPauseResult          };
  "control.resume":            { params: ControlResumeParams,         result: ControlResumeResult         };
  "control.cancel":            { params: ControlCancelParams,         result: ControlCancelResult         };
  "control.supersede":         { params: ControlSupersedeParams,      result: ControlSupersedeResult      };
  "control.snapshot":          { params: ControlSnapshotParams,       result: ControlSnapshotResult       };
  "control.rollback":          { params: ControlRollbackParams,       result: ControlRollbackResult       };
  "control.set_mode_ceiling":  { params: ControlSetModeCeilingParams, result: ControlSetModeCeilingResult };

  "task.route":                { params: TaskRouteParams,             result: TaskRouteResult             };
  "review.depth":              { params: ReviewDepthParams,           result: ReviewDepthResult           };
  "escalate.auto":             { params: EscalateAutoParams,          result: EscalateAutoResult          };

  "audit.read":                { params: AuditReadParams,             result: AuditReadResult             };
  "audit.submit_to_scitt":     { params: AuditSubmitToScittParams,    result: AuditSubmitToScittResult    };
  "audit.verify_receipt":      { params: AuditVerifyReceiptParams,    result: AuditVerifyReceiptResult    };
  "audit.verify_chain":        { params: AuditVerifyChainParams,      result: AuditVerifyChainResult      };
}

export type MethodName = keyof MethodTable;
export type MethodParams<M extends MethodName> = MethodTable[M]["params"];
export type MethodResult<M extends MethodName> = MethodTable[M]["result"];
