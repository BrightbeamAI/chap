/**
 * @brightbeamai/chap-coordinator/patch
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

// An array index is an RFC 6901 array index: "0" or a positive integer with no
// leading zero. A shared strict rule keeps the two references in step -- JS
// Number() and Python int() otherwise accept disjoint token sets (e.g. "1e1",
// "0x0a", "3.0", "", "1_0"), which would apply the same patch differently.
const ARRAY_INDEX_RE = /^(0|[1-9][0-9]*)$/;

// The JSON type name of a value. Python and JavaScript name their own types
// differently (dict against object, list against object, str against string).
// The refusal names the JSON type, which both references and the
// specification share.
function jsonType(value: unknown): string {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  switch (typeof value) {
    case "object":  return "object";
    case "string":  return "string";
    case "number":  return "number";
    case "boolean": return "boolean";
    default:        return "null";
  }
}

function arrayIndex(seg: string): number {
  if (!ARRAY_INDEX_RE.test(seg)) throw new PatchError(`Array index expected at ${JSON.stringify(seg)}`);
  return Number(seg);
}

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
      const idx = arrayIndex(part);
      if (idx >= parent.length) {
        throw new PatchError(`Index out of range at /${parts.slice(0, i + 1).join("/")}`);
      }
      parent = parent[idx];
    } else if (parent !== null && typeof parent === "object") {
      if (!Object.prototype.hasOwnProperty.call(parent, part)) {
        throw new PatchError(`Path not found: /${parts.slice(0, i + 1).join("/")}`);
      }
      parent = parent[part];
    } else {
      throw new PatchError(`Cannot traverse into ${jsonType(parent)} at ${JSON.stringify(part)}`);
    }
  }
  const last = parts[parts.length - 1];
  if (Array.isArray(parent)) {
    if (last === "-") return [parent, "-"];
    return [parent, arrayIndex(last)];
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

/**
 * Puts `value`, which the document owns from here on, at `path`. A copied
 * value counts towards what the patch carries. A moved value is counted
 * already: `movedFrom` is the number of segments of the place it left, and
 * only its key, a value it replaces and how deep it now reaches change the
 * count. A move to a deeper place, or onto the whole document, costs a pass
 * over the value, so it counts towards what the patch carries as well.
 */
function addAt(doc: any, path: string, value: unknown, size: Size | undefined,
               copied = false, movedFrom?: number): any {
  const parts = splitPath(path);
  if (parts.length === 0) {
    if (size) {
      size.reset(value);
      if (copied || movedFrom !== undefined) size.carry(size);
    }
    return value; // replace whole document
  }
  const [parent, key] = navigate(doc, parts);
  let newKey: string | undefined;
  if (Array.isArray(parent)) {
    if (key === "-") {
      parent.push(value);
    } else {
      const idx = key as number;
      if (idx < 0 || idx > parent.length) {
        throw new PatchError(`Index out of range for add: ${path}`);
      }
      parent.splice(idx, 0, value);
    }
  } else if (parent !== null && typeof parent === "object") {
    if (Object.prototype.hasOwnProperty.call(parent, key)) size?.take(parent[key as string]);
    else newKey = key as string;
    parent[key as string] = value;
  } else {
    throw new PatchError(`Cannot add into ${jsonType(parent)}`);
  }
  if (size) {
    if (movedFrom === undefined) {
      const inserted = size.insert(value, parts.length, newKey);
      if (copied) size.carry(inserted);
    } else {
      if (newKey !== undefined) size.bytes += utf8Length(newKey);
      if (parts.length > movedFrom) {
        // A move to a deeper place costs a pass over the value, for its depth.
        const moved = sizeOf(value);
        size.depth = Math.max(size.depth, parts.length + moved.depth);
        size.carry(moved);
      }
    }
  }
  return doc;
}

/** Removes the value at `path`. A moved value goes back in elsewhere, so only its key leaves the count. */
function removeAt(doc: any, path: string, size: Size | undefined, moved = false): any {
  const parts = splitPath(path);
  if (parts.length === 0) throw new PatchError("Cannot remove root.");
  const [parent, key] = navigate(doc, parts);
  if (Array.isArray(parent)) {
    // RFC 6901: "-" names no existing element, so it cannot be removed.
    // Left as a string it passes the numeric range check and then splice
    // coerces it to zero, removing the first element instead.
    const idx = key === "-" ? -1 : (key as number);
    if (idx < 0 || idx >= parent.length) {
      throw new PatchError(`Index out of range for remove: ${path}`);
    }
    if (size && !moved) size.take(parent[idx]);
    parent.splice(idx, 1);
  } else if (parent !== null && typeof parent === "object") {
    if (!Object.prototype.hasOwnProperty.call(parent, key)) {
      throw new PatchError(`Path not found for remove: ${path}`);
    }
    if (size) {
      if (moved) size.bytes -= utf8Length(key as string);
      else size.take(parent[key as string], key as string);
    }
    delete parent[key as string];
  } else {
    throw new PatchError(`Cannot remove from ${jsonType(parent)}`);
  }
  return doc;
}

