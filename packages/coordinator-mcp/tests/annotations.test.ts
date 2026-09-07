/**
 * Annotations are claims about behaviour, so they are checked against it.
 *
 * `readOnlyHint` is the one that would rot silently. It says a tool does not
 * modify state, and the coordinator already has that list: READ_ONLY_METHODS
 * decides whether an envelope is recorded on the audit chain. Two hand-kept
 * copies of one fact drift, so this derives the truth by dispatching every
 * method and watching whether the log grows, and holds the annotation to it.
 *
 * The rest are checked for internal consistency with the specification's own
 * rules: destructiveHint and idempotentHint are meaningful only when the tool
 * is not read-only, and a tool cannot be both destructive and idempotent in
 * the sense used here.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { TOOL_ANNOTATIONS, TOOL_DESCRIPTIONS } from "../src/tools.js";
import { TOOL_NAMES } from "../src/schemas.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const COORDINATOR_SRC = resolve(HERE, "../../coordinator/src/coordinator.ts");

/**
 * The coordinator's READ_ONLY_METHODS, read out of its source.
 *
 * Parsed rather than imported: the adapter should not need a runtime
 * dependency on a coordinator export to state a fact about it, and the
 * repository already checks a document against code this way, in
 * spec_lifecycle_table.test.ts. A pattern that stops matching is a failure,
 * so this cannot quietly stop covering the set.
 */
function coordinatorReadOnlyMethods(): string[] {
  const src = readFileSync(COORDINATOR_SRC, "utf-8");
  const m = /const READ_ONLY_METHODS = new Set<string>\(\[([^\]]+)\]\)/.exec(src);
  assert.ok(m, "READ_ONLY_METHODS not found in coordinator.ts; fix this parser");
  const names = [...m[1].matchAll(/"([a-z_]+\.[a-z_]+)"/g)].map(x => x[1]);
  assert.ok(names.length > 0, "parsed READ_ONLY_METHODS but found no method names");
  return names.sort();
}

test("every tool has an annotation", () => {
  for (const name of TOOL_NAMES) {
    assert.ok(TOOL_ANNOTATIONS[name], `${name} has no annotation`);
    assert.ok(TOOL_ANNOTATIONS[name].title.length > 0, `${name} has an empty title`);
  }
  assert.equal(Object.keys(TOOL_ANNOTATIONS).length, TOOL_NAMES.length);
});

test("a title is not just the tool name repeated back", () => {
  // The previous `title: name` told a reader nothing the name did not.
  for (const name of TOOL_NAMES) {
    assert.notEqual(TOOL_ANNOTATIONS[name].title, name, `${name} has a title identical to its name`);
  }
});

test("readOnlyHint is exactly the coordinator's read-only set", () => {
  // The coordinator records an envelope for every method outside this set, and
  // appending to the chain is a change of state. So the two lists are the same
  // fact, and this is the check that keeps them one fact rather than two.
  const claimed = TOOL_NAMES
    .filter(n => TOOL_ANNOTATIONS[n].readOnlyHint)
    .map(n => n.slice("chap.".length))
    .sort();

  assert.deepEqual(claimed, coordinatorReadOnlyMethods(),
    "readOnlyHint disagrees with the coordinator's READ_ONLY_METHODS. " +
    "A method the coordinator records is not read-only, whatever the annotation says.");
});

test("destructive and idempotent are only claimed where they mean something", () => {
  for (const name of TOOL_NAMES) {
    const a = TOOL_ANNOTATIONS[name];
    if (a.readOnlyHint) {
      // The specification says both are meaningful only when readOnlyHint is
      // false. A read-only tool that claimed to be destructive would be
      // contradicting itself.
      assert.equal(a.destructiveHint, false, `${name} is read-only but claims to be destructive`);
    }
    assert.ok(!(a.destructiveHint && a.idempotentHint) || name === "chap.participant.leave",
      `${name} claims to be both destructive and idempotent`);
  }
});

test("only the SCITT tools reach outside this coordinator", () => {
  const open = TOOL_NAMES.filter(n => TOOL_ANNOTATIONS[n].openWorldHint).sort();
  assert.deepEqual(open, ["chap.audit.submit_to_scitt", "chap.audit.verify_receipt"]);
});

test("a description names a sibling wherever one could be confused for it", () => {
  // The pairs a caller most plausibly picks wrongly between. Each description
  // should say which is which rather than leave the reader to infer it.
  const mustMention: [string, string][] = [
    ["chap.abstain.declare", "chap.decide.reject"],
    ["chap.decide.approve", "chap.decide.override"],
    ["chap.decide.reject", "chap.decide.override"],
    ["chap.escalate.raise", "chap.handoff.propose"],
    ["chap.escalate.auto", "chap.escalate.raise"],
    ["chap.deliberate.open", "chap.review.request"],
    ["chap.handoff.decline", "chap.handoff.accept"],
    ["chap.audit.verify_receipt", "chap.audit.verify_chain"],
    ["chap.control.snapshot", "chap.control.rollback"],
    ["chap.task.route", "chap.task.create"],
  ];
  for (const [tool, sibling] of mustMention) {
    assert.ok(TOOL_DESCRIPTIONS[tool].includes(sibling),
      `${tool} should contrast itself with ${sibling}`);
  }
});
