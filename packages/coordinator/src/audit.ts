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
 * and the outcome together with the request of a refusal. Altering either
 * half of a refusal breaks the chain, moving its record under `envelope` fails
 * the shape check, and an entry with no outcome hashes exactly as it always
 * has, so every existing chain still verifies.
 */
import { canonicalize, sha256Hex } from "./canonical.js";
import { E } from "./jsonrpc.js";
import type { AuditEntry, Envelope } from "./types.js";

/** The value an entry's chain link hashes. */
export function entryRecord(entry: AuditEntry): unknown {
  if (entry.outcome != null) return { outcome: entry.outcome, request: entry.request };
  return entry.envelope;
}

/** The call an entry records, whether it took effect or was refused. */
export function entryCall(entry: AuditEntry): Envelope | undefined {
  return entry.envelope ?? entry.request ?? undefined;
}

/** True when an entry records a refused call. */
export function isRefusal(entry: AuditEntry): boolean {
  return entry.outcome != null;
}

/** A JSON-RPC call: an object with `jsonrpc` "2.0" and a string `method`. */
function isCall(value: unknown): boolean {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const v = value as Record<string, unknown>;
  return v.jsonrpc === "2.0" && typeof v.method === "string";
}

/**
 * An entry is well formed when it records an accepted call alone, under
 * `envelope`, or a refused call alone, under `request`, with an outcome of
 * status `refused` and an integer code. Requiring a JSON-RPC call on either
 * side is what stops a refusal's record being moved under `envelope`, where it
 * would hash to the same bytes and read as a call that took effect.
 */
export function entryIsWellFormed(entry: AuditEntry): boolean {
  const accepted = entry.envelope != null;
  const refused = entry.request != null;
  if (accepted === refused) return false;
  if (accepted) return isCall(entry.envelope) && entry.outcome == null;
  const o = entry.outcome as unknown as Record<string, unknown> | null | undefined;
  return isCall(entry.request) && o != null && typeof o === "object"
    && o.status === "refused" && Number.isInteger(o.code);
}

/** Whether a call carries a top-level signature (security-signed/1.0). */
export function isSigned(call: unknown): boolean {
  return call !== null && typeof call === "object" && typeof (call as { sig?: unknown }).sig === "string";
}

/**
 * The digest that identifies a signed call by what its sender signed: SHA-256
 * of the JCS of the call without its `sig`. Two encodings of one signature
 * give the same digest, so re-encoding a signature does not make a new call.
 */
export function signedDigest(call: Envelope): string {
  const signed: Record<string, unknown> = { ...call };
  delete signed.sig;
  return sha256Hex(canonicalize(signed));
}

/** chain_head = SHA-256( JCS(record) || prev_hash ). */
export function linkHash(record: unknown, prev: string): string {
  return sha256Hex(Buffer.concat([canonicalize(record), Buffer.from(prev, "utf-8")]));
}

/**
 * Refusals the log never records: a call that was malformed or invalid, a
 * fault in the Coordinator, and a call whose signature or key did not check
 * out.
 */
const UNRECORDED_CODES: ReadonlySet<number> = new Set([
  E.PARSE, E.REQUEST, E.PARAMS, E.INTERNAL,
  E.SIG_VERIFY_FAILED, E.SIG_KEY_NOT_FOUND, E.SIG_KEY_REVOKED, E.SIG_ROTATION_KEY_MISMATCH,
]);

/**
 * Methods whose refusals are never recorded. They run before the caller is
 * established as a member and are exempt from signature checks, so a refusal
 * of one proves nothing about who sent it.
 */
const UNRECORDED_METHODS: ReadonlySet<string> = new Set(["workspace.create", "participant.join"]);

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
  if (!Number.isInteger(error.code)) return false;
  if (UNRECORDED_METHODS.has(method)) return false;
  if (UNRECORDED_CODES.has(error.code)) return false;
  if (error.code === E.METHOD) {
    const data = error.data as { profile?: unknown } | undefined;
    return typeof data?.profile === "string" && privileged.has(method);
  }
  return true;
}
