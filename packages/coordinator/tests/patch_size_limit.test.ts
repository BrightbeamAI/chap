/**
 * A patch cannot grow an artefact without limit.
 *
 * The patch engine capped operations and nodes, and nothing capped size or
 * depth. Each `copy` of the root doubles the document, its strings included,
 * so fifteen of them grew a 64 KiB artefact to about 2 GiB within the node
 * cap, and a patch that puts one value inside another nests the document as
 * deep as it likes. Any reviewer could send either as a `decide.override`. The
 * engine now counts the UTF-8 bytes of the document's strings and keys, which
 * the coordinator caps at its `max_envelope_bytes`, caps its depth at 64
 * levels, as a request's is, and caps what a patch's copies carry.
 *
 * Mirrors packages/coordinator-py/tests/test_patch_size_limit.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { applyJsonPatch, PatchError, DEFAULT_MAX_PATCHED_BYTES } from "../src/patch.ts";
import { Coordinator } from "../src/coordinator.ts";
import { E } from "../src/jsonrpc.ts";
import type { JsonPatchOp } from "../src/types.ts";

/** A value `levels` deep, itself included: nest(1) is 0, nest(2) is [0]. */
function nest(levels: number): unknown {
  let v: unknown = 0;
  for (let i = 1; i < levels; i++) v = [v];
  return v;
}

/** Four 20-level values, each put in at the innermost point of the one before: 78 levels. */
function stacked(): JsonPatchOp[] {
  const ops: JsonPatchOp[] = [];
  let path = "/a";
  for (let i = 0; i < 4; i++) {
    ops.push({ op: "add", path, value: nest(20) });
    path += "/0".repeat(19);
  }
  return ops;
}

test("the limit counts the UTF-8 bytes of strings and keys", () => {
  // é is 2 bytes, each € 3, the emoji 4: 2 + 30 + 1 + 4 = 37, and the copy adds 1 + 30.
  const doc = { "é": "€".repeat(10), e: "😀" };
  const op = [{ op: "copy" as const, from: "/é", path: "/k" }];
  assert.deepEqual(applyJsonPatch(doc, op, { maxStringBytes: 68 }), { ...doc, k: "€".repeat(10) });
  assert.throws(() => applyJsonPatch(doc, op, { maxStringBytes: 67 }), PatchError);
});

test("the default is the default envelope limit", () => {
  assert.equal(DEFAULT_MAX_PATCHED_BYTES, 1_048_576);
});

test("fifteen copies of a 64 KiB artefact are refused", () => {
  const ops = Array.from({ length: 15 }, (_, i) => ({ op: "copy" as const, from: "", path: `/c${i + 1}` }));
  assert.throws(() => applyJsonPatch({ s: "x".repeat(65_536) }, ops), /exceeds the size limit/);
});

test("a copy counts what it copies, where inserting into the array moves its source", () => {
  // Each copy reads the last element and inserts it at the front, which moves
  // that element along the array. Sixty copies of 64 KiB come to about 4 MB.
  const ops = Array.from({ length: 60 }, (_, i) => ({ op: "copy" as const, from: `/a/${i + 1}`, path: "/a/0" }));
  assert.throws(() => applyJsonPatch({ a: ["s", "x".repeat(65_536)] }, ops), /exceeds the size limit/);
});

// Each case is refused one byte below the size of its result, and applies at it.
const EXACT: Array<[string, unknown, JsonPatchOp[], number]> = [
  ["a replaced value", { t: "" }, [{ op: "replace", path: "/t", value: "yyyy" }], 5],
  ["a copy at its source", { a: "xxxx", b: "" }, [{ op: "copy", from: "/a", path: "/b" }], 10],
  ["an added key", {}, [{ op: "add", path: "/abcd", value: 0 }], 4],
  ["a moved key", { a: 0 }, [{ op: "move", from: "/a", path: "/bcd" }], 3],
  ["a key in UTF-8 bytes", {}, [{ op: "add", path: "/€", value: 0 }], 3],
];
for (const [name, doc, ops, size] of EXACT) {
  test(`the limit counts ${name} exactly`, () => {
    applyJsonPatch(doc, ops, { maxStringBytes: size });
    assert.throws(() => applyJsonPatch(doc, ops, { maxStringBytes: size - 1 }), /exceeds the size limit/);
  });
}

test("a document that passes the limit part way is refused, even if a later operation shrinks it", () => {
  const ops = [{ op: "copy" as const, from: "/s", path: "/t" }, { op: "remove" as const, path: "/t" }];
  assert.throws(() => applyJsonPatch({ s: "x".repeat(600_000) }, ops), /exceeds the size limit/);
});

