// The gate's CHAP side: the configuration, a signing client for the agent,
// a proposal as a task under review, the decision on it, and the evidence
// a commit carries. propose.mjs, agent.mjs, the hooks and verify.mjs all
// run on these.

import { execFile } from "node:child_process";
import { createHash, createPublicKey, verify as cryptoVerify } from "node:crypto";
import { access, readFile } from "node:fs/promises";
import { basename, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { canonicalize, contentHash, deepEqual, makeClient, signerFromJwk } from "../desk/chap-client.mjs";
import { keyPathFor, readKeyFile, sshKeyFiles } from "../keys.mjs";
import { currentBranch, fileAt, head, patchStats, workingTreePatch } from "./git.mjs";

/** The most text a change carries as file contents beside its patch, so the desk can edit files whole. */
export const MAX_CONTENT_BYTES = 200_000;

export const TASK_KIND = "code_change";
export const NOTE_VERSION = 1;
const here = dirname(fileURLToPath(import.meta.url));

/** The gate directory, its configuration, and where the coordinator answers. */
export async function loadGate(dir = dirname(here)) {
  const config = JSON.parse(await readFile(join(dir, "chap.config.json"), "utf8"));
  const host = process.env.CHAP_HOST ?? config.host ?? "127.0.0.1";
  const port = process.env.PORT ?? config.port ?? 8791;
  const url = process.env.CHAP_URL ?? `http://${host}:${port}/chap`;
  return { dir, config, url, base: url.replace(/\/chap\/?$/, "") };
}

/** GET from the gate's read API. A 404 gives null. */
export async function api(gate, path) {
  const res = await fetch(`${gate.base}${path}`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`GET ${path} answered ${res.status}`);
  return res.json();
}

/** What the running coordinator says it serves, or a clear error when it is not running. */
export async function served(gate) {
  let cfg;
  try { cfg = await api(gate, "/api/config"); } catch (e) {
    throw new Error(`The gate at ${gate.base} is not answering (${e.cause?.code ?? e.message}). Start it with: npm start`);
  }
  if (cfg.workspace !== gate.config.workspace) {
    throw new Error(`${gate.base} serves ${cfg.workspace}, and chap.config.json here names ${gate.config.workspace}`);
  }
  return cfg;
}

/**
 * A client for the agent named in the configuration, signing with the key
 * in keys/ when the coordinator requires signatures, joined to the workspace.
 */
export async function agentClient(gate, { keyPath } = {}) {
  const cfg = await served(gate);
  const uri = gate.config.agent?.uri;
  if (!uri) throw new Error("chap.config.json names no agent");
  let signer = null;
  if (cfg.require_signatures) {
    const path = keyPath ?? process.env.CHAP_AGENT_KEY ?? keyPathFor(uri, join(gate.dir, "keys"));
    signer = await signerFromJwk(uri, await readKeyFile(uri, path));
  }
  const client = makeClient({ url: gate.url, workspace: gate.config.workspace, from: uri, signer });
  const join_ = { type: "agent", role: gate.config.agent.role ?? "drafter", display_name: gate.config.agent.display_name };
  if (signer) join_.jwks = { keys: [signer.publicJwk] };
  await client.call("participant.join", join_);
  return client;
}

let sshSigning = null;

/** Whether ssh-keygen on PATH can sign (OpenSSH 8.2 or later), asked once. */
export function sshCanSign() {
  sshSigning ??= new Promise((resolve) => {
    execFile("ssh-keygen", ["-?"], (err, stdout, stderr) => resolve(`${stdout ?? ""}${stderr ?? ""}`.includes("-Y sign")));
  });
  return sshSigning;
}

/**
 * The key the agent's commits are signed with: its CHAP key in OpenSSH's
 * format, written beside the JWK. Returns { key, allowedSigners } or
 * { key: null, reason } when commits go unsigned: `sign_commits` is false
 * in chap.config.json, there is no key file, or ssh-keygen cannot sign.
 */
export async function agentSigningKey(gate, { keyPath } = {}) {
  if (gate.config.sign_commits === false) return { key: null, reason: "sign_commits is false in chap.config.json" };
  const uri = gate.config.agent?.uri;
  const path = keyPath ?? process.env.CHAP_AGENT_KEY ?? keyPathFor(uri, join(gate.dir, "keys"));
  try { await access(path); } catch { return { key: null, reason: `no key file at ${path}; run npm run keys` }; }
  if (!(await sshCanSign())) return { key: null, reason: "ssh-keygen with -Y sign (OpenSSH 8.2 or later) is not on PATH" };
  const files = await sshKeyFiles(uri, path);
  return { key: files.privatePath, allowedSigners: files.allowedSigners };
}

/** A client for a human reviewer, signing with a key when one is given. */
export function reviewerClient(gate, uri, signer = null) {
  return makeClient({ url: gate.url, workspace: gate.config.workspace, from: uri, signer });
}

export const sha256 = (text) => createHash("sha256").update(text).digest("hex");

/** The idempotency key of a change: the same patch on the same base is the same task. */
export function changeKey(base, patch) {
  return "change-" + sha256(`${base ?? "none"}\n${patch}`).slice(0, 24);
}

/**
 * The artefact under review for a change in a repository: the patch of the
 * working tree against HEAD, with the files it touches and where it came
 * from. Null when the working tree matches HEAD.
 */
export async function describeChange(repo, { summary, drafted_by = "working tree", requested_by } = {}) {
  const patch = await workingTreePatch(repo);
  if (!patch.trim()) return null;
  const base = await head(repo);
  const files = await patchStats(repo, patch);
  // Each text file's content before and after travels with the patch, up to
  // a size, so a reviewer can edit the file whole and the desk writes the
  // patch again. A binary file, or a change too large, carries the patch only.
  let total = 0;
  const contents = [];
  for (const f of files) {
    if (f.added === null) { contents.push(null); continue; }
    const before = base ? await fileAt(repo, base, f.path) : null;
    let after = null;
    try { after = await readFile(join(repo, f.path), "utf8"); } catch { /* deleted */ }
    total += (before?.length ?? 0) + (after?.length ?? 0);
    contents.push({ before, after });
  }
  if (total <= MAX_CONTENT_BYTES) files.forEach((f, i) => { if (contents[i]) Object.assign(f, contents[i]); });
  return {
    summary,
    repo: basename(repo),
    branch: await currentBranch(repo),
    base,
    files,
    patch,
    drafted_by,
    ...(requested_by ? { requested_by } : {}),
  };
}

/**
 * The task a revision belongs to: one of this agent's code changes that a
 * rejection sent back to in_progress, for the same repository, branch and
 * summary. Null when there is none.
 */
export async function revisionTarget(gate, client, artefact) {
  const r = await api(gate, `/api/tasks?kind=${TASK_KIND}&state=in_progress`);
  const same = (t) => t.assignee === client.from && t.input?.repo === artefact.repo && t.input?.branch === artefact.branch && t.input?.summary === artefact.summary;
  return (r?.tasks ?? []).find((t) => same(t) && lastDecision(t)?.kind === "reject") ?? null;
}

/** The review rule in chap.config.json: { rule, to }. any_one_approves when none is set. */
export const reviewRule = (gate) => ({ rule: gate?.config?.review?.rule ?? "any_one_approves", to: gate?.config?.review?.to ?? null });

/** How many distinct approvals a rule needs, given who it is addressed to. */
export function approvalsNeeded(rule, to = []) {
  if (rule.startsWith("quorum:")) return Math.max(1, parseInt(rule.slice("quorum:".length), 10) || 1);
  if (rule === "all_approve") return Math.max(1, to.length);
  return 1;
}

/** The human members other than this participant: who a review is addressed to by default. */
export async function humanReviewers(client) {
  const ws = await client.call("workspace.describe", {});
  return (ws.members ?? []).filter((m) => m.uri !== client.from && m.type === "human").map((m) => m.uri);
}

/**
 * Whether enough reviewers are members for the rule to be met. A review
 * opened on task.complete is addressed to the human members other than the
 * completer, and the completion is refused and recorded when there are
 * none; a quorum with fewer members than it needs could never close. The
 * desk joins a reviewer the first time it is opened.
 */
export async function reviewersReady(client, review = { rule: "any_one_approves", to: null }) {
  const to = review.to ?? (await humanReviewers(client));
  const need = approvalsNeeded(review.rule, to);
  return { ok: to.length >= need, have: to.length, need, to };
}

/** Whether one reviewer has joined. */
export async function reviewerPresent(client) {
  return (await reviewersReady(client)).ok;
}

/**
 * Open the task for a change and submit the patch for review. A change
 * proposed before, on the same base, answers with the task it has; a
 * revision of a change the reviewer sent back goes to that task, named by
 * `taskId` or found by `revisionTarget`.
 *
 * Under the default rule the patch is submitted with task.complete, and the
 * review opens addressed to the human members. Under a rule that needs more
 * than one approval, each round is opened with review.request, so a round
 * starts with no decisions and approvals of an earlier version are not
 * counted for this one.
 *
 * Returns the task id, its state after this call, whether it was a
 * revision, and the digest the reviewers' decisions will carry.
 */
export async function propose(client, artefact, { gate = null, taskId = null, review = reviewRule(gate) } = {}) {
  let target = taskId;
  if (!target && gate) target = (await revisionTarget(gate, client, artefact))?.task_id ?? null;
  const digest = await contentHash(artefact);
  const submit = async (id) => {
    if (review.rule !== "any_one_approves") {
      const to = review.to ?? (await humanReviewers(client));
      return (await client.call("review.request", { task_id: id, artefact, to, rule: review.rule })).state;
    }
    return (await client.call("task.complete", { task_id: id, output: artefact })).state;
  };
  if (target) return { task_id: target, state: await submit(target), revised: true, digest };
  const input = { summary: artefact.summary, repo: artefact.repo, branch: artefact.branch, base: artefact.base, files: artefact.files.map((f) => f.path) };
  if (artefact.requested_by) input.requested_by = artefact.requested_by;
  const created = await client.call("task.create", {
    kind: TASK_KIND, assignee: client.from, input, review_required: true, idempotency_key: changeKey(artefact.base, artefact.patch),
  });
  let state = created.state;
  if (state === "created" || state === "in_progress") state = await submit(created.task_id);
  return { task_id: created.task_id, state, revised: false, digest };
}

/** The task's view from the read API, or null. */
export const task = (gate, id) => api(gate, `/api/tasks/${encodeURIComponent(id)}`);

/** The last decision on a task's review, or null. */
export const lastDecision = (view) => view?.review?.decisions?.at(-1) ?? null;

/** How a task ended, from its view: approve, override, reject, or null while it is open. */
export function outcomeOfView(view) {
  if (view?.state === "declined") return "reject";
  if (view?.state !== "completed") return null;
  const log = view.decision_log ?? view.review?.decisions ?? [];
  return log.filter((d) => d.kind === "approve" || d.kind === "override").at(-1)?.kind === "override" ? "override" : "approve";
}

/**
 * Poll a task until it leaves review. Returns the view. `onState` hears each
 * state seen. Throws after `timeoutMs` when one is given.
 */
export async function waitForDecision(gate, id, { pollMs = 2000, timeoutMs = null, onState = () => {} } = {}) {
  const started = Date.now();
  let last = null;
  for (;;) {
    const view = await task(gate, id);
    if (!view) throw new Error(`The gate does not know task ${id}`);
    if (view.state !== last) { onState(view.state, view); last = view.state; }
    if (view.state !== "review_requested") return view;
    if (timeoutMs !== null && Date.now() - started > timeoutMs) throw new Error(`No decision on ${id} after ${timeoutMs} ms`);
    await new Promise((r) => setTimeout(r, pollMs));
  }
}

/** The approved code changes the gate holds, newest first. */
export async function approvedChanges(gate, limit = 50) {
  const r = await api(gate, `/api/tasks?kind=${TASK_KIND}&state=completed&limit=${limit}`);
  return r?.tasks ?? [];
}

/** The evidence behind a task's decisions, from the read API. */
export const evidence = (gate, id) => api(gate, `/api/tasks/${encodeURIComponent(id)}/evidence`);

// -- RFC 6902, the part decide.override uses -----------------------------------

/** Apply add, remove and replace operations to a JSON value. Other operations are refused. */
export function applyJsonPatch(doc, ops) {
  let out = structuredClone(doc);
  for (const op of ops) {
    if (!["add", "remove", "replace"].includes(op.op)) throw new Error(`Unsupported patch operation: ${op.op}`);
    const tokens = op.path === "" ? [] : op.path.split("/").slice(1).map((t) => t.replace(/~1/g, "/").replace(/~0/g, "~"));
    if (tokens.length === 0) {
      if (op.op === "remove") throw new Error("Cannot remove the root");
      out = structuredClone(op.value);
      continue;
    }
    let parent = out;
    for (const t of tokens.slice(0, -1)) {
      parent = Array.isArray(parent) ? parent[Number(t)] : parent?.[t];
      if (parent === undefined) throw new Error(`Path not found: ${op.path}`);
    }
    const last = tokens.at(-1);
    if (Array.isArray(parent)) {
      const i = last === "-" ? parent.length : Number(last);
      if (op.op === "add") parent.splice(i, 0, structuredClone(op.value));
      else if (op.op === "remove") parent.splice(i, 1);
      else parent[i] = structuredClone(op.value);
    } else {
      if (op.op === "remove") { if (!(last in parent)) throw new Error(`Path not found: ${op.path}`); delete parent[last]; }
      else if (op.op === "replace" && !(last in parent)) throw new Error(`Path not found: ${op.path}`);
      else parent[last] = structuredClone(op.value);
    }
  }
  return out;
}

// -- the evidence a commit carries -----------------------------------------------

/**
 * The note written beside a commit, from the task's evidence: the task,
 * the review rule and who the final round was addressed to, every decision
 * of that round as the chain holds it (signed under security-signed/1.0),
 * the artefact as proposed in that round and as approved, the keys on
 * record for the reviewers and for the agent, and the chain head. The
 * approved artefact of an override is the proposed one with the reviewer's
 * operations applied. `decision` and `decision_envelope` name the decision
 * that settled the review.
 */
export function buildNote(ev, gateUrl) {
  const settling = ev.decisions.filter((d) => ["decide.approve", "decide.override"].includes(d.envelope.method)).at(-1);
  if (!settling) throw new Error(`Task ${ev.task.task_id} has no approval on the chain`);
  // The round the settling decision belongs to opened with the last
  // submission before it; its artefact is what the decisions sign.
  const submission = ev.submissions.filter((x) => x.seq < settling.seq).at(-1);
  const round = ev.decisions.filter((d) => d.seq > (submission?.seq ?? -1) && d.seq <= settling.seq);
  const approved = ev.task.output;
  const proposed = submission?.envelope.params?.output ?? submission?.envelope.params?.artefact ?? approved;
  const params = settling.envelope.params;
  const rule = ev.task.review?.rule ?? submission?.envelope.params?.rule ?? "any_one_approves";
  const requestedTo = ev.task.review?.requested_to ?? submission?.envelope.params?.to ?? [];
  const reviewerKeys = {};
  for (const d of round) { const uri = d.envelope.params?.from; if (uri && ev.keys[uri]) reviewerKeys[uri] = ev.keys[uri]; }
  return {
    chap_note: NOTE_VERSION,
    workspace: ev.workspace,
    coordinator: gateUrl,
    task_id: ev.task.task_id,
    kind: ev.task.kind,
    agent: ev.task.assignee,
    summary: approved?.summary ?? ev.task.input?.summary ?? "",
    rule,
    requested_to: requestedTo,
    decision: { method: settling.envelope.method, reviewer: params.from, seq: settling.seq, arrived: settling.arrived, comment: params.comment ?? params.rationale ?? null, tags: params.tags ?? null },
    decisions: round.map((d) => ({ method: d.envelope.method, reviewer: d.envelope.params?.from, seq: d.seq, arrived: d.arrived, envelope: d.envelope })),
    approved_artefact: approved,
    proposed_artefact: proposed,
    decision_envelope: settling.envelope,
    reviewer_keys: reviewerKeys,
    agent_keys: ev.keys[ev.task.assignee] ?? [],
    chain_head: ev.chain_head,
    chain_enabled: ev.chain_enabled,
  };
}

/**
 * Check a note on its own: the decisions of the final round name the task,
 * sign the digest of the proposed artefact and verify against the keys on
 * record; an override's operations lead from the proposed artefact to the
 * approved one; and the rule is met, with that many distinct reviewers
 * approving. Under a rule that needs more than one approval an override
 * does not count, since the reviewers who approved saw the proposed
 * version and not the edited one. Returns a list of problems, empty when
 * the note holds.
 */
export async function checkNote(note, { taskId = note.task_id, allowUnsigned = false } = {}) {
  const problems = [];
  if (note.task_id !== taskId) problems.push(`the note is for ${note.task_id}, the commit names ${taskId}`);
  const approved = note.approved_artefact;
  const proposed = note.proposed_artefact ?? approved;
  if (!approved || typeof approved.patch !== "string") problems.push("the note holds no approved patch");
  const proposedDigest = proposed ? await contentHash(proposed) : null;
  const decisions = Array.isArray(note.decisions) && note.decisions.length
    ? note.decisions
    : [{ method: note.decision_envelope?.method, reviewer: note.decision_envelope?.params?.from, envelope: note.decision_envelope }];
  const approvers = new Set();
  let overridden = false;
  for (const d of decisions) {
    const env = d.envelope ?? {};
    const params = env.params ?? {};
    if (!["decide.approve", "decide.override"].includes(env.method)) continue;
    if (params.task_id !== taskId) { problems.push(`a decision by ${params.from} names another task`); continue; }
    if (params.approved_artefact_digest !== proposedDigest) { problems.push(`the decision by ${params.from} does not sign the digest of the proposed artefact`); continue; }
    const keys = (note.reviewer_keys ?? {})[params.from] ?? [];
    if (env.sig) {
      const v = envelopeVerifies(env, keys);
      if (!v.ok) { problems.push(`the signature of ${params.from}: ${v.reason}`); continue; }
    } else if (!allowUnsigned) {
      problems.push(`the decision by ${params.from} is unsigned (pass --allow-unsigned where signatures are off)`);
      continue;
    }
    if (env.method === "decide.override") {
      overridden = true;
      try {
        if (!deepEqual(applyJsonPatch(proposed, params.diff ?? []), approved)) problems.push("the reviewer's operations applied to the proposed artefact do not give the approved one");
      } catch (e) { problems.push(`the reviewer's operations do not apply: ${e.message}`); }
    }
    approvers.add(params.from);
  }
  const rule = note.rule ?? "any_one_approves";
  const need = approvalsNeeded(rule, note.requested_to ?? []);
  if (!overridden && approved && proposed && !deepEqual(proposed, approved)) problems.push("approved as written, yet the approved artefact differs from the proposed one");
  if (need > 1 && overridden) problems.push(`under ${rule} an edit settles the review with one reviewer's decision; request changes so the edited version is approved again`);
  else if (approvers.size < need) problems.push(`${rule} needs ${need} approval${need === 1 ? "" : "s"}; ${approvers.size} on record`);
  if (rule === "all_approve") for (const uri of note.requested_to ?? []) if (!approvers.has(uri)) problems.push(`all_approve: no approval from ${uri}`);
  return problems;
}

/** The trailers a governed commit carries, from its note. */
export async function trailersFor(note) {
  const approvers = [...new Set((note.decisions ?? []).filter((d) => d.method === "decide.approve" || d.method === "decide.override").map((d) => d.reviewer))];
  const pairs = [
    ["CHAP-Workspace", note.workspace],
    ["CHAP-Task", note.task_id],
    ["CHAP-Agent", note.agent],
    ...(approvers.length ? approvers : [note.decision.reviewer]).map((r) => ["CHAP-Reviewer", r]),
    ["CHAP-Decision", note.decision.method === "decide.override" ? "override" : "approve"],
    ["CHAP-Rule", note.rule ?? "any_one_approves"],
    ["CHAP-Artefact", await contentHash(note.approved_artefact)],
    ["CHAP-Coordinator", note.coordinator],
  ];
  if (note.chain_head) pairs.push(["CHAP-Chain-Head", note.chain_head]);
  return pairs;
}

/** Whether `sig` on an envelope verifies against one of the JWKs given. */
export function envelopeVerifies(envelope, jwks) {
  const sig = envelope?.sig;
  if (typeof sig !== "string") return { ok: false, reason: "the envelope carries no signature" };
  const parts = sig.split(":");
  if (parts.length !== 3 || parts[0] !== "ed25519") return { ok: false, reason: "the signature is not ed25519:<kid>:<base64>" };
  const [, kid, b64] = parts;
  const { sig: _omit, ...rest } = envelope;
  const bytes = Buffer.from(canonicalize(rest), "utf8");
  const candidates = (jwks ?? []).filter((k) => k && (k.kid === kid || !k.kid));
  if (!candidates.length) return { ok: false, reason: `no key on record with kid ${kid}` };
  for (const jwk of candidates) {
    try {
      const key = createPublicKey({ key: { kty: "OKP", crv: "Ed25519", x: jwk.x }, format: "jwk" });
      if (cryptoVerify(null, bytes, key, Buffer.from(b64, "base64"))) return { ok: true, kid };
    } catch { /* the next key */ }
  }
  return { ok: false, reason: `the signature does not verify against the key ${kid}` };
}

export { canonicalize, contentHash };
