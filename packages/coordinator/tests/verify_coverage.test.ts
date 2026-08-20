/**
 * Coverage-reporting tests for audit.verify_chain (issue #76).
 *
 * A chain replay proves the entries it covers were not altered. It proves
 * nothing about entries written before chaining was switched on, because
 * no stored hash reaches them. Before this fix the verdict for a log with
 * an unchained prefix was `ok: true` with a smaller `entries_checked`
 * beside it, so a caller reading `ok` alone took a pass over entries
 * nothing had checked.
 *
 * The three verdicts are terminal and mutually exclusive: a broken chain
 * is a JSON-RPC error, an unevaluated range is `not_evaluated` with
 * `ok: false`, and `verified` requires complete coverage. These cases are
 * mirrored one for one in tests/test_verify_coverage.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.ts";
import { ZERO_HASH } from "../src/canonical.ts";

const CHAINED = ["core/1.0", "review/1.0", "audit-scitt/1.0"];

function make(profiles: string[]) {
  const c = new Coordinator({ defaultProfiles: profiles });
  const s = (method: string, params: unknown) =>
    c.dispatch({ jsonrpc: "2.0", id: method, method, params } as never);
  s("workspace.create", { workspace: "w", profiles });
  s("participant.join", { workspace: "w", from: "human:a", type: "human", role: "admin" });
  s("participant.join", { workspace: "w", from: "agent:bot", type: "agent" });
  return { c, s };
}

/**
 * Writes one audited entry, and proves it did.
 *
 * The assertion is the point: task.create requires kind and input, and a
 * rejected call appends nothing. Without this check a wrong-shaped call
 * leaves the log short and every assertion below still passes, for the
 * wrong reason.
 */
function task(s: (m: string, p: unknown) => unknown, name: string) {
  const r = s("task.create", {
    workspace: "w", from: "human:a",
    kind: "review", input: { name }, assignee: "agent:bot",
  }) as object;
  assert.ok("result" in r, `task.create should succeed: ${JSON.stringify(r)}`);
}

/**
 * Enables chaining part-way through a workspace's life.
 *
 * This is the supported route, not a poke at internals:
 * workspace.set_profiles turns the chain on when audit-scitt/1.0 is
 * added, which is exactly how a real deployment reaches this state.
 */
function enableChainMidLife(s: (m: string, p: unknown) => unknown) {
  const r = s("workspace.set_profiles", {
    workspace: "w", from: "human:a",
    profiles: ["core/1.0", "review/1.0", "audit-scitt/1.0"],
  }) as object;
  assert.ok("result" in r, "set_profiles should succeed");
}

function verdict(s: (m: string, p: unknown) => unknown) {
  const r = s("audit.verify_chain", { workspace: "w" }) as { result?: Record<string, unknown> };
  assert.ok(r.result, "expected a result, got an error");
  return r.result as Record<string, unknown>;
}

test("a fully covered chain verifies and says so", () => {
  const { s } = make(CHAINED);
  task(s, "t1");
  task(s, "t2");
  const v = verdict(s);
  assert.equal(v.status, "verified");
  assert.equal(v.ok, true);
  assert.equal(v.entries_unchecked, 0);
  assert.equal(v.entries_checked, v.entries_total);
  assert.ok(!("reason" in v), "a pass carries no reason");
});

test("an unchained prefix is not_evaluated, never a pass", () => {
  const { c, s } = make(["core/1.0", "review/1.0"]);
  task(s, "t1");
  task(s, "t2");
  const before = (c.workspaces.get("w") as never as { audit: unknown[] }).audit.length;
  enableChainMidLife(s);
  task(s, "t3");

  const v = verdict(s);
  assert.equal(v.status, "not_evaluated");
  assert.equal(v.ok, false, "the whole point: ok must not be true here");
  assert.equal(v.reason, "unchained_prefix");
  assert.equal(v.entries_unchecked, before);
  assert.ok((v.entries_checked as number) < (v.entries_total as number));
});

test("the enabling call is itself the first checked entry", () => {
  // set_profiles is an audited call, so switching the chain on writes an
  // entry that the chain then covers. Coverage therefore always begins at
  // the enabling call, and everything before it stays outside.
  const { c, s } = make(["core/1.0", "review/1.0"]);
  task(s, "t1");
  task(s, "t2");
  const audit = (c.workspaces.get("w") as never as { audit: { seq: number }[] }).audit;
  const before = audit.length;
  enableChainMidLife(s);

  const v = verdict(s);
  assert.equal(v.status, "not_evaluated");
  assert.equal(v.ok, false);
  assert.equal(v.entries_checked, 1, "only the enabling call is covered");
  assert.equal(v.entries_unchecked, before);
  assert.equal(v.checked_from_seq, audit[before].seq);
});

