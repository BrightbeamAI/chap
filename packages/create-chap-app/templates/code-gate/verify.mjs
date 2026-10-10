// Verify that commits were approved through the gate: `node verify.mjs [range] [options]`.
//
//   range                     commits to check, e.g. main..HEAD (default: HEAD alone)
//   --repo <path>             the repository (default: the current directory)
//   --trust <file>            the trust policy: a chap-trust.json, read from this file
//   --trust-ref <ref>         the trust policy: chap-trust.json as the repository holds it at <ref>
//   --coordinator <url>       check each task against a running gate (its POST /chap address);
//                             with no trust policy given, the gate's records are the policy
//   --require-signed-commit   fail a commit that is not signed with a key the trust policy
//                             lists: the agent's, or a person's under people
//   --allow-people            let a commit with no CHAP approval through when it is signed by a
//                             key the trust policy lists under people (SSH signatures)
//   --allow-clean-merges      let a merge commit through when its tree is the clean merge of its
//                             parents (git 2.38 or later); otherwise every merge commit fails
//   --base <sha>              the commit a pull request merges into: the range is then one
//                             line of commits from the point where it leaves <sha>'s history,
//                             with no merge in it, no approval in it was used in <sha>'s
//                             history already, and an approved branch in it is there whole
//                             (in CI, the pull request's base)
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
// in the range uses the same approval; the trailers say what the evidence
// says, the reviewers' names and emails and the model included; and the
// commit's signature verifies against the agent's pinned key. A sealed
// commit of an approved branch is held to the commit the reviewers saw: its
// tree, author and message, its place in the branch, the sealed commit
// before it, and the patch the desk showed for it. With --coordinator the
// same checks run
// against the gate's own records, and the note must agree with them. With
// neither, the gate in chap.config.json beside this file is asked. Exit
// code 1 on any failure.

