// What each released create-chap-app wrote into a project, by hash, so
// upgrade can tell a copied file nobody has changed from one somebody has.
// scripts/released-copies.mjs writes released.json from the release tags.

import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

/**
 * A file's content hash, the same on every platform: SHA-256 of the bytes,
 * with a text file's CRLF line endings read as LF, so a checkout that
 * converted them does not count as an edit.
 */
export function contentHash(bytes) {
  const buf = Buffer.isBuffer(bytes) ? bytes : Buffer.from(bytes);
  const text = !buf.includes(0);
  return createHash("sha256").update(text ? Buffer.from(buf.toString("latin1").replace(/\r\n/g, "\n"), "latin1") : buf).digest("hex");
}

/** A placeholder the generator fills, which makes a file differ from project to project. */
export const PLACEHOLDER = /__[A-Z][A-Z_]*__/;

/**
 * released.json: { versions, templates: { <name>: { versions, files:
 * { <path>: { <hash>: [versions] } }, scripts: { <name>: { <command>:
 * [versions] } } } } }. Paths are as they are in a project.
 */
export function loadReleased(url = new URL("./released.json", import.meta.url)) {
  return JSON.parse(readFileSync(url, "utf8"));
}

/** Version strings in release order. */
export function byVersion(a, b) {
  const pa = a.split(".").map(Number);
  const pb = b.split(".").map(Number);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) if ((pa[i] ?? 0) !== (pb[i] ?? 0)) return (pa[i] ?? 0) - (pb[i] ?? 0);
  return 0;
}
