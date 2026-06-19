/**
 * @chap/coordinator/canonical
 *
 * RFC 8785 JSON Canonicalisation Scheme (JCS) implementation.
 * CHAP signs and hashes the JCS canonicalisation of envelopes.
 *
 * Pragmatic JCS sufficient for the JSON value space CHAP uses
 * (objects, arrays, strings, booleans, null, integers, short
 * decimal floats). RFC 8785 §3.2.2.3 number formatting via
 * shortest round-trip.
 */

import { createHash } from "node:crypto";

export const ZERO_HASH = "sha256:" + "0".repeat(64);

function canonicalString(s: string): string {
  // JSON string escaping; \uXXXX for control chars
  return JSON.stringify(s);
}

function canonicalNumber(n: number): string {
  if (!Number.isFinite(n)) {
    throw new Error("Non-finite numbers are not permitted in JCS");
  }
  if (Number.isInteger(n)) return n.toString();
  // Shortest round-trip. JS's default toString is close to RFC 8785's
  // ECMAScript-style rendering and is what JCS requires.
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