import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { approvalOf, buildNote, checkApproval, checkChange, checkedLines, contentHash, evidence, legacyTrailersFor, LEGACY_CHECKED_TRAILERS, loadGate, noteArtefacts, onlinePolicy, RANGE_KIND, readTrustFile, served, trailersFor, trustAtRef } from "./lib/gate.mjs";
import { cleanMergeTree, commitInfo, commitSignature, commitsWithApproval, emptyTree, git, parseTrailers, rawCommit, readNote, repoRoot, revList, treeOf, verifySshSignature } from "./lib/git.mjs";
import { checkRangeCommit, seriesOf } from "./lib/range.mjs";
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
  const approval = approvalOf(trailerList);
  if (!approval) {
    if (allowPeople && policy?.people?.length) {
      const s = await signedBy(repo, sha, policy.people);
      if (s.signed && s.ok) return row("ok", `no CHAP approval; signed by a person ${policy.source} lists (${s.detail})`);
    }
    return row("FAIL", "no CHAP approval: the message carries no CHAP-Approval trailer");
  }
  if (approval.invalid !== undefined) return row("FAIL", `CHAP-Approval ${JSON.stringify(approval.invalid)} does not read as <task> or <task> <n>/<of>`);
  const taskId = approval.task;
  const problems = [];
  // A commit of a sealed branch names its place in it; an approval covers
  // one commit, or one commit at each place of an approved branch.
  const series = seriesOf(trailerList);
  const place = series && series.invalid === undefined ? series : null;
  const useKey = place ? `${taskId}#${place.index}` : taskId;
  const what = place ? `${taskId} at ${place.index + 1}/${place.of}` : taskId;
  if (seen.has(useKey)) problems.push(`${what} was used already by ${seen.get(useKey).slice(0, 12)}; an approval covers one commit`);
  else if (seen.base) {
    for (const other of await commitsWithApproval(repo, taskId, [seen.base])) {
      const theirs = place ? seriesOf(await parseTrailers(repo, (await commitInfo(repo, other)).message)) : null;
      if (!place || (theirs && theirs.index === place.index)) { problems.push(`${what} was used already by ${other.slice(0, 12)} in the base's history; an approval covers one commit`); break; }
    }
  }
  seen.set(useKey, sha);
  if (place) {
    seen.series ??= new Map();
    const entry = seen.series.get(taskId) ?? { of: place.of, have: new Set(), shas: [] };
    entry.have.add(place.index);
    entry.shas.push(sha);
    seen.series.set(taskId, entry);
  }

  // The note: from the repository, and, with a gate, rebuilt from its records.
  let note = null;
  const text = await readNote(repo, sha);
  if (text) { try { note = JSON.parse(text); } catch { problems.push("the evidence note is not JSON"); } }
  let usePolicy = policy;
  if (gate) {
    // The commits of one branch share a task: its evidence is read once.
    seen.evidence ??= new Map();
    if (!seen.evidence.has(taskId)) seen.evidence.set(taskId, await evidence(gate, taskId).catch(() => null));
    const ev = seen.evidence.get(taskId);
    if (!ev) problems.push(`the gate at ${gate.base} does not know ${taskId}`);
    else if (ev.task.state !== "completed") problems.push(`the gate holds ${taskId} as ${ev.task.state}`);
    else {
      const fromGate = buildNote(ev, gate.url);
      if (note && (await contentHash(noteArtefacts(note).approved)) !== (await contentHash(noteArtefacts(fromGate).approved))) problems.push("the note's approved artefact differs from the gate's record");
      note = fromGate;
      if (!usePolicy) usePolicy = onlinePolicy(gate, ev);
    }
  }
  if (!note) {
    problems.push(`no evidence note for ${taskId}; fetch it with: git fetch origin refs/notes/chap:refs/notes/chap`);
    return row("FAIL", problems.join("; "), { task_id: taskId });
  }
  if (!usePolicy) return row("FAIL", [...problems, "no trust policy to check the approval against"].join("; "), { task_id: taskId });

  if (series && note.kind !== RANGE_KIND) problems.push("the commit names a place in an approved branch, and its note approves a single change");
  if (!series && note.kind === RANGE_KIND) problems.push("the note approves a branch, and the commit names no place in it");
  problems.push(...(await checkApproval(note, usePolicy, { taskId, allowUnsigned })));
  const { approved } = noteArtefacts(note);
  const reviewers = [...new Set((note.decisions ?? []).filter((d) => d.method === "decide.approve" || d.method === "decide.override").map((d) => d.reviewer))];
  // The trailers the commit should carry, from the evidence and the policy:
  // the model that drafted it, each approving reviewer by name and email,
  // and the approval with the commit's place in a branch. A commit in the
  // longer form an earlier version wrote is held to that form.
  if (note.decision && approved) {
    const series = place ? { ...place, proposed: approved.commits?.[place.index]?.sha ?? null } : null;
    const tokens = approval.legacy ? LEGACY_CHECKED_TRAILERS : undefined;
    const expected = checkedLines(await (approval.legacy ? legacyTrailersFor : trailersFor)(note, usePolicy, series ? { series } : {}), tokens);
    const actual = checkedLines(trailerList.map((t) => [t.token, t.value]), tokens);
    const missing = expected.filter((l) => !actual.includes(l));
    const extra = actual.filter((l) => !expected.includes(l));
    if (missing.length || extra.length) problems.push(`the trailers differ from the evidence${missing.length ? `; missing ${missing.map((l) => JSON.stringify(l)).join(", ")}` : ""}${extra.length ? `; not borne out ${extra.map((l) => JSON.stringify(l)).join(", ")}` : ""}`);
    // The sign-off names the committer the commit records, and only them.
    if (!approval.legacy) {
      const raw = await rawCommit(repo, sha);
      const committer = raw?.committer ? `${raw.committer.name} <${raw.committer.email}>` : null;
      const signoffs = trailerList.filter((t) => t.token === "Signed-off-by").map((t) => t.value);
      if (signoffs.length !== 1 || signoffs[0] !== committer) problems.push(`Signed-off-by should name the committer, ${committer}, once; the commit says ${signoffs.length ? signoffs.map((v) => JSON.stringify(v)).join(", ") : "nothing"}`);
    }
  }
  if (series && note.kind === RANGE_KIND) {
    problems.push(...(await checkRangeCommit(repo, await rawCommit(repo, sha), { series, note })));
  } else if (!series && typeof approved?.patch === "string") {
    const parent = info.parents[0] ?? null;
    const parentTree = parent ? await treeOf(repo, parent) : await emptyTree(repo);
    problems.push(...(await checkChange(repo, { parent, parentTree, tree: info.tree, approved })));
  }
  // A git signature is checked against the keys the policy lists. One by
  // a key it does not list (the committer's own, say) is a record, and
  // fails the commit only where signed commits are required.
  const signature = await signedBy(repo, sha, [...signerLines(note.agent, usePolicy.agents?.[note.agent]), ...(usePolicy.people ?? [])]);
  const byAgent = signature.signed && signature.ok && signature.detail.includes(`for ${note.agent} `);
  if (requireSignedCommit && !(signature.signed && signature.ok)) problems.push(signature.signed ? `the commit is signed with a key ${usePolicy.source} does not list (${signature.detail})` : "the commit is not signed");

  if (problems.length) return row("FAIL", problems.join("; "), { task_id: taskId, reviewers });
  const how = note.decision?.method === "decide.override" ? "approved with an edit" : "approved";
  const names = reviewers.map((uri) => usePolicy.identities?.[uri]?.name ?? uri);
  const by = names.length > 1 ? `${names.join(" and ")} (${usePolicy.rule})` : names[0];
  const parts = [`${how} by ${by} as ${taskId}${place ? `, commit ${place.index + 1} of ${place.of} of the branch` : ""}`, `checked against ${usePolicy.source}`];
  const model = (place && approved?.commits?.[place.index]?.model) ?? approved?.model;
  if (model) parts.push(`written by ${model}`);
  if (signature.signed && signature.ok) parts.push(byAgent ? "commit signed by the agent's key" : "commit signed by a person the policy lists");
  return row("ok", parts.join(", "), { task_id: taskId, reviewers });
}

