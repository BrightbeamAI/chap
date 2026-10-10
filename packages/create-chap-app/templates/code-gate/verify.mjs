// Verify that commits were approved through the gate: `node verify.mjs [range] [options]`.
//
//   range                     commits to check, e.g. main..HEAD (default: HEAD alone)
//   --repo <path>             the repository (default: the current directory)
//   --trust <file>            the trust policy: a chap-trust.json, read from this file
//   --trust-ref <ref>         the trust policy: chap-trust.json as the repository holds it at <ref>
//   --coordinator <url>       check each task against a running gate (its POST /chap address);
//                             with no trust policy given, the gate's records are the policy
//   --require-signed-commit   fail a commit that the agent's key did not sign
//   --allow-people            let a commit with no CHAP approval through when it is signed by a
//                             key the trust policy lists under people (SSH signatures)
//   --allow-clean-merges      let a merge commit through when its tree is the clean merge of its
//                             parents (git 2.38 or later); otherwise every merge commit fails
//   --base <sha>              the commit a pull request merges into: the range is then one
//                             line of commits from the point where it leaves <sha>'s history,
//                             with no merge in it, and no approval in it was used in <sha>'s
//                             history already (in CI, the pull request's base)
//   --allow-unsigned          accept decisions with no signature (where signatures are off)
//   --json                    print the results as JSON
//
// A trust policy says whose approvals count and which agents may commit,
// with their keys pinned. Offline, with --trust or --trust-ref, each commit
// is checked with nothing but the repository: its note's approvals come
// from reviewers the policy names, never from an agent, verify against the
// pinned keys and sign the digest of what was proposed; the agent's signed
// submission is the proposed artefact; the policy's rule is met; an
// override's operations lead to the approved artefact; the commit's parent
// is the commit the change was approved against; the approved patch is
// git's own diff of the change and gives the commit's tree; no other commit
// in the range uses the same approval; and the commit's signature verifies
// against the agent's pinned key. With --coordinator the same checks run
// against the gate's own records, and the note must agree with them. With
// neither, the gate in chap.config.json beside this file is asked. Exit
// code 1 on any failure.

import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { buildNote, checkApproval, checkChange, contentHash, evidence, loadGate, onlinePolicy, readTrustFile, served, trustAtRef } from "./lib/gate.mjs";
import { cleanMergeTree, commitInfo, commitSignature, commitsWithTrailer, emptyTree, git, parseTrailers, readNote, repoRoot, revList, treeOf, verifySshSignature } from "./lib/git.mjs";
import { opensshPublicKey } from "./keys.mjs";

const here = dirname(fileURLToPath(import.meta.url));

export function parseArgs(argv) {
  const out = { range: "HEAD", repo: process.cwd(), trust: null, trustRef: null, coordinator: null, base: null, requireSignedCommit: false, allowPeople: false, allowCleanMerges: false, allowUnsigned: false, json: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--repo") out.repo = argv[++i];
    else if (a === "--trust") out.trust = argv[++i];
    else if (a === "--trust-ref") out.trustRef = argv[++i];
    else if (a === "--coordinator") out.coordinator = argv[++i];
    else if (a === "--base") out.base = argv[++i];
    else if (a === "--require-signed-commit") out.requireSignedCommit = true;
    else if (a === "--allow-people") out.allowPeople = true;
    else if (a === "--allow-clean-merges") out.allowCleanMerges = true;
    else if (a === "--allow-unsigned") out.allowUnsigned = true;
    else if (a === "--json") out.json = true;
    else if (a.startsWith("-")) throw new Error(`Unknown option ${a}`);
    else out.range = a;
  }
  return out;
}

