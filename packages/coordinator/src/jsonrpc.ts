/**
 * @chap/coordinator/jsonrpc
 *
 * Error codes (JSON-RPC standard + CHAP private range) and helpers.
 */

export const E = {
  // JSON-RPC 2.0 standard
  PARSE:    -32700,
  REQUEST:  -32600,
  METHOD:   -32601,
  PARAMS:   -32602,
  INTERNAL: -32603,

  // review/1.0 profile
  NOT_REVIEWABLE: -32010,
  NOT_AUTHORISED: -32011,
  PATCH_FAILED:   -32012,
  REVIEW_LAPSED:  -32013,

  // routing/1.0 profile (signals carried, decisions optional)
  NO_ELIGIBLE_ASSIGNEE:     -32510,
  ROUTING_POLICY_VIOLATION: -32511,
  AUTO_ESCALATION_TRIGGERED: -32512,
  CANDIDATES_EMPTY:         -32513,
  DEPTH_NOT_APPLICABLE:     -32514,
  POLICY_UNREACHABLE:       -32515,
  ESCALATION_TARGET_UNAVAILABLE: -32516,
} as const;

export function rpcError(code: number, message: string, data?: unknown) {
  return { code, message, ...(data !== undefined ? { data } : {}) };
}

export function isValidEnvelope(env: unknown): boolean {
  if (typeof env !== "object" || env === null) return false;
  const e = env as Record<string, unknown>;
  return e.jsonrpc === "2.0";
}
