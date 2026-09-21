/**
 * The lifecycle table in SPECIFICATION.md §8.1 is checked against the coordinator.
 *
 * The table is normative and nothing enforced it, so it described
 * `task.assign`, `task.accept` and `task.start`, three methods no coordinator
 * implements, and two states (`assigned`, `accepted`) not in `TaskState`.
 *
 * This test reads it. It drives a task into every state, attempts every
 * state-changing method from each, and requires the result to match the table
 * exactly.
 *
 * Both columns are read. The From column says where a method may be called,
 * and the To column says where it leaves the task. A state named in a To cell
 * must be one the method actually produces, and a state a method produces must
 * be named. Rows whose outcome depends on more than the starting state, a
 * review rule not yet satisfied, a rejection sent back for revision, the state
 * a pause captured, are driven by outcomeScenarios below.
 *
 * Mirrors packages/coordinator-py/tests/test_spec_lifecycle_table.py, so the
 * two implementations and the specification move together or not at all.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { Coordinator } from "../src/coordinator.js";

const SPEC = resolve(dirname(fileURLToPath(import.meta.url)), "../../../SPECIFICATION.md");
const PROFILES = ["core/1.0", "review/1.0", "control/1.0"];
const AGENT = "agent:b", HUMAN = "human:a", OTHER = "human:c";
const ARTEFACT = { text: "draft" };

const STATES = ["created", "in_progress", "review_requested", "completed", "declined",
                "abstained", "escalated", "paused", "cancelled", "superseded"];

const METHODS = ["task.complete", "review.request", "decide.approve", "decide.reject",
                 "decide.override", "abstain.declare", "escalate.raise",
                 "control.pause", "control.resume", "control.cancel", "control.supersede"];

// ------------------------------------------------------------------ the table

function tableRows(): [string, string, string][] {
  const text = readFileSync(SPEC, "utf-8");
  const section = text.slice(text.indexOf("### 8.1 Lifecycle"), text.indexOf("### 8.2"));
  const rows: [string, string, string][] = [];
  for (const line of section.split("\n")) {
    const m = /^\| (.+?) \| (.+?) \| (.+?) \|$/.exec(line);
    if (!m) continue;
    if (m[1] === "From" || /^[-| ]+$/.test(m[1])) continue;
    rows.push([m[1], m[2], m[3]]);
  }
  return rows;
}

/**
 * The states a To cell names, whatever prose surrounds them. A cell may carry
 * an explanation beside the outcome, as "completed once the review rule is
 * satisfied, otherwise review_requested" does. What is normative is which
 * states it names.
 */
function statesNamedIn(cell: string): string[] {
  return STATES.filter(s => new RegExp(`\\b${s}\\b`).test(cell));
}

function specMachine() {
  const permitted = new Map<string, Set<string>>();
  const updateTargets = new Map<string, Set<string>>();
  const outcomes = new Map<string, Set<string>>();
  for (const [froms, methodCell, toCell] of tableRows()) {
    const method = /`([a-z_]+\.[a-z_]+)`/.exec(methodCell)![1];
    const refused = toCell.trim().startsWith("refused");
    const states = froms.trim() === "any state" ? STATES : froms.split(",").map(s => s.trim());
    for (const state of states) {
      assert.ok(STATES.includes(state), `§8.1 names an unknown state: ${state}`);
      if (refused) {
        // A refusal row documents a precondition. Its To cell names an
        // error, not a reachable state, so it contributes nothing to
        // either map.
        continue;
      }
      if (method === "task.update") {
        const set = updateTargets.get(state) ?? new Set<string>();
        for (const t of toCell.split(",")) set.add(t.trim());
        updateTargets.set(state, set);
      } else {
        permitted.set(method, (permitted.get(method) ?? new Set()).add(state));
        const named = statesNamedIn(toCell);
        assert.ok(named.length > 0,
                  `§8.1 gives ${method} a To cell that names no state: ${toCell}`);
        const set = outcomes.get(method) ?? new Set<string>();
        for (const n of named) set.add(n);
        outcomes.set(method, set);
      }
    }
  }
  return { permitted, updateTargets, outcomes };
}

// ------------------------------------------------------------ the coordinator

function ready() {
  const c = new Coordinator({ defaultProfiles: PROFILES, deterministicIds: true });
  const send = (method: string, params: Record<string, unknown> = {}, actor = AGENT) =>
    c.dispatch({ jsonrpc: "2.0", id: method, method,
                 params: { workspace: "w", from: actor, ...params } }) as any;
  send("workspace.create", { profiles: PROFILES });
  for (const [uri, type] of [[HUMAN, "human"], [OTHER, "human"], [AGENT, "agent"]]) {
    send("participant.join", { type }, uri);
  }
  return { c, send };
}

