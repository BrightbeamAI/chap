/**
 * @brightbeamai/chap-coordinator/canonical
 *
 * Deterministic JSON canonicalisation for CHAP. CHAP signs and hashes the
 * canonicalisation of envelopes.
 *
 * Follows RFC 8785 (JCS) for objects, arrays, strings, booleans, and null,
 * with one deliberate restriction on numbers. RFC 8785 mandates the
 * ECMAScript number-to-string algorithm, which is hard to reproduce
 * byte-identically across languages; a subtle mismatch would make a chain
 * written by one implementation fail verification against another. To make
 * cross-implementation agreement provable rather than approximate, a CHAP
 * canonical number MUST be an integer within the safe-integer range
 * (abs value <= Number.MAX_SAFE_INTEGER). Non-integers and larger
 * magnitudes are rejected: represent them as strings. The Python reference
 * enforces the identical rule.
 */

import { createHash } from "node:crypto";

export const ZERO_HASH = "sha256:" + "0".repeat(64);

const NON_INTEGER_ERROR =
  'CHAP canonical numbers must be integers; represent decimals as strings ' +
  '(e.g. "8.2") so the hash is deterministic across implementations.';
const NUMBER_RANGE_ERROR =
  "CHAP canonical integers must be within the safe-integer range " +
  "(abs value <= Number.MAX_SAFE_INTEGER); represent larger numbers as strings.";

function canonicalString(s: string): string {
  // JSON string escaping; \uXXXX for control chars
  return JSON.stringify(s);
}

function canonicalNumber(n: number): string {
  if (!Number.isFinite(n)) {
    throw new Error(
      "Non-finite numbers are not permitted in a CHAP canonical value",
    );
  }
  if (!Number.isInteger(n)) {
    throw new Error(NON_INTEGER_ERROR);
  }
  if (Math.abs(n) > Number.MAX_SAFE_INTEGER) {
    throw new Error(NUMBER_RANGE_ERROR);
  }
  // A safe integer's toString() is always plain decimal digits (no
  // exponent), byte-identical to Python's str(int(...)).
  return n.toString();
}

function canon(value: unknown): string {
  if (value === null) return "null";
  if (value === true) return "true";
  if (value === false) return "false";
  if (typeof value === "string") return canonicalString(value);
  if (typeof value === "number") return canonicalNumber(value);
  if (Array.isArray(value)) {
    return "[" + value.map(canon).join(",") + "]";
  }
  if (typeof value === "object") {
    const obj = value as Record<string, unknown>;
    const keys = Object.keys(obj).sort();
    const parts: string[] = [];
    for (const key of keys) {
      parts.push(canonicalString(key) + ":" + canon(obj[key]));
    }
    return "{" + parts.join(",") + "}";
  }
  throw new TypeError(`Cannot canonicalise value of type ${typeof value}`);
}

/** Return the JCS canonical UTF-8 bytes for a JSON-compatible value. */
export function canonicalize(value: unknown): Buffer {
  return Buffer.from(canon(value), "utf-8");
}

/** Return ``sha256:<64-hex>`` digest of bytes. */
export function sha256Hex(data: Buffer | string): string {
  const h = createHash("sha256");
  h.update(typeof data === "string" ? Buffer.from(data, "utf-8") : data);
  return "sha256:" + h.digest("hex");
}

/** Convenience: content hash for an artefact payload via JCS. */
export function contentHash(content: unknown): string {
  return sha256Hex(canonicalize(content));
}
