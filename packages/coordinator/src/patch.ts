/**
 * @brightbeamai/coordinator/patch
 *
 * RFC 6902 JSON Patch implementation, plus a small diff computer that
 * produces a patch from two values. Used by the Coordinator for
 * `decide.override` apply and by the playground UI (in the browser) to
 * compute the patch from a textarea edit.
 *
 * Implements all six RFC 6902 operations (add, remove, replace, move,
 * copy, test) with semantics identical to the Python reference. Path
 * segments that enable prototype pollution in a JavaScript runtime
 * (`__proto__`, `constructor`, `prototype`) are rejected, so the two
 * references accept and reject exactly the same patches.
 */

import type { JsonPatchOp } from "./types.js";

export class PatchError extends Error {}

// Rejected in every JSON Pointer segment: these enable prototype pollution.
const DANGEROUS_KEYS = new Set(["__proto__", "constructor", "prototype"]);

function unescape(token: string): string {
  return token.replace(/~1/g, "/").replace(/~0/g, "~");
}

function splitPath(path: string): string[] {
  if (path === "") return [];
  if (!path.startsWith("/")) {
    throw new PatchError(`JSON Pointer must start with '/': ${JSON.stringify(path)}`);
  }
  const segments = path.slice(1).split("/").map(unescape);
  for (const seg of segments) {
    if (DANGEROUS_KEYS.has(seg)) {
      throw new PatchError(`Refusing unsafe path segment ${JSON.stringify(seg)}`);
    }
  }
  return segments;
}

/** Navigate to the parent of the target; return [parent, lastKey]. */
function navigate(doc: any, parts: string[]): [any, string | number] {
  if (parts.length === 0) {
    throw new PatchError("Cannot operate on root with this helper.");
  }
  let parent = doc;
  for (let i = 0; i < parts.length - 1; i++) {
    const part = parts[i];
    if (Array.isArray(parent)) {
      const idx = Number(part);
      if (!Number.isInteger(idx) || idx < 0 || idx >= parent.length) {
        throw new PatchError(`Index out of range at /${parts.slice(0, i + 1).join("/")}`);
      }
      parent = parent[idx];
    } else if (parent !== null && typeof parent === "object") {
      if (!Object.prototype.hasOwnProperty.call(parent, part)) {
        throw new PatchError(`Path not found: /${parts.slice(0, i + 1).join("/")}`);
      }
      parent = parent[part];
    } else {
      throw new PatchError(`Cannot traverse into ${typeof parent} at ${JSON.stringify(part)}`);
    }
  }
  const last = parts[parts.length - 1];
  if (Array.isArray(parent)) {
    if (last === "-") return [parent, "-"];
    const idx = Number(last);
    if (!Number.isInteger(idx)) {
      throw new PatchError(`Array index expected at ${JSON.stringify(last)}`);
    }
    return [parent, idx];
  }
  return [parent, last];
}

function getAt(doc: any, path: string): any {
  const parts = splitPath(path);
  if (parts.length === 0) return doc;
  const [parent, key] = navigate(doc, parts);
  if (Array.isArray(parent)) {
    if (key === "-") throw new PatchError("Cannot read '-' position.");
    const idx = key as number;
    if (idx < 0 || idx >= parent.length) throw new PatchError(`Index out of range: ${path}`);
    return parent[idx];
  }
  if (
    parent === null ||
    typeof parent !== "object" ||
    !Object.prototype.hasOwnProperty.call(parent, key)
  ) {
    throw new PatchError(`Path not found: ${path}`);
  }
  return parent[key as string];
}

function clone<T>(v: T): T {
  return v === undefined ? v : (JSON.parse(JSON.stringify(v)) as T);
}

function deepEqual(a: any, b: any): boolean {
  if (a === b) return true;
  if (a === null || b === null) return a === b;
  if (typeof a !== typeof b) return false;
  if (typeof a !== "object") return a === b;
  const aArr = Array.isArray(a);
  if (aArr !== Array.isArray(b)) return false;
  if (aArr) {
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) if (!deepEqual(a[i], b[i])) return false;
    return true;
  }
  const ak = Object.keys(a);
  const bk = Object.keys(b);
  if (ak.length !== bk.length) return false;
  for (const k of ak) {
    if (!Object.prototype.hasOwnProperty.call(b, k)) return false;
    if (!deepEqual(a[k], b[k])) return false;
  }
  return true;
}