function applyOne(doc: any, op: JsonPatchOp, size?: Size): any {
  const kind = op.op;
  const path = op.path ?? "";

  if (kind === "add") {
    if (!("value" in op)) throw new PatchError("'add' requires 'value'");
    // A copy, so an operation on the inserted value leaves the patch as it
    // was sent.
    return addAt(doc, path, clone(op.value), size);
  }

  if (kind === "replace") {
    if (!("value" in op)) throw new PatchError("'replace' requires 'value'");
    const parts = splitPath(path);
    const value = clone(op.value);
    if (parts.length === 0) {
      size?.reset(value);
      return value;
    }
    const [parent, key] = navigate(doc, parts);
    if (Array.isArray(parent)) {
      // RFC 6901: "-" names the position after the last element, which no
      // element occupies, so only "add" can use it. Left as a string it slips
      // through a numeric range check and writes a "-" property on the array.
      const idx = key === "-" ? -1 : (key as number);
      if (idx < 0 || idx >= parent.length) {
        throw new PatchError(`Path not found for replace: ${path}`);
      }
      size?.take(parent[idx]);
      parent[idx] = value;
    } else if (parent !== null && typeof parent === "object") {
      if (!Object.prototype.hasOwnProperty.call(parent, key)) {
        throw new PatchError(`Path not found for replace: ${path}`);
      }
      size?.take(parent[key as string]);
      parent[key as string] = value;
    } else {
      throw new PatchError(`Cannot replace in ${jsonType(parent)}`);
    }
    size?.insert(value, parts.length);
    return doc;
  }

  if (kind === "remove") {
    return removeAt(doc, path, size);
  }

  if (kind === "copy") {
    const src = op.from;
    if (src === undefined) throw new PatchError("'copy' requires 'from'");
    return addAt(doc, path, clone(getAt(doc, src)), size, true);
  }

  if (kind === "move") {
    const src = op.from;
    if (src === undefined) throw new PatchError("'move' requires 'from'");
    // RFC 6902 4.4: 'from' MUST NOT be a proper prefix of 'path'.
    if (path.startsWith(src + "/")) {
      throw new PatchError("'move' cannot move a location into its own child.");
    }
    // The remove detaches the value, so it moves as it is, uncopied.
    const value = getAt(doc, src);
    doc = removeAt(doc, src, size, true);
    return addAt(doc, path, value, size, false, splitPath(src).length);
  }

  if (kind === "test") {
    if (!("value" in op)) throw new PatchError("'test' requires 'value'");
    const actual = getAt(doc, path);
    if (!deepEqual(actual, op.value)) throw new PatchError(`'test' failed at ${path}`);
    return doc;
  }

  throw new PatchError(`Unsupported op: ${JSON.stringify(kind)}`);
}

const MAX_PATCH_OPS = 1000;
const MAX_DOCUMENT_NODES = 100_000;

/**
 * How deeply a patched document may nest, counting the document as level one,
 * as the coordinator counts a request's nesting. Each operation's value comes
 * within the request's limit, and a patch that puts one value inside another
 * would otherwise nest the document without limit.
 */
const MAX_DOCUMENT_DEPTH = 64;

/**
 * How much a patch's copies, and its moves to a deeper place or onto the whole
 * document, may carry between them, as a multiple of the document's limits.
 * Each costs a pass over the value it carries, so a patch that copied the same
 * large value back and forth could otherwise keep a coordinator busy for
 * seconds.
 */
const MAX_CARRIED_TIMES = 3;

/**
 * The default cap on the string and key text a patched document may hold, in
 * UTF-8 bytes: the default `max_envelope_bytes`. An artefact that arrives in a
 * request holds no more text than the request's size. The node cap alone does
 * not bound size: a `copy` of the root doubles the document, long strings
 * included, so fifteen of them grow 64 KiB to about 2 GiB within 100,000
 * nodes.
 */
export const DEFAULT_MAX_PATCHED_BYTES = 1_048_576;

/** Limits a caller can set on {@link applyJsonPatch}. */
export interface PatchLimits {
  /** The most UTF-8 bytes of string and key text the result may hold. */
  maxStringBytes?: number;
}

/**
 * UTF-8 length of a string: Buffer's count where Node provides it, and the
 * same count by hand elsewhere, so the module runs anywhere. A lone surrogate
 * counts three bytes either way, as does Python's
 * `len(s.encode("utf-8", "surrogatepass"))`, which keeps the two references
 * refusing the same patches.
 */
