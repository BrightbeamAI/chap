/**
 * Typed method facade tests.
 *
 * Verifies that:
 *   - `coord.api.workspace.create({...})` etc. work as drop-in replacements
 *     for the dispatch pattern.
 *   - Audit chain semantics are identical regardless of which API is used.
 *   - Dispatch errors surface as CoordinatorError exceptions with code and message.
 *   - The standalone `call()` helper works without going through the facade.
 */

import { test, describe } from "node:test";
import assert from "node:assert/strict";

import { Coordinator } from "../src/coordinator.js";
import { call, CoordinatorError } from "../src/api.js";

describe("typed facade — basic operations", () => {
  test("workspace.create returns workspace id", () => {
    const c = new Coordinator();
    const r = c.api.workspace.create({ workspace: "wsp_facade" });
    assert.equal(r.workspace, "wsp_facade");
  });

  test("participant.join + task.create + decide.override end-to-end", () => {
    const c = new Coordinator();
    c.api.workspace.create({
      workspace: "wsp_e2e",
      profiles: ["core/1.0", "review/1.0"],
    });
    c.api.participant.join({
      workspace: "wsp_e2e",
      from: "human:alice@example.org",
      type: "human",
    });
    c.api.participant.join({
      workspace: "wsp_e2e",
      from: "agent:bot#v1",
      type: "agent",
    });
    c.api.participant.join({
      workspace: "wsp_e2e",
      from: "human:reviewer@example.org",
      type: "human",
    });
    const { task_id } = c.api.task.create({
      workspace: "wsp_e2e",
      from: "human:alice@example.org",
      assignee: "agent:bot#v1",
      kind: "draft",
      input: { ticket: "TKT-1" },
    });
    c.api.task.complete({
      workspace: "wsp_e2e",
      from: "agent:bot#v1",
      task_id,
      output: { reply: "draft reply" },
    });
    c.api.review.request({
      workspace: "wsp_e2e",
      from: "agent:bot#v1",
      task_id,
      artefact: { reply: "draft reply" },
      to: "human:reviewer@example.org",
    });
    const ov = c.api.decide.override({
      workspace: "wsp_e2e",
      from: "human:reviewer@example.org",
      task_id,
      diff: [{ op: "replace", path: "/reply", value: "improved reply" }],
      rationale: "Improved tone.",
      tags: ["tone-warmed"],
    });
    assert.ok(ov.override_artefact_id);
    assert.equal(ov.state, "completed");
    const result = ov.applied as { reply: string };
    assert.equal(result.reply, "improved reply");
  });
});

describe("typed facade — audit chain equivalence", () => {
  test("dispatch and api produce equivalent audit entries", () => {
    // Path A: dispatch directly
    const c1 = new Coordinator({ deterministicIds: true, deterministicClock: true });
    c1.dispatch({
      jsonrpc: "2.0", id: "1",
      method: "workspace.create",
      params: { workspace: "wsp_path_a" },
    });
    c1.dispatch({
      jsonrpc: "2.0", id: "2",
      method: "participant.join",
      params: { workspace: "wsp_path_a", from: "human:me", type: "human" },
    });

    // Path B: typed facade
    const c2 = new Coordinator({ deterministicIds: true, deterministicClock: true });
    c2.api.workspace.create({ workspace: "wsp_path_b" });
    c2.api.participant.join({ workspace: "wsp_path_b", from: "human:me", type: "human" });

    // The audit entries should have matching methods/params (ids differ
    // because envelope.id is generated, but everything else aligns).
    const a = c1.workspaces.get("wsp_path_a")!.audit;
    const b = c2.workspaces.get("wsp_path_b")!.audit;
    assert.equal(a.length, b.length);
    for (let i = 0; i < a.length; i++) {
      assert.equal(a[i].envelope.method, b[i].envelope.method);
      const ap = (a[i].envelope.params as Record<string, unknown>);
      const bp = (b[i].envelope.params as Record<string, unknown>);
      // Workspace ids differ but other fields are identical.
      delete ap.workspace; delete bp.workspace;
      assert.deepEqual(ap, bp);
    }
  });
});

describe("typed facade — error handling", () => {
  test("unknown workspace throws CoordinatorError with code", () => {
    const c = new Coordinator();
    assert.throws(
      () => c.api.task.create({
        workspace: "wsp_nope",
        from: "human:me",
        kind: "draft",
        input: {},
      }),
      (e: unknown) => {
        assert.ok(e instanceof CoordinatorError);
        assert.equal((e as CoordinatorError).code, -32602); // PARAMS
        return true;
      },
    );
  });

  test("missing required field throws with field name", () => {
    const c = new Coordinator();
    c.api.workspace.create({ workspace: "wsp_e" });
    assert.throws(
      // @ts-expect-error — deliberately missing `type`
      () => c.api.participant.join({ workspace: "wsp_e", from: "human:x" }),
      /Missing field: type/,
    );
  });
});

describe("typed facade — standalone call() helper", () => {
  test("call() works without the namespace facade", () => {
    const c = new Coordinator();
    const r = call(c, "workspace.create", { workspace: "wsp_call" });
    assert.equal(r.workspace, "wsp_call");
  });
});
