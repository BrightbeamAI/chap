// The gate's CHAP side: the configuration, a signing client for the agent,
// a proposal as a task under review, the decision on it, the trust policy
// that says whose approvals count, and the evidence a commit carries.
// propose.mjs, agent.mjs, the hooks, trust.mjs and verify.mjs run on these.

import { execFile } from "node:child_process";
import { createHash, createPublicKey, verify as cryptoVerify } from "node:crypto";
import { access, readFile } from "node:fs/promises";
import { basename, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { canonicalize, contentHash, deepEqual, makeClient, signerFromJwk } from "../desk/chap-client.mjs";
import { keyPathFor, readKeyFile, sshKeyFiles } from "../keys.mjs";
import { blobAt, currentBranch, faithfulCheck, git, head, numstat, workingTreeChange } from "./git.mjs";

/** The most text a change carries as file contents beside its patch, so the desk can edit files whole. */
export const MAX_CONTENT_BYTES = 200_000;

export const TASK_KIND = "code_change";
export const NOTE_VERSION = 1;
export const TRUST_FILE = "chap-trust.json";
const here = dirname(fileURLToPath(import.meta.url));

// -- the gate --------------------------------------------------------------------

/**
 * The gate directory, its configuration, and where the coordinator answers.
 * CHAP_URL names a gate elsewhere, CHAP_AGENT_URI the agent this
 * developer's proposals come from, when a team shares one gate, and
 * CHAP_CONFIG a configuration file other than chap.config.json here.
 */
export async function loadGate(dir = dirname(here)) {
  const configPath = process.env.CHAP_CONFIG ?? join(dir, "chap.config.json");
  const config = JSON.parse(await readFile(configPath, "utf8"));
  if (process.env.CHAP_AGENT_URI) config.agent = { ...(config.agent ?? {}), uri: process.env.CHAP_AGENT_URI };
  const host = process.env.CHAP_HOST ?? config.host ?? "127.0.0.1";
  const port = process.env.PORT ?? config.port ?? 8791;
  const url = process.env.CHAP_URL ?? `http://${host}:${port}/chap`;
  return { dir, config, configPath, url, base: url.replace(/\/chap\/?$/, "") };
}

/** The environment a commit made for this gate passes to its hooks, so they ask the same gate. */
export function gateEnv(gate) {
  return { CHAP_URL: gate.url, ...(gate.configPath ? { CHAP_CONFIG: gate.configPath } : {}) };
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
    throw Object.assign(new Error(`The gate at ${gate.base} is not answering (${e.cause?.code ?? e.message}). Start it with: npm start`), { unreachable: true });
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

// -- a change ----------------------------------------------------------------------

/**
 * The artefact under review for a change in a repository: the canonical
 * patch of the working tree against HEAD, the commit it applies to, the
 * files it touches and where it came from. Each text file's content before
 * and after, read from the two trees, travels beside the patch up to a
 * size, so a reviewer can edit a file whole. Null when the working tree
 * matches HEAD.
 */
export async function describeChange(repo, { summary, drafted_by = "working tree", requested_by } = {}) {
  const { baseTree, tree, patch } = await workingTreeChange(repo);
  if (!patch.trim()) return null;
  // The patch travels as text. It must give the working tree back, and read
  // in the desk as the change it is, or the reviewer would decide on
  // something other than what gets committed: a file that is not UTF-8
  // text, or a submodule, is refused here.
  const check = await faithfulCheck(repo, baseTree, patch);
  if (!check.ok) throw new Error(`This change cannot be proposed as it stands: ${check.reason.replace(/^what a reviewer sees of the patch is not what git applies: /, "")}`);
  if (check.tree !== tree) throw new Error("This change cannot be proposed as text: a file in it is not UTF-8, so the patch would not give the working tree back");
  const files = await numstat(repo, baseTree, tree);
  let total = 0;
  const contents = [];
  for (const f of files) {
    if (f.added === null) { contents.push(null); continue; }
    const before = await blobAt(repo, baseTree, f.path);
    const after = await blobAt(repo, tree, f.path);
    total += (before?.length ?? 0) + (after?.length ?? 0);
    contents.push({ before, after });
  }
  if (total <= MAX_CONTENT_BYTES) files.forEach((f, i) => { if (contents[i]) Object.assign(f, contents[i]); });
  return {
    summary,
    repo: basename(repo),
    branch: await currentBranch(repo),
    base: await head(repo),
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

/** How many distinct approvals a rule needs, given the reviewers it covers. */
export function approvalsNeeded(rule, reviewers = []) {
  if (String(rule).startsWith("quorum:")) return Math.max(1, parseInt(String(rule).slice("quorum:".length), 10) || 1);
  if (rule === "all_approve") return Math.max(1, reviewers.length);
  return 1;
}

/** Of several rules, the one that needs the most approvals. */
export function strictestRule(rules, reviewers = []) {
  return rules.filter(Boolean).reduce((a, b) => (approvalsNeeded(b, reviewers) > approvalsNeeded(a, reviewers) ? b : a), "any_one_approves");
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

/** The approved code changes the gate holds, newest first; with `base`, only those made against that commit. */
export async function approvedChanges(gate, { base, limit } = {}) {
  const r = await api(gate, `/api/tasks?kind=${TASK_KIND}&state=completed${limit ? `&limit=${limit}` : ""}`);
  const tasks = r?.tasks ?? [];
  return base === undefined ? tasks : tasks.filter((t) => (t.output?.base ?? null) === base);
}

/** The evidence behind a task's decisions, from the read API. */
export const evidence = (gate, id) => api(gate, `/api/tasks/${encodeURIComponent(id)}/evidence`);

// -- RFC 6902, the part decide.override uses -----------------------------------

const UNSAFE_KEYS = new Set(["__proto__", "prototype", "constructor"]);

/** Apply add, remove and replace operations to a JSON value. Other operations are refused. */
export function applyJsonPatch(doc, ops) {
  let out = structuredClone(doc);
  for (const op of ops) {
    if (!["add", "remove", "replace"].includes(op.op)) throw new Error(`Unsupported patch operation: ${op.op}`);
    const tokens = op.path === "" ? [] : String(op.path).split("/").slice(1).map((t) => t.replace(/~1/g, "/").replace(/~0/g, "~"));
    if (tokens.some((t) => UNSAFE_KEYS.has(t))) throw new Error(`Refused path: ${op.path}`);
    if (tokens.length === 0) {
      if (op.op === "remove") throw new Error("Cannot remove the root");
      out = structuredClone(op.value);
      continue;
    }
    let parent = out;
    for (const t of tokens.slice(0, -1)) {
      parent = Array.isArray(parent) ? parent[Number(t)] : (parent && Object.hasOwn(parent, t) ? parent[t] : undefined);
      if (parent === undefined || parent === null || typeof parent !== "object") throw new Error(`Path not found: ${op.path}`);
    }
    const last = tokens.at(-1);
    if (Array.isArray(parent)) {
      const i = last === "-" ? parent.length : Number(last);
      if (!Number.isInteger(i) || i < 0 || i > parent.length) throw new Error(`Path not found: ${op.path}`);
      if (op.op === "add") parent.splice(i, 0, structuredClone(op.value));
      else if (op.op === "remove") { if (i >= parent.length) throw new Error(`Path not found: ${op.path}`); parent.splice(i, 1); }
      else { if (i >= parent.length) throw new Error(`Path not found: ${op.path}`); parent[i] = structuredClone(op.value); }
    } else {
      const own = Object.hasOwn(parent, last);
      if (op.op === "remove") { if (!own) throw new Error(`Path not found: ${op.path}`); delete parent[last]; }
      else if (op.op === "replace" && !own) throw new Error(`Path not found: ${op.path}`);
      else parent[last] = structuredClone(op.value);
    }
  }
  return out;
}

// -- the trust policy ---------------------------------------------------------------

/**
 * A trust policy says whose approvals count and which agents may commit:
 * { source, workspace, rule, reviewers: { uri: [jwk] }, agents: { uri: [jwk] },
 *   people: [allowed_signers line] }. The keys are pinned, so a note signed
 * with any other key does not verify, whatever the note itself carries.
 */
export function policyFromTrust(json, source) {
  if (!json || json.chap_trust !== 1) throw new Error(`${source} is not a CHAP trust file (chap_trust: 1)`);
  return {
    source, workspace: json.workspace ?? null, rule: json.rule ?? "any_one_approves",
    reviewers: json.reviewers ?? {}, agents: json.agents ?? {}, people: json.people ?? [],
  };
}

/** The trust policy in a file. */
export async function readTrustFile(path) {
  return policyFromTrust(JSON.parse(await readFile(path, "utf8")), path);
}

/** The trust policy a repository holds at a revision, or null when it holds none. */
export async function trustAtRef(repo, ref, name = TRUST_FILE) {
  const text = await blobAt(repo, ref, name);
  return text === null ? null : policyFromTrust(JSON.parse(text), `${ref}:${name}`);
}

/**
 * The policy a running gate gives for a task's evidence: the reviewers are
 * the humans chap.config.json names, with the keys the workspace records
 * for them, and every agent member is an agent. A participant who joined
 * under another URI counts for nothing, whatever type it gave itself.
 */
export function onlinePolicy(gate, ev) {
  const configured = new Set((gate.config.humans ?? []).map((h) => h.uri));
  const reviewers = {};
  const agents = {};
  for (const [uri, m] of Object.entries(ev.members ?? {})) {
    if (m.type === "human" && configured.has(uri)) reviewers[uri] = m.keys ?? [];
    if (m.type === "agent") agents[uri] = m.keys ?? [];
  }
  return { source: `the gate at ${gate.base}`, workspace: ev.workspace, rule: reviewRule(gate).rule, reviewers, agents, people: [] };
}

// -- the evidence a commit carries -----------------------------------------------

/**
 * The note written beside a commit, from the task's evidence: the task, the
 * review rule and who the final round was addressed to, the agent's signed
 * submission that opened the round, every decision of the round as the
 * chain holds it, the artefact as proposed and as approved, the keys on
 * record for the reviewers and the agent, and the chain head. The keys and
 * the rule are a record of what the workspace held; a verifier checks the
 * signatures against the keys its own policy pins.
 */
export function buildNote(ev, gateUrl) {
  const settling = ev.decisions.filter((d) => ["decide.approve", "decide.override"].includes(d.envelope.method)).at(-1);
  if (!settling) throw new Error(`Task ${ev.task.task_id} has no approval on the chain`);
  const submission = ev.submissions.filter((x) => x.seq < settling.seq).at(-1);
  const round = ev.decisions.filter((d) => d.seq > (submission?.seq ?? -1) && d.seq <= settling.seq);
  const approved = ev.task.output;
  const proposed = submission?.envelope.params?.output ?? submission?.envelope.params?.artefact ?? null;
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
    submission: submission ? { method: submission.envelope.method, seq: submission.seq, arrived: submission.arrived, envelope: submission.envelope } : null,
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
 * Check a note against a trust policy. The agent the note names must be one
 * the policy trusts, and its signed submission must be the proposed
 * artefact. Each counted approval must come from a reviewer the policy
 * names, never from an agent, sign the digest of the proposed artefact and
 * verify against that reviewer's pinned key; an override's operations must
 * lead from the proposed artefact to the approved one. The policy's rule
 * must be met by that many distinct reviewers, and under a rule that needs
 * more than one approval an override does not count, since the others
 * approved the proposed version. The rule the note itself records is not
 * read. Returns a list of problems, empty when the note holds.
 */
export async function checkApproval(note, policy, { taskId = note.task_id, allowUnsigned = false } = {}) {
  const problems = [];
  if (note.task_id !== taskId) problems.push(`the note is for ${note.task_id}, the commit names ${taskId}`);
  if (policy.workspace && note.workspace !== policy.workspace) problems.push(`the note is from ${note.workspace}, and ${policy.source} covers ${policy.workspace}`);
  const approved = note.approved_artefact;
  const proposed = note.proposed_artefact;
  if (!approved || typeof approved.patch !== "string") problems.push("the note holds no approved patch");
  if (!proposed) problems.push("the note holds no proposed artefact");
  const proposedDigest = proposed ? await contentHash(proposed) : null;

  const agentKeys = policy.agents?.[note.agent];
  if (!agentKeys) problems.push(`${note.agent} is not an agent ${policy.source} names`);
  const sub = note.submission?.envelope;
  if (!sub) problems.push("the note holds no submission from the agent");
  else {
    const p = sub.params ?? {};
    const artefact = sub.method === "review.request" ? p.artefact : p.output;
    if (!["task.complete", "review.request"].includes(sub.method) || p.task_id !== taskId) problems.push("the submission in the note is not one for this task");
    else if (p.from !== note.agent) problems.push(`the submission was made by ${p.from}, and the note names ${note.agent}`);
    else if (!proposedDigest || (await contentHash(artefact)) !== proposedDigest) problems.push("the submission is not the proposed artefact");
    else if (sub.sig) {
      if (agentKeys) { const v = envelopeVerifies(sub, agentKeys); if (!v.ok) problems.push(`the agent's signature on its submission: ${v.reason}`); }
    } else if (!allowUnsigned) problems.push("the agent's submission is unsigned");
  }

  const approvers = new Set();
  let overridden = false;
  for (const d of note.decisions ?? []) {
    const env = d.envelope ?? {};
    const p = env.params ?? {};
    if (!["decide.approve", "decide.override"].includes(env.method)) continue;
    const from = p.from;
    if (p.task_id !== taskId) { problems.push(`an approval by ${from} names another task`); continue; }
    if (from === note.agent || policy.agents?.[from]) { problems.push(`${from} approved the change, and is an agent`); continue; }
    const keys = policy.reviewers?.[from];
    if (!keys) { problems.push(`${from} approved the change, and is not a reviewer ${policy.source} names`); continue; }
    if (p.approved_artefact_digest !== proposedDigest) { problems.push(`the approval by ${from} does not sign the digest of the proposed artefact`); continue; }
    if (env.sig) {
      const v = envelopeVerifies(env, keys);
      if (!v.ok) { problems.push(`the signature of ${from}: ${v.reason}`); continue; }
    } else if (!allowUnsigned) { problems.push(`the approval by ${from} is unsigned (pass --allow-unsigned where signatures are off)`); continue; }
    if (env.method === "decide.override") {
      overridden = true;
      try {
        if (!deepEqual(applyJsonPatch(proposed, p.diff ?? []), approved)) problems.push("the reviewer's operations applied to the proposed artefact do not give the approved one");
      } catch (e) { problems.push(`the reviewer's operations do not apply: ${e.message}`); }
    }
    approvers.add(from);
  }
  const reviewerList = Object.keys(policy.reviewers ?? {});
  const need = approvalsNeeded(policy.rule, reviewerList);
  if (!overridden && approved && proposed && !deepEqual(proposed, approved)) problems.push("approved as written, yet the approved artefact differs from the proposed one");
  if (need > 1 && overridden) problems.push(`under ${policy.rule} an edit settles the review with one reviewer's decision; request changes so the edited version is approved again`);
  else if (approvers.size < need) problems.push(`${policy.rule} needs ${need} approval${need === 1 ? "" : "s"} from reviewers ${policy.source} names; ${approvers.size} on record`);
  if (policy.rule === "all_approve") for (const uri of reviewerList) if (!approvers.has(uri)) problems.push(`all_approve: no approval from ${uri}`);
  return problems;
}

/**
 * Check that a commit (or a commit about to be made) is the approved change:
 * its parent is the commit the change was proposed against, what the desk
 * showed of the approved patch is the change git makes with it, and it
 * gives the commit's tree. Returns a list of problems.
 */
export async function checkChange(repo, { parent, parentTree, tree, approved }) {
  const problems = [];
  if ((approved?.base ?? null) !== (parent ?? null)) {
    problems.push(`the change was approved against ${approved?.base ? approved.base.slice(0, 12) : "an empty repository"}, and this commit's parent is ${parent ? parent.slice(0, 12) : "none"}; propose it again on this base`);
    return problems;
  }
  const c = await faithfulCheck(repo, parentTree, approved.patch);
  if (!c.ok) problems.push(c.reason);
  else if (c.tree !== tree) problems.push("the approved patch applied to the parent does not give this commit's tree");
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
  const candidates = (jwks ?? []).filter((k) => k && typeof k.x === "string" && (k.kid === kid || !k.kid));
  if (!candidates.length) return { ok: false, reason: `no pinned key with kid ${kid}` };
  for (const jwk of candidates) {
    try {
      const key = createPublicKey({ key: { kty: "OKP", crv: "Ed25519", x: jwk.x }, format: "jwk" });
      if (cryptoVerify(null, bytes, key, Buffer.from(b64, "base64"))) return { ok: true, kid };
    } catch { /* the next key */ }
  }
  return { ok: false, reason: `the signature does not verify against the key ${kid}` };
}

export { canonicalize, contentHash, git };
