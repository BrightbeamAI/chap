/**
 * Typed method facade for the Coordinator.
 *
 * Replaces the verbose dispatch pattern:
 *
 *   const env = await coord.dispatch({
 *     jsonrpc: "2.0", id: "x",
 *     method: "task.create",
 *     params: { workspace, from, kind, input }
 *   });
 *   const taskId = env.result.task_id;
 *
 * with namespace-grouped calls that autocomplete and type-check:
 *
 *   const { task_id } = coord.api.task.create({ workspace, from, kind, input });
 *
 * Every method on the facade calls `dispatch(envelope)` internally with
 * an auto-generated envelope id, so the audit chain semantics, profile
 * gates, and signature checks all run identically.
 *
 * On dispatch error, the facade throws a `CoordinatorError` carrying the
 * JSON-RPC error code and message. Callers expecting `error` objects on
 * the wire can still use `dispatch()` directly.
 */

import type { Coordinator } from "./coordinator.js";
import type { Envelope } from "./types.js";
import type {
  MethodName, MethodParams, MethodResult,
} from "./methods.js";

export class CoordinatorError extends Error {
  readonly code: number;
  readonly data?: unknown;
  constructor(code: number, message: string, data?: unknown) {
    super(message);
    this.name = "CoordinatorError";
    this.code = code;
    this.data = data;
  }
}

/**
 * Single-call helper. Type-safe wrapper around `coord.dispatch()`.
 * Throws CoordinatorError if dispatch returns an error response.
 */
export function call<M extends MethodName>(
  coord: Coordinator,
  method: M,
  params: MethodParams<M>,
): MethodResult<M> {
  const envelope: Envelope = {
    jsonrpc: "2.0",
    id: coord.ids.envelopeId(),
    method,
    params: params as unknown as Record<string, unknown>,
  };
  const out = coord.dispatch(envelope);
  if (out.error) {
    throw new CoordinatorError(out.error.code, out.error.message, out.error.data);
  }
  return out.result as MethodResult<M>;
}

// ============================================================
//   Namespace-grouped facades
// ============================================================
//
// Each namespace facade is a small object exposing one method per
// CHAP verb. Implementations are one-liners that delegate to call().
// The pattern is verbose but autocompletes well and type-checks
// param shape and result shape from one declaration in methods.ts.

export function makeApi(coord: Coordinator) {
  const c = <M extends MethodName>(m: M, p: MethodParams<M>) => call(coord, m, p);

  return {
    workspace: {
      create:       (p: MethodParams<"workspace.create">)       => c("workspace.create", p),
      describe:     (p: MethodParams<"workspace.describe">)     => c("workspace.describe", p),
      set_profiles: (p: MethodParams<"workspace.set_profiles">) => c("workspace.set_profiles", p),
    },
    participant: {
      join:       (p: MethodParams<"participant.join">)       => c("participant.join", p),
      leave:      (p: MethodParams<"participant.leave">)      => c("participant.leave", p),
      rotate_key: (p: MethodParams<"participant.rotate_key">) => c("participant.rotate_key", p),
      revoke_key: (p: MethodParams<"participant.revoke_key">) => c("participant.revoke_key", p),
    },
    task: {
      create:   (p: MethodParams<"task.create">)   => c("task.create", p),
      update:   (p: MethodParams<"task.update">)   => c("task.update", p),
      complete: (p: MethodParams<"task.complete">) => c("task.complete", p),
      route:    (p: MethodParams<"task.route">)    => c("task.route", p),
    },
    review: {
      request: (p: MethodParams<"review.request">) => c("review.request", p),
      depth:   (p: MethodParams<"review.depth">)   => c("review.depth", p),
    },
    decide: {
      approve:  (p: MethodParams<"decide.approve">)  => c("decide.approve", p),
      reject:   (p: MethodParams<"decide.reject">)   => c("decide.reject", p),
      override: (p: MethodParams<"decide.override">) => c("decide.override", p),
    },
    abstain: {
      declare: (p: MethodParams<"abstain.declare">) => c("abstain.declare", p),
    },
    escalate: {
      raise: (p: MethodParams<"escalate.raise">) => c("escalate.raise", p),
      auto:  (p: MethodParams<"escalate.auto">)  => c("escalate.auto", p),
    },
    whisper: {
      ask:    (p: MethodParams<"whisper.ask">)    => c("whisper.ask", p),
      answer: (p: MethodParams<"whisper.answer">) => c("whisper.answer", p),
    },
    deliberate: {
      open:    (p: MethodParams<"deliberate.open">)    => c("deliberate.open", p),
      comment: (p: MethodParams<"deliberate.comment">) => c("deliberate.comment", p),
      vote:    (p: MethodParams<"deliberate.vote">)    => c("deliberate.vote", p),
      close:   (p: MethodParams<"deliberate.close">)   => c("deliberate.close", p),
    },
    handoff: {
      propose: (p: MethodParams<"handoff.propose">) => c("handoff.propose", p),
      accept:  (p: MethodParams<"handoff.accept">)  => c("handoff.accept", p),
      decline: (p: MethodParams<"handoff.decline">) => c("handoff.decline", p),
    },
    control: {
      pause:             (p: MethodParams<"control.pause">)             => c("control.pause", p),
      resume:            (p: MethodParams<"control.resume">)            => c("control.resume", p),
      cancel:            (p: MethodParams<"control.cancel">)            => c("control.cancel", p),
      supersede:         (p: MethodParams<"control.supersede">)         => c("control.supersede", p),
      snapshot:          (p: MethodParams<"control.snapshot">)          => c("control.snapshot", p),
      rollback:          (p: MethodParams<"control.rollback">)          => c("control.rollback", p),
      set_mode_ceiling:  (p: MethodParams<"control.set_mode_ceiling">)  => c("control.set_mode_ceiling", p),
    },
    audit: {
      read:            (p: MethodParams<"audit.read">)            => c("audit.read", p),
      submit_to_scitt: (p: MethodParams<"audit.submit_to_scitt">) => c("audit.submit_to_scitt", p),
      verify_receipt:  (p: MethodParams<"audit.verify_receipt">)  => c("audit.verify_receipt", p),
      verify_chain:    (p: MethodParams<"audit.verify_chain">)    => c("audit.verify_chain", p),
    },
  };
}

export type CoordinatorApi = ReturnType<typeof makeApi>;
