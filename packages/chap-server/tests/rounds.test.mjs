// Which call opened a review round. A decision names the submission that
// opened its round, and the code gate's note carries it for a verifier, so
// the desk servers, the desk and the note must agree on it: a review.request
// that adds reviewers to the open round opens none, and one after a reject,
// an override or an abstention opens another.
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "@brightbeamai/chap-coordinator";
import { taskEvidence, taskView } from "../index.mjs";

test("a review.request that adds reviewers opens no round; one after a reject opens another", () => {
  const coord = new Coordinator({ defaultProfiles: ["core/1.0", "review/1.0"], deterministicIds: true });
  let n = 0;
  const send = (method, params, from = "agent:b") => {
    const r = coord.dispatch({ jsonrpc: "2.0", id: `c${++n}`, method, params: { workspace: "w", from, ...params } });
    assert.equal(r.error, undefined, JSON.stringify(r.error));
    return r.result;
  };
  send("workspace.create", {});
  for (const [uri, type] of [["human:a", "human"], ["human:c", "human"], ["agent:b", "agent"]]) send("participant.join", { type }, uri);
  const id = send("task.create", { kind: "k", input: {}, assignee: "agent:b" }).task_id;
  const draft = { body: "v1" };
  send("task.complete", { task_id: id, output: draft });
  const opened = () => taskView(coord, "w", id).submission?.envelope;
  const noted = () => taskEvidence(coord, "w", id).submissions.map((s) => s.envelope.method);
  send("review.request", { task_id: id, artefact: draft, to: ["human:a"] });
  send("review.request", { task_id: id, artefact: draft, to: ["human:c"] });
  assert.equal(opened().method, "task.complete", "the requests carry the artefact under review, and open no round");
  assert.deepEqual(noted(), ["task.complete"]);
  send("decide.reject", { task_id: id }, "human:a");
  send("review.request", { task_id: id, artefact: draft, to: ["human:c"] });
  assert.equal(opened().method, "review.request", "after a reject, the same artefact opens a new round");
  assert.deepEqual(noted(), ["task.complete", "review.request"]);
});