/**
 * The approved branches a set of commits holds only part of. A branch is
 * approved as a whole, so each place in it must be among the commits
 * checked, or in the history given (what the base, or the remote, holds
 * already). Returns [{ task_id, of, missing: [n], shas }].
 */
export async function seriesGaps(repo, seen, history = []) {
  const out = [];
  for (const [taskId, entry] of seen.series ?? []) {
    let missing = [];
    for (let i = 0; i < entry.of; i++) if (!entry.have.has(i)) missing.push(i);
    if (missing.length && history.length) {
      const before = new Set();
      for (const other of await commitsWithApproval(repo, taskId, history)) {
        const s = seriesOf(await parseTrailers(repo, (await commitInfo(repo, other)).message));
        if (s && s.invalid === undefined) before.add(s.index);
      }
      missing = missing.filter((i) => !before.has(i));
    }
    if (missing.length) out.push({ task_id: taskId, of: entry.of, missing: missing.map((i) => i + 1), shas: entry.shas });
  }
  return out;
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
  // A pull request lands an approved branch whole: commits of it missing
  // from the range and from the base's history fail the ones that are here.
  for (const gap of seen.base ? await seriesGaps(repo, seen, [seen.base]) : []) {
    for (const r of results.filter((x) => gap.shas.includes(x.sha))) {
      r.status = "FAIL";
      r.detail = `${gap.task_id} approved a branch of ${gap.of} commits, and commit${gap.missing.length === 1 ? "" : "s"} ${gap.missing.join(", ")} of it ${gap.missing.length === 1 ? "is" : "are"} not here; an approved branch lands whole${r.detail ? `; ${r.detail}` : ""}`;
    }
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
