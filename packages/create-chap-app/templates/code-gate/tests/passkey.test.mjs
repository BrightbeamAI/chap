// An approval the reviewer signed with a passkey, as the desk signs under
// "desk_signing": "auto" where the browser has one: the hooks commit it, the
// verifier checks it online and against a trust file that pins the
// passkey's key, and the trust file refuses it once it pins another key.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describeChange, fingerprint, propose, reviewerClient } from "@brightbeamai/chap-code-gate/gate";
import { head, readNote } from "@brightbeamai/chap-code-gate/git";
import { verifyRange } from "@brightbeamai/chap-code-gate/verify";
import { buildTrust } from "@brightbeamai/chap-code-gate/trust";
import { appendTo, commit, decide, demoRepo, passkeySigner, reviewsFor, startGate } from "./helpers.mjs";

let g, reviewer, repo;

before(async () => {
  g = await startGate({ suffix: "passkey" });
  const uri = g.config.humans[0].uri;
  reviewer = reviewerClient(g.gate, uri, await passkeySigner(uri, `http://localhost:${g.server.address().port}`));
  await reviewer.call("participant.join", { type: "human", role: "reviewer", jwks: { keys: [reviewer.signer.publicJwk] } });
  repo = await demoRepo();
});

after(async () => { await g.close(); });

test("the code gate ships with the desk signing with a passkey where the browser has one", async () => {
  const cfg = await (await fetch(`${g.base}/api/config`)).json();
  assert.equal(cfg.desk_signing, "auto");
});

test("an approval signed with a passkey commits through the hooks and verifies online and against the trust file", async () => {
  await appendTo(repo, "lib/calc.mjs", "\nexport function cube(a) {\n  return a * a * a;\n}\n");
  const artefact = await describeChange(repo, { summary: "Add cube", drafted_by: "the test" });
  const { task_id } = await propose(g.agent, artefact, { gate: g.gate });
  const review = (await reviewsFor(g, reviewer)).find((r) => r.task_id === task_id);
  assert.equal((await decide(reviewer, "decide.approve", review)).state, "completed");
  await commit(g, repo, "Add cube");
  const sha = await head(repo);
  const note = JSON.parse(await readNote(repo, sha));
  assert.match(note.decision_envelope.sig, new RegExp(`^webauthn:${reviewer.signer.kid}:`));
  assert.deepEqual(note.reviewer_keys[reviewer.from], [reviewer.signer.publicJwk]);

  const online = await verifyRange(repo, "HEAD", { gate: g.gate });
  assert.equal(online[0].status, "ok", online[0].detail);

  const { trust } = await buildTrust(g.gate, { keyPath: g.keyPath });
  assert.deepEqual(trust.reviewers[reviewer.from].keys, [reviewer.signer.publicJwk], "the trust file pins the passkey's key, webauthn member and all");
  assert.match(fingerprint(reviewer.signer.publicJwk), /^SHA256:[A-Za-z0-9+/]{43}$/);
  const file = join(await mkdtemp(join(tmpdir(), "chap-trust-")), "chap-trust.json");
  await writeFile(file, JSON.stringify(trust));
  const offline = await verifyRange(repo, "HEAD", { trust: file });
  assert.equal(offline[0].status, "ok", offline[0].detail);

  const other = await passkeySigner(reviewer.from, "http://localhost:1");
  await writeFile(file, JSON.stringify({ ...trust, reviewers: { [reviewer.from]: { ...trust.reviewers[reviewer.from], keys: [other.publicJwk] } } }));
  const refused = await verifyRange(repo, "HEAD", { trust: file });
  assert.equal(refused[0].status, "FAIL");
  assert.match(refused[0].detail, /signature of human:you@local: no pinned passkey with kid/);
});