function drive(send: any, state: string): string {
  const id = send("task.create", { kind: "k", input: {}, assignee: AGENT }).result.task_id;
  const rr = () => send("review.request", { task_id: id, artefact: ARTEFACT, to: HUMAN });
  switch (state) {
    case "created": break;
    case "in_progress": send("task.update", { task_id: id, state: "in_progress" }); break;
    case "review_requested": rr(); break;
    case "completed":
      rr(); send("decide.approve", { task_id: id, comment: "ok", rationale: "ok" }, HUMAN); break;
    case "declined":
      rr(); send("decide.reject", { task_id: id, comment: "no", rationale: "no" }, HUMAN); break;
    case "abstained":
      rr(); send("abstain.declare", { task_id: id, reason: "conflict of interest" }, HUMAN); break;
    case "escalated":
      send("escalate.raise", { original_task_id: id, reason: "above me",
        new_task: { kind: "k", input: {}, assignee: AGENT } }, HUMAN); break;
    case "paused": send("control.pause", { task_id: id, reason: "hold" }, HUMAN); break;
    case "cancelled": send("control.cancel", { task_id: id, reason: "not needed" }, HUMAN); break;
    case "superseded":
      send("control.supersede", { task_id: id, reason: "redone",
        successor_task: { kind: "k", input: {}, assignee: AGENT } }, HUMAN); break;
    default: throw new Error(`no recipe for ${state}`);
  }
  return id;
}

function attempt(send: any, method: string, id: string) {
  switch (method) {
    case "task.complete": return send("task.complete", { task_id: id, output: ARTEFACT });
    case "review.request": return send("review.request", { task_id: id, artefact: ARTEFACT, to: HUMAN });
    case "decide.approve": return send("decide.approve", { task_id: id, comment: "ok", rationale: "ok" }, HUMAN);
    case "decide.reject": return send("decide.reject", { task_id: id, comment: "no", rationale: "no" }, HUMAN);
    case "decide.override": return send("decide.override", { task_id: id, comment: "fix", rationale: "fix",
      diff: [{ op: "replace", path: "/text", value: "x" }] }, HUMAN);
    case "abstain.declare": return send("abstain.declare", { task_id: id, reason: "conflict of interest" }, HUMAN);
    case "escalate.raise": return send("escalate.raise", { original_task_id: id, reason: "above me",
      new_task: { kind: "k", input: {}, assignee: AGENT } }, HUMAN);
    case "control.pause": return send("control.pause", { task_id: id, reason: "hold" }, HUMAN);
    case "control.resume": return send("control.resume", { task_id: id, reason: "carry on" }, HUMAN);
    case "control.cancel": return send("control.cancel", { task_id: id, reason: "stop" }, HUMAN);
    case "control.supersede": return send("control.supersede", { task_id: id, reason: "redone",
      successor_task: { kind: "k", input: {}, assignee: AGENT } }, HUMAN);
    default: throw new Error(`no attempt for ${method}`);
  }
}

/**
 * Outcomes a starting state alone does not settle: a review rule not yet
 * satisfied, a rejection sent back for revision, the state a pause captured.
 * Each scenario drives itself and names the method it exercised, so the state
 * the task lands in is attributed to that method.
 */
function outcomeScenarios(): ((send: any) => [string, string])[] {
  const resumeFrom = (origin: string) => (send: any): [string, string] => {
    const id = drive(send, origin);
    send("control.pause", { task_id: id, reason: "hold" }, HUMAN);
    send("control.resume", { task_id: id }, HUMAN);
    return ["control.resume", id];
  };
  return [
    (send): [string, string] => {
      const id = send("task.create", { kind: "k", input: {}, assignee: AGENT }).result.task_id;
      send("review.request", { task_id: id, artefact: ARTEFACT, to: [HUMAN, OTHER],
                               rule: "quorum:2" });
      send("decide.approve", { task_id: id, comment: "ok", rationale: "ok" }, HUMAN);
      return ["decide.approve", id];
    },
    (send): [string, string] => {
      const id = send("task.create", { kind: "k", input: {}, assignee: AGENT }).result.task_id;
      send("review.request", { task_id: id, artefact: ARTEFACT, to: HUMAN });
      send("decide.reject", { task_id: id, comment: "no", rationale: "no",
                              request_revision: true }, HUMAN);
      return ["decide.reject", id];
    },
    (send): [string, string] => {
      const id = send("task.create", { kind: "k", input: {}, assignee: AGENT,
                                       review_required: true }).result.task_id;
      send("task.complete", { task_id: id, output: ARTEFACT });
      return ["task.complete", id];
    },
    // control.pause names the states a pause can be entered from, so those are
    // the states a resume can restore.
    ...["created", "in_progress", "review_requested", "abstained", "escalated"]
      .map(origin => resumeFrom(origin)),
  ];
}