test("a long patch of small edits to a large artefact applies", () => {
  const ops = Array.from({ length: 1000 }, (_, i) => ({ op: "replace" as const, path: "/n", value: i }));
  const out = applyJsonPatch({ s: "x".repeat(1_000_000), n: 0 }, ops) as { n: number };
  assert.equal(out.n, 999);
});

test("an operation's value goes in as a copy, so the patch stays as it was sent", () => {
  const ops: JsonPatchOp[] = [{ op: "add", path: "/x", value: [] }, { op: "add", path: "/x/-", value: 1 }];
  assert.deepEqual(applyJsonPatch({}, ops), { x: [1] });
  assert.deepEqual(ops[0], { op: "add", path: "/x", value: [] });
});

test("a patched document may nest 64 levels, counting itself as one, and no more", () => {
  assert.deepEqual(applyJsonPatch({}, [{ op: "add", path: "/a", value: nest(63) }]), { a: nest(63) });
  assert.throws(() => applyJsonPatch({}, [{ op: "add", path: "/a", value: nest(64) }]), /nests deeper than 64 levels/);
});

test("values that each sit within the limit cannot be stacked past it", () => {
  const ops = stacked();
  applyJsonPatch({}, ops.slice(0, 3));
  assert.throws(() => applyJsonPatch({}, ops), /nests deeper than 64 levels/);
});

test("a move or a copy cannot carry a value past the limit", () => {
  // /a reaches 41 levels, and the place under /b is 31 segments down.
  const doc = { a: nest(40), b: nest(31) };
  const deep = "/b" + "/0".repeat(30);
  for (const op of ["move", "copy"] as const) {
    assert.throws(() => applyJsonPatch(doc, [{ op, from: "/a", path: deep }]), /nests deeper than 64 levels/, op);
    applyJsonPatch(doc, [{ op, from: "/a", path: "/c" }]);
  }
});

// A small seeded generator, so a failure names a case that can be run again.
function generator(seed: number) {
  let s = seed >>> 0;
  const next = () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const int = (lo: number, hi: number) => lo + Math.floor(next() * (hi - lo + 1));
  const pick = <T>(xs: T[]): T => xs[int(0, xs.length - 1)];
  const text = (n: number) => Array.from({ length: int(0, n) }, () => pick(["a", "é", "€", "😀", "/", "~"])).join("");
  const value = (depth = 0): unknown => {
    const k = next();
    if (depth > 2 || k < 0.35) return pick<unknown>([text(10), int(0, 9), true, null]);
    if (k < 0.65) return Array.from({ length: int(0, 3) }, () => value(depth + 1));
    return Object.fromEntries(Array.from({ length: int(0, 3) }, () => [text(3), value(depth + 1)]));
  };
  return { next, int, pick, text, value };
}

