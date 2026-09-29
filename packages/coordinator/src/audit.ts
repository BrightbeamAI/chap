/**
 * @brightbeamai/chap-coordinator/audit
 *
 * Audit entries, the chain link over them, and the rule for recording a
 * refusal (SPECIFICATION 10.1).
 *
 * An accepted call is recorded under `envelope`. A refused call that the rule
 * names is recorded under `request`, with an `outcome` beside it, so a reader
 * keyed on `envelope` passes it by instead of replaying it as effective. The
 * chain link hashes what the entry records: the envelope of an accepted call,
 * and the outcome together with the request of a refusal. Neither half of a
 * refusal can be altered or stripped without breaking the chain, and an entry
 * with no outcome hashes exactly as it always has, so every existing chain
 * still verifies.
 */
import { canonicalize, sha256Hex } from "./canonical.js";
import { E } from "./jsonrpc.js";
import type { AuditEntry, Envelope } from "./types.js";

/** The value an entry's chain link hashes. */
export function entryRecord(entry: AuditEntry): unknown {
  if (entry.outcome !== undefined) return { outcome: entry.outcome, request: entry.request };
  return entry.envelope;
}

/** The call an entry records, whether it took effect or was refused. */
export function entryCall(entry: AuditEntry): Envelope | undefined {
  return entry.envelope ?? entry.request;
}

/** True when an entry records a refused call. */
export function isRefusal(entry: AuditEntry): boolean {
  return entry.outcome !== undefined;
}

/**
 * An entry is well formed when it records an accepted envelope alone, or a
 * refused request with an outcome of status `refused` and an integer code.
 */
export function entryIsWellFormed(entry: AuditEntry): boolean {
  const accepted = entry.envelope !== undefined;
  const refused = entry.request !== undefined;
  if (accepted === refused) return false;
  if (accepted) return entry.outcome === undefined;
  const o = entry.outcome;
  return o !== undefined && o.status === "refused" && Number.isInteger(o.code);
}

/** chain_head = SHA-256( JCS(record) || prev_hash ). */
export function linkHash(record: unknown, prev: string): string {
  return sha256Hex(Buffer.concat([canonicalize(record), Buffer.from(prev, "utf-8")]));
}

/**
 * Refusals the log never records: a call that was malformed or invalid, a
 * fault in the Coordinator, and a call whose signature or key did not check
 * out, which leaves its sender unauthenticated.
 */
const UNRECORDED_CODES: ReadonlySet<number> = new Set([
  E.PARSE, E.REQUEST, E.PARAMS, E.INTERNAL,
  E.SIG_VERIFY_FAILED, E.SIG_KEY_NOT_FOUND, E.SIG_KEY_REVOKED, E.SIG_ROTATION_KEY_MISMATCH,
]);

/**
 * Whether a refusal with this error is one the log records, before the test
 * that the caller is a member.
 *
 * `-32601` is recorded only when the profile gate refused a privileged method,
 * which is an attempt to pull an emergency brake the workspace has switched
 * off. The gate refusing an ordinary method, and a method that does not
 * exist, are not recorded.
 */
export function refusalIsRecorded(
  method: string,
  error: { code: number; data?: unknown },
  privileged: ReadonlySet<string>,
): boolean {
  if (UNRECORDED_CODES.has(error.code)) return false;
  if (error.code === E.METHOD) {
    const data = error.data as { profile?: unknown } | undefined;
    return typeof data?.profile === "string" && privileged.has(method);
  }
  return true;
}