function implementedMachine() {
  const permitted = new Map<string, Set<string>>();
  const updateTargets = new Map<string, Set<string>>();
  const outcomes = new Map<string, Set<string>>();
  for (const state of STATES) {
    {
      const { c, send } = ready();
      const id = drive(send, state);
      assert.equal((c as any).workspaces.get("w").tasks.get(id).state, state,
                   `the fixture for ${state} did not reach it`);
    }
    for (const method of METHODS) {
      const { c, send } = ready();
      const id = drive(send, state);
      if (attempt(send, method, id).error === undefined) {
        permitted.set(method, (permitted.get(method) ?? new Set()).add(state));
        outcomes.set(method, (outcomes.get(method) ?? new Set())
          .add((c as any).workspaces.get("w").tasks.get(id).state));
      }
    }
    for (const target of STATES) {
      const { send } = ready();
      const id = drive(send, state);
      if (send("task.update", { task_id: id, state: target }).error === undefined) {
        updateTargets.set(state, (updateTargets.get(state) ?? new Set()).add(target));
      }
    }
  }
  for (const scenario of outcomeScenarios()) {
    const { c, send } = ready();
    const [method, id] = scenario(send);
    outcomes.set(method, (outcomes.get(method) ?? new Set())
      .add((c as any).workspaces.get("w").tasks.get(id).state));
  }
  return { permitted, updateTargets, outcomes };
}

const spec = specMachine();
const real = implementedMachine();
const sorted = (s: Set<string> | undefined) => [...(s ?? [])].sort();

// ------------------------------------------------------------------ the check

test("the table is not empty", () => {
  // Without this the comparisons below would pass by having nothing to compare.
  assert.ok(spec.permitted.size >= 8, "SPECIFICATION.md 8.1 parsed to almost nothing");
  assert.ok(spec.updateTargets.size > 0, "no task.update rows found in 8.1");
  assert.ok(spec.outcomes.size >= 8, "the To column of 8.1 parsed to almost nothing");
});

for (const method of METHODS) {
  test(`the table matches the coordinator for ${method}`, () => {
    const documented = sorted(spec.permitted.get(method));
    const actual = sorted(real.permitted.get(method));
    assert.deepEqual(documented, actual,
      `SPECIFICATION.md 8.1 and the coordinator disagree about ${method}.\n` +
      `  the table permits it from : ${documented.join(", ") || "nothing"}\n` +
      `  the coordinator permits it: ${actual.join(", ") || "nothing"}`);
  });
}

for (const method of METHODS) {
  test(`the To column matches where ${method} leaves the task`, () => {
    const named = sorted(spec.outcomes.get(method));
    const reached = sorted(real.outcomes.get(method));
    assert.deepEqual(named, reached,
      `SPECIFICATION.md 8.1 and the coordinator disagree about where ${method} ` +
      `leaves the task.\n` +
      `  the To column names    : ${named.join(", ") || "nothing"}\n` +
      `  the coordinator reaches: ${reached.join(", ") || "nothing"}`);
  });
}

for (const state of STATES) {
  test(`the task.update row for ${state} matches the transition map`, () => {
    assert.deepEqual(sorted(spec.updateTargets.get(state)), sorted(real.updateTargets.get(state)),
      `SPECIFICATION.md 8.1 and task.update disagree about ${state}`);
  });
}

test("the table names no method the coordinator lacks", () => {
  // The table named task.assign, task.accept and task.start, which no
  // coordinator implements. A named method must be dispatchable.
  const { c } = ready();
  const named = new Set(tableRows().map(([, cell]) => /`([a-z_]+\.[a-z_]+)`/.exec(cell)![1]));
  for (const method of [...named].sort()) {
    const r = c.dispatch({ jsonrpc: "2.0", id: "x", method, params: {} }) as any;
    assert.notEqual(r.error?.code, -32601,
      `SPECIFICATION.md 8.1 names ${method}, which the coordinator does not implement`);
  }
});