const escape = (k: string) => k.replace(/~/g, "~0").replace(/\//g, "~1");

function pointers(doc: unknown, base = ""): string[] {
  const out = [base];
  if (Array.isArray(doc)) doc.forEach((v, i) => out.push(...pointers(v, `${base}/${i}`)));
  else if (doc !== null && typeof doc === "object") {
    for (const [k, v] of Object.entries(doc)) out.push(...pointers(v, `${base}/${escape(k)}`));
  }
  return out;
}

/** The value at a JSON Pointer. */
function valueAt(doc: unknown, pointer: string): unknown {
  let v: any = doc;
  for (const token of pointer === "" ? [] : pointer.slice(1).split("/")) {
    v = v[token.replace(/~1/g, "/").replace(/~0/g, "~")];
  }
  return v;
}

const segments = (pointer: string) => (pointer === "" ? 0 : pointer.split("/").length - 1);

/** The document's string and key bytes, measured whole. */
function textBytes(v: unknown): number {
  if (typeof v === "string") return Buffer.byteLength(v, "utf8");
  if (Array.isArray(v)) return v.reduce((n: number, x) => n + textBytes(x), 0);
  if (v !== null && typeof v === "object") {
    return Object.entries(v).reduce((n, [k, x]) => n + Buffer.byteLength(k, "utf8") + textBytes(x), 0);
  }
  return 0;
}

function outcome(f: () => unknown): string {
  try { return JSON.stringify(["ok", f()]); } catch (e) { return JSON.stringify(["error", (e as Error).message]); }
}

test("the size kept by each operation agrees with measuring the whole document after it", () => {
  const g = generator(20261029);
  for (let i = 0; i < 1500; i++) {
    const doc = Object.fromEntries(Array.from({ length: g.int(1, 4) }, () => [g.text(3), g.value()]));
    let current: unknown = doc;
    const ops: JsonPatchOp[] = [];
    for (let j = g.int(1, 12); j > 0; j--) {
      const at = pointers(current);
      const kind = g.pick(["add", "replace", "remove", "move", "copy", "copy", "test"] as const);
      const where = g.next() < 0.7 ? `${g.pick(at)}/${g.pick(["0", "-", escape(g.text(3))])}` : g.pick(at);
      const op: JsonPatchOp = kind === "move" || kind === "copy" ? { op: kind, from: g.pick(at), path: where }
        : kind === "remove" ? { op: kind, path: g.pick(at) }
        : { op: kind, path: kind === "add" ? where : g.pick(at), value: g.value() };
      ops.push(op);
      try { current = applyJsonPatch(current, [op], { maxStringBytes: Infinity }); } catch { /* the op is refused */ }
    }
    const limit = g.int(5, 160);
    const reference = () => {
      let out: unknown = doc;
      let carried = 0;
      for (const op of ops) {
        const before = out;
        out = applyJsonPatch(out, [op], { maxStringBytes: Infinity });
        if (textBytes(out) > limit) {
          throw new PatchError(`Patched document exceeds the size limit (${limit} bytes of strings and keys)`);
        }
        // A copy, and a move to a deeper place or onto the whole document, carries its value.
        if (op.op === "copy" || (op.op === "move" && (segments(op.path) > segments(op.from!) || op.path === ""))) {
          carried += textBytes(valueAt(before, op.from!));
        }
        if (carried > 3 * limit) {
          throw new PatchError(`Patch copies or moves more than its limit (300000 values or ${3 * limit} bytes of strings and keys)`);
        }
      }
      return out;
    };
    assert.equal(outcome(() => applyJsonPatch(doc, ops, { maxStringBytes: limit })), outcome(reference),
                 JSON.stringify({ case: i, doc, ops, limit }));
  }
});

test("a patch's copies, and its moves to a deeper place, carry at most three times the document's limits", () => {
  // 1,001 values copied onto themselves: 299 copies carry 299,299 values, and 300 carry 300,300.
  const doc = { a: Array(1000).fill(0) };
  const onto = (n: number): JsonPatchOp[] => Array.from({ length: n }, () => ({ op: "copy", from: "/a", path: "/a" }));
  applyJsonPatch(doc, onto(299));
  assert.throws(() => applyJsonPatch(doc, onto(300)),
                /Patch copies or moves more than its limit \(300000 values or 3145728 bytes of strings and keys\)$/);
  // 99 bytes copied onto themselves, against a limit of 100: three carry 297 bytes, and four 396.
  const text = { s: "x".repeat(99) };
  const again = (n: number): JsonPatchOp[] => Array.from({ length: n }, () => ({ op: "copy", from: "/s", path: "/s" }));
  applyJsonPatch(text, again(3), { maxStringBytes: 100 });
  assert.throws(() => applyJsonPatch(text, again(4), { maxStringBytes: 100 }), /300 bytes of strings and keys/);
  // A copy onto the whole document carries the document.
  const root = (n: number): JsonPatchOp[] => Array.from({ length: n }, () => ({ op: "copy", from: "", path: "" }));
  applyJsonPatch(Array(1000).fill(0), root(299));
  assert.throws(() => applyJsonPatch(Array(1000).fill(0), root(300)), /copies or moves more than its limit/);
  // A move onto the whole document carries the value that becomes it: about 99,915 values each time.
  let wrapped: unknown = Array(99_900).fill(0);
  for (let i = 0; i < 15; i++) wrapped = { a: wrapped };
  const up = (n: number): JsonPatchOp[] => Array.from({ length: n }, () => ({ op: "move", from: "/a", path: "" }));
  applyJsonPatch(wrapped, up(3));
  assert.throws(() => applyJsonPatch(wrapped, up(4)), /copies or moves more than its limit/);
  // A move to a deeper place carries its 2,501 values, and a move back costs nothing.
  const big = { a: Array(2500).fill(0), d: {} };
  const moves = (n: number): JsonPatchOp[] => Array.from({ length: n }, (_, i) =>
    i % 2 === 0 ? { op: "move", from: "/a", path: "/d/a" } : { op: "move", from: "/d/a", path: "/a" });
  applyJsonPatch(big, moves(237));
  assert.throws(() => applyJsonPatch(big, moves(239)), /copies or moves more than its limit/);
});

test("the value count is exact at the limit", () => {
  // 1 for the document, 1 for the array and 99,998 zeros: 100,000 values.
  const doc = { a: Array(99_998).fill(0) };
  const nodeLimit = /exceeds the node limit \(100000\)/;
  assert.throws(() => applyJsonPatch(doc, [{ op: "add", path: "/b", value: 0 }]), nodeLimit);
  applyJsonPatch(doc, [{ op: "remove", path: "/a/0" }, { op: "add", path: "/b", value: 0 }]);
  assert.throws(() => applyJsonPatch(doc, [{ op: "replace", path: "/a/0", value: [0] }]), nodeLimit);
  applyJsonPatch(doc, [{ op: "replace", path: "/a", value: 0 }, { op: "add", path: "/b", value: Array(99_997).fill(0) }]);
  applyJsonPatch(doc, [{ op: "move", from: "/a", path: "/b" }]);
  assert.throws(() => applyJsonPatch(doc, [{ op: "move", from: "/a", path: "/b" }, { op: "add", path: "/c", value: 0 }]), nodeLimit);
});

test("the limits are checked in a fixed order: values, then size, then depth", () => {
  const doc = { a: Array(99_998).fill(0) };
  assert.throws(() => applyJsonPatch(doc, [{ op: "add", path: "/b", value: "xxxxxxxx" }], { maxStringBytes: 2 }),
                /exceeds the node limit/);
  let deep: unknown = "x".repeat(100);
  for (let i = 0; i < 70; i++) deep = [deep];
  assert.throws(() => applyJsonPatch({}, [{ op: "add", path: "/a", value: deep }], { maxStringBytes: 10 }),
                /exceeds the size limit/);
});

test("a document that starts deeper than the limit is refused unless the patch removes the depth", () => {
  const doc = { a: nest(70) };
  assert.deepEqual(applyJsonPatch(doc, [{ op: "remove", path: "/a" }]), {});
  assert.throws(() => applyJsonPatch(doc, [{ op: "test", path: "/a", value: nest(70) }]), /nests deeper than 64 levels/);
});

test("where there is no Buffer, the module counts UTF-8 bytes the same way", () => {
  const samples = ["a", "é", "€", "😀", "a😀b", "\ud800", "\udc00", "x\ud83d", "\ud83dx", "\ude00\ud83d"];
  const sizes = samples.map((text) => 1 + Buffer.byteLength(text, "utf8"));
  const global = globalThis as { Buffer?: unknown };
  const saved = global.Buffer;
  const outcomes: string[] = [];
  global.Buffer = undefined;
  try {
    samples.forEach((text, i) => {
      const op: JsonPatchOp[] = [{ op: "add", path: "/k", value: text }];
      outcomes.push(outcome(() => applyJsonPatch({}, op, { maxStringBytes: sizes[i] })));
      outcomes.push(outcome(() => applyJsonPatch({}, op, { maxStringBytes: sizes[i] - 1 })));
    });
  } finally {
    global.Buffer = saved;
  }
  samples.forEach((text, i) => {
    assert.match(outcomes[2 * i], /^\["ok"/, JSON.stringify(text));
    assert.match(outcomes[2 * i + 1], /exceeds the size limit/, JSON.stringify(text));
  });
});

function overrideOn(c: Coordinator, diff: unknown[] = [1, 2, 3].map((i) => ({ op: "copy", from: "", path: `/c${i}` }))) {
  const send = (method: string, params: Record<string, unknown> = {}, actor = "agent:b") =>
    c.dispatch({ jsonrpc: "2.0", id: method, method,
                 params: { workspace: "w", from: actor, ...params } }) as any;
  send("workspace.create", { profiles: ["core/1.0", "review/1.0"] });
  for (const [uri, type] of [["human:a", "human"], ["agent:b", "agent"]])
    send("participant.join", { type }, uri);
  const id = send("task.create", { kind: "k", input: {}, assignee: "agent:b" }).result.task_id;
  send("review.request", { task_id: id, to: ["human:a"], artefact: { s: "x".repeat(1000) } });
  return { id, send, reply: send("decide.override", { task_id: id, diff, rationale: "r" }, "human:a") };
}

test("the coordinator caps an override at its max_envelope_bytes", () => {
  const c = new Coordinator({ deterministicIds: true, maxEnvelopeBytes: 4096 });
  const { id, reply } = overrideOn(c);
  assert.equal(reply.error?.code, E.PATCH_FAILED);
  assert.match(reply.error.message, /size limit \(4096 bytes/);
  assert.equal((c as any).workspaces.get("w").tasks.get(id).state, "review_requested");
});

test("the same override applies under the default limit", () => {
  const c = new Coordinator({ deterministicIds: true });
  const { id, reply } = overrideOn(c);
  assert.equal(reply.error, undefined, JSON.stringify(reply.error));
  assert.equal((c as any).workspaces.get("w").tasks.get(id).state, "completed");
});

test("the coordinator refuses an override that nests the artefact too deep, and keeps the task as it was", () => {
  const c = new Coordinator({ deterministicIds: true });
  const { id, reply } = overrideOn(c, stacked());
  assert.equal(reply.error?.code, E.PATCH_FAILED);
  assert.match(reply.error.message, /nests deeper than 64 levels/);
  const task = (c as any).workspaces.get("w").tasks.get(id);
  assert.equal(task.state, "review_requested");
  assert.equal(task.output, undefined);
});
