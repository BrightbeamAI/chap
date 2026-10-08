/**
 * Two input edges, answered alike by both references.
 *
 * `audit.read` reports `next_seq` as the next entry to read. A `to_seq` past
 * the end of the log was returned as is, so a reader paging forward from it
 * skipped every entry written after its read.
 *
 * Under required signatures the key is chosen by the `ts` the sender gives.
 * A `ts` that is not a string gives no time: TypeScript chose a key anyway and
 * Python raised out of `dispatch`. Both now refuse it with `-32070`.
 *
 * Mirrors packages/coordinator-py/tests/test_read_paging_and_signed_ts.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.js";

test("audit.read never reports a next_seq past the end of the log", () => {
  const c = new Coordinator({ deterministicIds: true, deterministicClock: true });
  const send = (method: string, params: Record<string, unknown>) =>
    c.dispatch({ jsonrpc: "2.0", id: method, method, params: { workspace: "w", ...params } }) as any;
  send("workspace.create", { profiles: ["core/1.0"] });
  send("participant.join", { from: "human:a", type: "human" });
  send("participant.join", { from: "agent:b", type: "agent" });
  const length = () => c.workspaces.get("w")!.audit.length;

  const past = send("audit.read", { range: { from_seq: 0, to_seq: 1000 } }).result;
  assert.equal(past.next_seq, length());
  assert.equal(past.entries.length, length());

  const within = send("audit.read", { range: { from_seq: 0, to_seq: 1 } }).result;
  assert.equal(within.next_seq, 1);
  assert.equal(send("audit.read", {}).result.next_seq, length());

  // A reader that pages on from next_seq meets the entry written after it.
  send("task.create", { from: "human:a", kind: "k", input: {}, assignee: "agent:b" });
  const next = send("audit.read", { range: { from_seq: past.next_seq } }).result;
  assert.equal(next.entries.length, 1);
  assert.equal(next.entries[0].envelope.method, "task.create");
});

test("a signed call whose ts is not a string is refused with -32070", () => {
  const c = new Coordinator({ requireSignatures: true, deterministicIds: true, deterministicClock: true });
  const send = (params: Record<string, unknown>, sig?: string) =>
    c.dispatch({ jsonrpc: "2.0", id: "x", method: "task.create", ...(sig ? { sig } : {}),
                 params: { workspace: "w", from: "human:a", kind: "k", input: {}, assignee: "human:a", ...params } } as never) as any;
  c.dispatch({ jsonrpc: "2.0", id: "c", method: "workspace.create", params: { workspace: "w", profiles: ["core/1.0"] } });
  c.dispatch({ jsonrpc: "2.0", id: "j", method: "participant.join", params: {
    workspace: "w", from: "human:a", type: "human",
    jwks: { keys: [{ kty: "OKP", crv: "Ed25519", kid: "k1", x: "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo" }] } } });
  const sig = "ed25519:k1:AAAA";

  for (const ts of [5, true, { at: "now" }, ["2026-01-01T00:00:00Z"]]) {
    const r = send({ ts }, sig);
    assert.equal(r.error?.code, -32070, JSON.stringify(ts));
    assert.equal(r.error?.message, "Cannot verify signature: ts must be a string");
  }
  // An empty ts is a time no key covers.
  const empty = send({ ts: "" }, sig);
  assert.equal(empty.error?.code, -32071);
  assert.equal(empty.error?.message, "No key k1 valid at  for human:a");
  // No ts at all falls back to the coordinator's clock, which finds the key,
  // and this signature then fails to verify.
  const none = send({}, sig);
  assert.equal(none.error?.code, -32070);
  assert.equal(none.error?.message, "Signature failed verification");
  assert.equal(c.workspaces.get("w")!.tasks.size, 0);
});