function utf8Length(s: string): number {
  const buffer = (globalThis as { Buffer?: unknown }).Buffer;
  if (typeof buffer === "function") {
    return (buffer as unknown as { byteLength(s: string, encoding: "utf8"): number }).byteLength(s, "utf8");
  }
  let n = 0;
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c < 0x80) n += 1;
    else if (c < 0x800) n += 2;
    else if (c >= 0xd800 && c <= 0xdbff && i + 1 < s.length) {
      const d = s.charCodeAt(i + 1);
      if (d >= 0xdc00 && d <= 0xdfff) { n += 4; i++; } else n += 3;
    } else n += 3;
  }
  return n;
}

interface Measured { nodes: number; bytes: number; depth: number }

/**
 * A value's count of values, the UTF-8 bytes of its strings and keys, and its
 * depth, counting the value itself as level one, in one pass that does not
 * recurse.
 */
function sizeOf(value: unknown): Measured {
  if (value === null || typeof value !== "object") {
    return { nodes: 1, bytes: typeof value === "string" ? utf8Length(value) : 0, depth: 1 };
  }
  let nodes = 0;
  let bytes = 0;
  let depth = 0;
  const values: object[] = [value];
  const levels: number[] = [1];
  // A scalar child is counted where it is found, which saves a trip through
  // the stack.
  const visit = (x: unknown, level: number): void => {
    if (x !== null && typeof x === "object") {
      values.push(x);
      levels.push(level);
      return;
    }
    nodes += 1;
    if (level > depth) depth = level;
    if (typeof x === "string") bytes += utf8Length(x);
  };
  while (values.length > 0) {
    const v = values.pop()!;
    const level = levels.pop()!;
    nodes += 1;
    if (level > depth) depth = level;
    if (Array.isArray(v)) {
      for (const x of v) visit(x, level + 1);
    } else {
      const o = v as Record<string, unknown>;
      for (const k of Object.keys(o)) {
        bytes += utf8Length(k);
        visit(o[k], level + 1);
      }
    }
  }
  return { nodes, bytes, depth };
}

/**
 * What the working document holds, kept in step by each operation. The values
 * and the bytes are exact: each operation counts what it puts in and what it
 * takes out, so a patch costs what it changes, whatever the document's size.
 * The depth is a bound, raised by what each operation puts in; it passes the
 * limit when the document does, or when the document started deeper.
 */
class Size implements Measured {
  nodes = 0;
  bytes = 0;
  depth = 0;
  /** What copies, and moves to a deeper place or onto the whole document, have carried. */
  carriedNodes = 0;
  carriedBytes = 0;

  constructor(doc: unknown) {
    this.reset(doc);
  }

  reset(doc: unknown): void {
    const s = sizeOf(doc);
    this.nodes = s.nodes;
    this.bytes = s.bytes;
    this.depth = s.depth;
  }

  /** `value` goes in `segments` levels below the top, under a new `key`. */
  insert(value: unknown, segments: number, key?: string): Measured {
    const s = sizeOf(value);
    this.nodes += s.nodes;
    this.bytes += s.bytes + (key === undefined ? 0 : utf8Length(key));
    this.depth = Math.max(this.depth, segments + s.depth);
    return s;
  }

  carry(s: Measured): void {
    this.carriedNodes += s.nodes;
    this.carriedBytes += s.bytes;
  }

  /** `value` comes out, with its `key`. */
  take(value: unknown, key?: string): void {
    const s = sizeOf(value);
    this.nodes -= s.nodes;
    this.bytes -= s.bytes + (key === undefined ? 0 : utf8Length(key));
  }
}

export function applyJsonPatch(doc: unknown, ops: JsonPatchOp[], limits: PatchLimits = {}): unknown {
  const maxBytes = limits.maxStringBytes ?? DEFAULT_MAX_PATCHED_BYTES;
  if (ops.length > MAX_PATCH_OPS) {
    throw new PatchError(`Patch has too many operations (${ops.length} > ${MAX_PATCH_OPS})`);
  }
  let out: any = JSON.parse(JSON.stringify(doc));
  const size = new Size(out);
  for (const op of ops) {
    out = applyOne(out, op, size);
    if (size.depth > MAX_DOCUMENT_DEPTH) size.depth = sizeOf(out).depth;
    if (size.nodes > MAX_DOCUMENT_NODES) {
      throw new PatchError(`Patched document exceeds the node limit (${MAX_DOCUMENT_NODES})`);
    }
    if (size.bytes > maxBytes) {
      throw new PatchError(`Patched document exceeds the size limit (${maxBytes} bytes of strings and keys)`);
    }
    if (size.depth > MAX_DOCUMENT_DEPTH) {
      throw new PatchError(`Patched document nests deeper than ${MAX_DOCUMENT_DEPTH} levels`);
    }
    if (size.carriedNodes > MAX_CARRIED_TIMES * MAX_DOCUMENT_NODES || size.carriedBytes > MAX_CARRIED_TIMES * maxBytes) {
      throw new PatchError(`Patch copies or moves more than its limit (${MAX_CARRIED_TIMES * MAX_DOCUMENT_NODES} values or ${MAX_CARRIED_TIMES * maxBytes} bytes of strings and keys)`);
    }
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