function applyOne(doc: any, op: JsonPatchOp): any {
  const kind = op.op;
  const path = op.path ?? "";

  if (kind === "add") {
    if (!("value" in op)) throw new PatchError("'add' requires 'value'");
    const parts = splitPath(path);
    if (parts.length === 0) return op.value; // replace whole document
    const [parent, key] = navigate(doc, parts);
    if (Array.isArray(parent)) {
      if (key === "-") {
        parent.push(op.value);
      } else {
        const idx = key as number;
        if (idx < 0 || idx > parent.length) {
          throw new PatchError(`Index out of range for add: ${path}`);
        }
        parent.splice(idx, 0, op.value);
      }
    } else if (parent !== null && typeof parent === "object") {
      parent[key as string] = op.value;
    } else {
      throw new PatchError(`Cannot add into ${typeof parent}`);
    }
    return doc;
  }

  if (kind === "replace") {
    if (!("value" in op)) throw new PatchError("'replace' requires 'value'");
    const parts = splitPath(path);
    if (parts.length === 0) return op.value;
    const [parent, key] = navigate(doc, parts);
    if (Array.isArray(parent)) {
      const idx = key as number;
      if (idx < 0 || idx >= parent.length) {
        throw new PatchError(`Path not found for replace: ${path}`);
      }
      parent[idx] = op.value;
    } else if (parent !== null && typeof parent === "object") {
      if (!Object.prototype.hasOwnProperty.call(parent, key)) {
        throw new PatchError(`Path not found for replace: ${path}`);
      }
      parent[key as string] = op.value;
    } else {
      throw new PatchError(`Cannot replace in ${typeof parent}`);
    }
    return doc;
  }

  if (kind === "remove") {
    const parts = splitPath(path);
    if (parts.length === 0) throw new PatchError("Cannot remove root.");
    const [parent, key] = navigate(doc, parts);
    if (Array.isArray(parent)) {
      const idx = key as number;
      if (idx < 0 || idx >= parent.length) {
        throw new PatchError(`Index out of range for remove: ${path}`);
      }
      parent.splice(idx, 1);
    } else if (parent !== null && typeof parent === "object") {
      if (!Object.prototype.hasOwnProperty.call(parent, key)) {
        throw new PatchError(`Path not found for remove: ${path}`);
      }
      delete parent[key as string];
    } else {
      throw new PatchError(`Cannot remove from ${typeof parent}`);
    }
    return doc;
  }

  if (kind === "copy") {
    const src = op.from;
    if (src === undefined) throw new PatchError("'copy' requires 'from'");
    const value = clone(getAt(doc, src));
    return applyOne(doc, { op: "add", path, value });
  }

  if (kind === "move") {
    const src = op.from;
    if (src === undefined) throw new PatchError("'move' requires 'from'");
    // RFC 6902 4.4: 'from' MUST NOT be a proper prefix of 'path'.
    if (path.startsWith(src + "/")) {
      throw new PatchError("'move' cannot move a location into its own child.");
    }
    const value = clone(getAt(doc, src));
    doc = applyOne(doc, { op: "remove", path: src });
    return applyOne(doc, { op: "add", path, value });
  }

  if (kind === "test") {
    if (!("value" in op)) throw new PatchError("'test' requires 'value'");
    const actual = getAt(doc, path);
    if (!deepEqual(actual, op.value)) throw new PatchError(`'test' failed at ${path}`);
    return doc;
  }

  throw new PatchError(`Unsupported op: ${JSON.stringify(kind)}`);
}

export function applyJsonPatch(doc: unknown, ops: JsonPatchOp[]): unknown {
  let out: any = JSON.parse(JSON.stringify(doc));
  for (const op of ops) {
    out = applyOne(out, op);
  }
  return out;
}

// ============================================================
//   diff: produce a patch that transforms `from` into `to`
// ============================================================

/**
 * Compute an RFC 6902 patch that, when applied to `from`, produces
 * `to`. Walks objects key-by-key; arrays use a shallow whole-array
 * replace if any element differs. Sufficient for the playground's
 * draft-edit use case where drafts are small JSON objects.
 */
export function diffJsonPatch(from: unknown, to: unknown, basePath = ""): JsonPatchOp[] {
  const ops: JsonPatchOp[] = [];

  if (typeof from !== typeof to || Array.isArray(from) !== Array.isArray(to)) {
    ops.push({ op: "replace", path: basePath || "/", value: to });
    return ops;
  }

  if (from === null || to === null || typeof from !== "object") {
    if (from !== to) {
      ops.push({ op: "replace", path: basePath || "/", value: to });
    }
    return ops;
  }

  if (Array.isArray(from) && Array.isArray(to)) {
    if (JSON.stringify(from) !== JSON.stringify(to)) {
      ops.push({ op: "replace", path: basePath || "/", value: to });
    }
    return ops;
  }

  const fromObj = from as Record<string, unknown>;
  const toObj = to as Record<string, unknown>;
  const allKeys = new Set([...Object.keys(fromObj), ...Object.keys(toObj)]);

  for (const key of allKeys) {
    const escaped = key.replace(/~/g, "~0").replace(/\//g, "~1");
    const path = `${basePath}/${escaped}`;
    if (!(key in fromObj)) {
      ops.push({ op: "add", path, value: toObj[key] });
    } else if (!(key in toObj)) {
      ops.push({ op: "remove", path });
    } else {
      ops.push(...diffJsonPatch(fromObj[key], toObj[key], path));
    }
  }

  return ops;
}