/** Check a commit's SSH signature against allowed signers lines. */
async function signedBy(repo, sha, lines) {
  const sig = await commitSignature(repo, sha);
  if (!sig) return { signed: false };
  if (!sig.ssh) return { signed: true, ok: false, detail: "the commit carries a signature that is not an SSH signature" };
  if (!lines.length) return { signed: true, ok: false, detail: "no key to check the commit's signature against" };
  const dir = await mkdtemp(join(tmpdir(), "chap-signers-"));
  try {
    const file = join(dir, "allowed_signers");
    await writeFile(file, lines.join("\n") + "\n");
    const v = await verifySshSignature(repo, sha, file);
    return { signed: true, ok: v.ok, detail: v.detail };
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

const signerLines = (uri, jwks) => (jwks ?? []).filter((k) => k?.kty === "OKP" && k?.crv === "Ed25519" && typeof k.x === "string")
  .map((k) => `${uri} namespaces="git" ${opensshPublicKey(k).split(" ").slice(0, 2).join(" ")}`);

/**
 * Check one commit. `policy` is a trust policy, or null to take it from
 * the gate's records for each task. Returns { sha, subject, status:
 * "ok" | "FAIL", detail, task_id?, reviewers? }.
 */
export async function verifyCommit(repo, sha, { policy = null, gate = null, seen = new Map(), requireSignedCommit = false, allowPeople = false, allowCleanMerges = false, allowUnsigned = false } = {}) {
  const info = await commitInfo(repo, sha);
  const subject = info.message.split("\n")[0];
  const row = (status, detail, extra = {}) => ({ sha, subject, status, detail, ...extra });
  if (info.parents.length > 1) {
    if (!allowCleanMerges) return row("FAIL", "a merge commit: what it brings in was never proposed as one change. Rebase, or pass --allow-clean-merges");
    const clean = await cleanMergeTree(repo, info.parents[0], info.parents[1]);
    if (info.parents.length !== 2 || clean === null) return row("FAIL", "a merge commit whose merge this git cannot reproduce cleanly (git merge-tree --write-tree needs git 2.38 or later)");
    return clean === info.tree ? row("ok", "a clean merge of its parents, carrying no change of its own") : row("FAIL", "a merge commit whose tree is not the clean merge of its parents: it carries changes of its own");
  }
  const trailerList = await parseTrailers(repo, info.message);
  const trailers = Object.fromEntries(trailerList.map((t) => [t.token, t.value]));
  const taskId = trailers["CHAP-Task"];
  if (!taskId) {
    if (allowPeople && policy?.people?.length) {
      const s = await signedBy(repo, sha, policy.people);
      if (s.signed && s.ok) return row("ok", `no CHAP approval; signed by a person ${policy.source} lists (${s.detail})`);
    }
    return row("FAIL", "no CHAP approval: the message carries no CHAP-Task trailer");
  }
  const problems = [];
  if (seen.has(taskId)) problems.push(`${taskId} was used already by ${seen.get(taskId).slice(0, 12)}; an approval covers one commit`);
  else if (seen.base) {
    const before = await commitsWithTrailer(repo, "CHAP-Task", taskId, [seen.base]);
    if (before.length) problems.push(`${taskId} was used already by ${before[0].slice(0, 12)} in the base's history; an approval covers one commit`);
  }
  seen.set(taskId, sha);

  // The note: from the repository, and, with a gate, rebuilt from its records.
  let note = null;
  const text = await readNote(repo, sha);
  if (text) { try { note = JSON.parse(text); } catch { problems.push("the evidence note is not JSON"); } }
  let usePolicy = policy;
  if (gate) {
    const ev = await evidence(gate, taskId).catch(() => null);
    if (!ev) problems.push(`the gate at ${gate.base} does not know ${taskId}`);
    else if (ev.task.state !== "completed") problems.push(`the gate holds ${taskId} as ${ev.task.state}`);
    else {
      const fromGate = buildNote(ev, gate.url);
      if (note && (await contentHash(note.approved_artefact)) !== (await contentHash(fromGate.approved_artefact))) problems.push("the note's approved artefact differs from the gate's record");
      note = fromGate;
      if (!usePolicy) usePolicy = onlinePolicy(gate, ev);
    }
  }
  if (!note) {
    problems.push(`no evidence note for ${taskId}; fetch it with: git fetch origin refs/notes/chap:refs/notes/chap`);
    return row("FAIL", problems.join("; "), { task_id: taskId });
  }
  if (!usePolicy) return row("FAIL", [...problems, "no trust policy to check the approval against"].join("; "), { task_id: taskId });

  problems.push(...(await checkApproval(note, usePolicy, { taskId, allowUnsigned })));
  if (trailers["CHAP-Workspace"] && note.workspace !== trailers["CHAP-Workspace"]) problems.push("the note's workspace differs from the trailer");
  const approvedDigest = note.approved_artefact ? await contentHash(note.approved_artefact) : null;
  if (approvedDigest && trailers["CHAP-Artefact"] && approvedDigest !== trailers["CHAP-Artefact"]) problems.push("the approved artefact's digest differs from the CHAP-Artefact trailer");
  const reviewers = [...new Set((note.decisions ?? []).filter((d) => d.method === "decide.approve" || d.method === "decide.override").map((d) => d.reviewer))];
  const trailerReviewers = trailerList.filter((t) => t.token === "CHAP-Reviewer").map((t) => t.value);
  if (trailerReviewers.slice().sort().join(",") !== reviewers.slice().sort().join(",")) problems.push("the CHAP-Reviewer trailers differ from the approvals in the note");
  if (typeof note.approved_artefact?.patch === "string") {
    const parent = info.parents[0] ?? null;
    const parentTree = parent ? await treeOf(repo, parent) : await emptyTree(repo);
    problems.push(...(await checkChange(repo, { parent, parentTree, tree: info.tree, approved: note.approved_artefact })));
  }
  const signature = await signedBy(repo, sha, signerLines(note.agent, usePolicy.agents?.[note.agent]));
  if (signature.signed && !signature.ok) problems.push(`the commit's signature: ${signature.detail}`);
  if (!signature.signed && requireSignedCommit) problems.push("the commit is not signed by the agent's key");

  if (problems.length) return row("FAIL", problems.join("; "), { task_id: taskId, reviewers });
  const how = note.decision?.method === "decide.override" ? "approved with an edit" : "approved";
  const by = reviewers.length > 1 ? `${reviewers.join(" and ")} (${usePolicy.rule})` : reviewers[0];
  const parts = [`${how} by ${by} as ${taskId}`, `checked against ${usePolicy.source}`];
  if (signature.signed) parts.push("commit signed by the agent's key");
  return row("ok", parts.join(", "), { task_id: taskId, reviewers });
}

/**
 * Check every commit of a range. The policy comes from `trust` (a file),
 * `trustRef` (chap-trust.json at a revision), or a gate: `gate` (as
 * loadGate returns it) or the one at `coordinator`; with none, from the
 * gate in chap.config.json beside this file.
 */
export async function verifyRange(repoPath, range, options = {}) {
  const repo = await repoRoot(repoPath);
  if (!repo) throw new Error(`${repoPath} is not inside a git repository`);
  let policy = null;
  if (options.trust) policy = await readTrustFile(options.trust);
  else if (options.trustRef) {
    policy = await trustAtRef(repo, options.trustRef);
    if (!policy) throw new Error(`${options.trustRef} holds no chap-trust.json`);
  }
  let gate = options.gate ?? null;
  if (!gate && options.coordinator) gate = { config: (await loadGate(here)).config, url: options.coordinator, base: options.coordinator.replace(/\/chap\/?$/, "") };
  else if (!gate && !policy) {
    gate = await loadGate(here);
    try { await served(gate); } catch (e) {
      throw new Error(`No trust policy was given, and the gate at ${gate.base} cannot be asked (${e.message}). Pass --trust chap-trust.json, --trust-ref <ref>, or --coordinator <url>.`);
    }
  }
  const seen = new Map();
  const shas = await revList(repo, range);
  const results = [];
  let anchored = null;
  if (options.base) {
    // A pull request against its base: one line of commits from the point
    // where it leaves the base's history, with no merge in it. Each commit
    // was approved on the commit it sits on, so a pull request behind its
    // base passes as it is, and the merge brings it onto the newer base. An
    // approval already used anywhere in the base's history counts for
    // nothing here, so an approved change that landed and was reverted
    // cannot come back on an older commit.
    const base = (await git(repo, ["rev-parse", "--verify", `${options.base}^{commit}`])).trim();
    seen.base = base;
    if (shas.length) {
      const fork = await git(repo, ["merge-base", base, shas.at(-1)]).then((o) => o.trim(), () => "");
      if (!fork) anchored = `the range shares no history with ${base.slice(0, 12)}`;
      let expected = fork;
      for (const sha of fork ? shas : []) {
        const info = await commitInfo(repo, sha);
        if (info.parents.length !== 1 || info.parents[0] !== expected) {
          anchored = `${sha.slice(0, 12)} does not sit on ${expected.slice(0, 12)}: a pull request is one line of commits from where it leaves ${base.slice(0, 12)}, with no merge in it`;
          break;
        }
        expected = sha;
      }
    }
  }
  for (const sha of shas) {
    const r = await verifyCommit(repo, sha, { ...options, policy, gate, seen });
    if (anchored) { r.status = "FAIL"; r.detail = anchored + (r.detail ? `; ${r.detail}` : ""); }
    results.push(r);
  }
  return results;
}

export function render(results) {
  const lines = results.map((r) => `${r.status.padEnd(5)} ${r.sha.slice(0, 12)}  ${r.subject.slice(0, 44).padEnd(44)}  ${r.detail}`);
  const failed = results.filter((r) => r.status === "FAIL").length;
  lines.push(failed ? `${failed} of ${results.length} commit${results.length === 1 ? "" : "s"} failed` : `${results.length} commit${results.length === 1 ? "" : "s"} verified`);
  return lines.join("\n");
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  (async () => {
    const args = parseArgs(process.argv.slice(2));
    const results = await verifyRange(args.repo, args.range, args);
    console.log(args.json ? JSON.stringify(results, null, 2) : render(results));
    process.exit(results.some((r) => r.status === "FAIL") ? 1 : 0);
  })().catch((e) => { console.error(e instanceof Error ? e.message : String(e)); process.exit(1); });
}