test("a log with no chained entries checks nothing", () => {
  // Not reachable through the public API, since the enabling call is
  // itself chained. It is reachable by restoring a store whose chain flag
  // outlived its chained entries, so the zero-coverage verdict is pinned
  // here rather than left to chance.
  const { c, s } = make(["core/1.0", "review/1.0"]);
  task(s, "t1");
  const ws = c.workspaces.get("w") as never as { chain_enabled: boolean; chain_head: string };
  ws.chain_enabled = true;
  ws.chain_head = ZERO_HASH;

  const v = verdict(s);
  assert.equal(v.status, "not_evaluated");
  assert.equal(v.ok, false);
  assert.equal(v.entries_checked, 0);
  assert.equal(v.checked_from_seq, null, "nothing was checked, so no start seq");
  assert.equal(v.entries_unchecked, v.entries_total);
});

test("an empty chained log verifies vacuously", () => {
  // Constructed boundary: a chained workspace whose log holds nothing.
  // Normal use never reaches it, because workspace.create is itself an
  // audited call, so the emptiness has to be made deliberately. It is
  // worth pinning: an empty log has nothing to contradict its ZERO_HASH
  // head, so a pass is correct, and the counts must still add up.
  const { c, s } = make(CHAINED);
  const ws = c.workspaces.get("w") as never as { audit: unknown[]; chain_head: string };
  ws.audit.length = 0;
  ws.chain_head = ZERO_HASH;
  const v = verdict(s);
  assert.equal(v.status, "verified");
  assert.equal(v.ok, true);
  assert.equal(v.entries_total, 0);
  assert.equal(v.entries_checked, 0);
  assert.equal(v.entries_unchecked, 0);
  assert.equal(v.checked_from_seq, null);
});

test("a broken chain is still an error, not a verdict", () => {
  const { c, s } = make(CHAINED);
  task(s, "t1");
  task(s, "t2");
  (c.workspaces.get("w") as never as { audit: { envelope: { params: Record<string, unknown> } }[] })
    .audit[1].envelope.params.intent = "TAMPERED";
  const r = s("audit.verify_chain", { workspace: "w" }) as object;
  assert.ok("error" in r, "tampering must not be downgraded to not_evaluated");
});

test("coverage counts are internally consistent in every verdict", () => {
  for (const build of [
    () => { const m = make(CHAINED); task(m.s, "a"); return m; },
    () => { const m = make(["core/1.0"]); task(m.s, "a"); enableChainMidLife(m.s); task(m.s, "b"); return m; },
    () => { const m = make(["core/1.0"]); task(m.s, "a"); enableChainMidLife(m.s); return m; },
  ]) {
    const { s } = build();
    const v = verdict(s);
    assert.equal(
      (v.entries_checked as number) + (v.entries_unchecked as number),
      v.entries_total,
      "checked plus unchecked must account for the whole log",
    );
    assert.equal(v.ok, v.status === "verified", "ok and status must never disagree");
  }
});

test("a narrowing range is refused, not silently widened", () => {
  // from_seq and to_seq are declared on the params interface but no
  // implementation honours them. Answering the whole-log question while
  // the caller asked a narrower one is the same failure this method was
  // fixed to stop making, so the call is refused instead.
  const { s } = make(CHAINED);
  task(s, "t1");
  for (const narrowing of [{ from_seq: 1 }, { to_seq: 2 }, { from_seq: 1, to_seq: 2 }]) {
    const r = s("audit.verify_chain", { workspace: "w", ...narrowing }) as
      { error?: { message: string } };
    assert.ok(r.error, `${JSON.stringify(narrowing)} should be refused`);
    assert.match(r.error.message, /not implemented/);
  }
  assert.ok("result" in (s("audit.verify_chain", { workspace: "w" }) as object),
    "no range still works");
});

test("an empty string head is not treated as absent", () => {
  // Guards a TS/Python divergence: `or ZERO_HASH` and `?? ZERO_HASH`
  // disagree on "". Both must treat a present-but-empty head as a real
  // value and reach the same verdict. Only reachable through a restore.
  const { c, s } = make(CHAINED);
  task(s, "t1");
  (c.workspaces.get("w") as never as { chain_head: string }).chain_head = "";
  const r = s("audit.verify_chain", { workspace: "w" }) as { error?: { message: string } };
  assert.ok(r.error, "an empty head cannot match a real replay");
  assert.match(r.error.message, /chain_head mismatch/);
});
