/**
 * @chap/coordinator/patch
 *
 * Minimal RFC 6902 JSON Patch implementation, plus a small diff
 * computer that produces a patch from two values. Used by the
 * Coordinator for `decide.override` apply and by the playground UI
 * (in the browser) to compute the patch from a textarea edit.
 *
 * Scope: add / replace / remove for objects and arrays. test / copy /
 * move are intentionally unsupported in this teaching reference.
 */

import type { JsonPatchOp } from "./types.js";

// ============================================================
//   apply
// ============================================================

export function applyJsonPatch(doc: unknown, ops: JsonPatchOp[]): unknown {
  const target: any = JSON.parse(JSON.stringify(doc));

  for (const op of ops) {
    const parts = op.path
      .split("/")
      .slice(1)
      .map((p) => p.replace(/~1/g, "/").replace(/~0/g, "~"));
    if (parts.length === 0) {
      // root replace
      if (op.op === "replace" || op.op === "add") {
        return JSON.parse(JSON.stringify(op.value));
      }
      throw new Error(`Cannot ${op.op} at root path`);
    }

    let parent: any = target;
    for (let i = 0; i < parts.length - 1; i++) {
      const key = Array.isArray(parent) ? parseInt(parts[i], 10) : parts[i];
      if (parent[key] === undefined) {
        if (op.op === "add") {
          parent[key] = {};
        } else {
          throw new Error(`Path not found: ${op.path}`);
        }
      }
      parent = parent[key];
    }

    const lastKey = Array.isArray(parent)
      ? parseInt(parts[parts.length - 1], 10)
      : parts[parts.length - 1];

    switch (op.op) {
      case "add":
      case "replace":
        parent[lastKey] = op.value;
        break;
      case "remove":
        if (Array.isArray(parent)) parent.splice(lastKey as number, 1);
        else delete parent[lastKey];
        break;
      default:
        throw new Error(`Unsupported op for this reference: ${op.op}`);
    }
  }

  return target;
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

  if (typeof from !== typeof to || (Array.isArray(from) !== Array.isArray(to))) {
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
  const toObj   = to   as Record<string, unknown>;
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
