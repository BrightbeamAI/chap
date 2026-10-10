// The review desk. One page, served by the process that owns the store,
// the same in every template. It lists what waits for the reviewer, shows
// the artefact under review in the form its shape calls for, and sends
// decide.approve, decide.reject or decide.override as CHAP calls to
// POST /chap, each carrying the digest of the artefact shown, so a decision
// on a draft that changed under the reviewer is refused. Under
// security-signed/1.0 it signs in the browser with a key it generates and
// keeps in the browser's storage. Activity reads the chain; Insights counts
// what happened.

import { contentHash, deepEqual, generateSigner, jsonPatch, makeClient, signerFromJwk } from "./chap-client.mjs";
import { ago, artefactAnomalies, el, renderArtefact, renderDiff, renderJson, shapeOf, stateBadge, titleOf } from "./render.js";
import { parsePatch } from "./diff.js";
import { decisionsOf, outcomeOf, renderInsights, renderMarkdown } from "./insights.js";
import { followUpPrompt } from "./followup.js";

const $ = (id) => document.getElementById(id);
const POLL_MS = 4000;
const CHAIN_PAGE = 200;

const state = {
  config: null, client: null, tasks: [], reviews: [], selectedId: null, view: "review",
  editing: null, health: null, chain: { entries: [], from: null, total: 0, verified: null }, chainShown: -1,
  analytics: { report: false, refine: null, cases: false }, busy: false, lost: false,
  // The lists are brief; the task on show is read whole. A branch remembers
  // the commit on show and the commits opened.
  detail: null, range: { taskId: null, index: 0, read: new Set() }, reads: new Map(),
  // Comments on lines, by task, until they go with a decision; the reviews
  // already seen waiting, so a new one is brought forward.
  comments: new Map(), waitingSeen: null,
};

// -- helpers --------------------------------------------------------------------

const hashParams = () => new URLSearchParams(location.hash.slice(1));
function setHash(changes) {
  const p = hashParams();
  for (const [k, v] of Object.entries(changes)) { if (v === null || v === undefined || v === "") p.delete(k); else p.set(k, v); }
  const next = p.toString();
  if (next !== location.hash.slice(1)) history.replaceState(null, "", `#${next}`);
}
async function getJson(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path} answered ${res.status}`);
  return res.json();
}
let toastTimer = null;
function toast(message, kind = "") {
  document.querySelector(".toast")?.remove();
  const t = el("div", { class: `toast ${kind}`, text: message });
  document.body.append(t);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.remove(), 4000);
}
function notice(message, kind = "") { const n = $("notice"); n.textContent = message; n.className = `notice ${kind}`; }
function chip(id, text, cls) { const c = $(id); c.className = `chip ${cls}`; c.lastElementChild.textContent = text; }

const selected = () => state.tasks.find((t) => t.task_id === state.selectedId) ?? null;

/** The whole view of the selected task, once read. */
const current = () => (state.detail?.id === state.selectedId ? state.detail.view : null);

/** What changes when a task does: its state, its last update, the submission under review. */
const detailKey = (t) => `${t.state}|${t.updated_at}|${t.submission?.seq ?? ""}|${(t.decision_log ?? []).length}`;

/** Read the selected task whole. Returns the view it replaced, if it was of the same task. */
async function loadDetail(t) {
  const prior = state.detail?.id === t.task_id ? state.detail.view : null;
  const view = await getJson(`/api/tasks/${encodeURIComponent(t.task_id)}`);
  if (state.selectedId !== t.task_id) return prior;
  state.detail = { id: t.task_id, key: detailKey(t), view };
  return prior;
}

/** When the current review round opened: the last time the task entered review_requested. */
const roundStart = (t) => (t.history ?? []).filter((h) => h.state === "review_requested").at(-1)?.ts ?? t.review?.requested_at ?? "";

/**
 * Whether a task waits for this reviewer: under review, addressed to them,
 * and not yet decided by them in this round. A rule that needs more than
 * one approval keeps the task under review after the first.
 */
const mine = (t) => t.state === "review_requested" && !!t.review?.requested_to?.includes(state.client?.from)
  && !decisionsOf(t).some((d) => d.reviewer === state.client?.from && String(d.ts) >= roundStart(t));

// -- boot -----------------------------------------------------------------------

async function boot() {
  const saved = localStorage.getItem("chap-desk-theme");
  if (saved) document.documentElement.dataset.theme = saved;
  $("theme").onclick = () => {
    const dark = matchMedia("(prefers-color-scheme: dark)").matches;
    const current = document.documentElement.dataset.theme || (dark ? "dark" : "light");
    const next = current === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("chap-desk-theme", next);
  };
  // The text size: a step on the scale the root size is multiplied by,
  // kept in this browser for next time.
  const steps = [0.875, 1, 1.125, 1.25, 1.375, 1.5];
  let step = steps.indexOf(Number(localStorage.getItem("chap-desk-text") ?? 1));
  if (step < 0) step = 1;
  const applyText = (say) => {
    document.documentElement.style.setProperty("--scale", String(steps[step]));
    $("text-smaller").disabled = step === 0;
    $("text-larger").disabled = step === steps.length - 1;
    if (say) { localStorage.setItem("chap-desk-text", String(steps[step])); toast(`Text size ${Math.round(steps[step] * 100)}%`); }
  };
  applyText(false);
  $("text-smaller").onclick = () => { step = Math.max(0, step - 1); applyText(true); };
  $("text-larger").onclick = () => { step = Math.min(steps.length - 1, step + 1); applyText(true); };
  $("help-btn").onclick = () => { $("help").hidden = false; };
  $("help-close").onclick = () => { $("help").hidden = true; };
  $("help").onclick = (e) => { if (e.target === $("help")) $("help").hidden = true; };
  for (const tab of document.querySelectorAll(".tab")) tab.onclick = () => showView(tab.dataset.view);
  $("search").oninput = renderQueue;
  $("act-filter").oninput = renderActivity;
  $("act-refused").onchange = renderActivity;
  $("act-more").onclick = () => loadChain({ earlier: true });
  $("approve").onclick = () => decide("approve");
  $("changes").onclick = () => decide("changes");
  $("reject").onclick = () => decide("reject");
  $("override").onclick = () => decide("override");
  $("r-edit").onclick = toggleEdit;
  $("cancel-edit").onclick = () => { state.editing = null; renderReview(); };
  $("prompt-copy").onclick = () => copyText($("prompt-text").textContent);
  $("prompt-close").onclick = () => { $("prompt").hidden = true; };
  $("r-prompt-copy").onclick = () => copyText($("r-prompt").textContent);
  document.addEventListener("keydown", onKey);

  state.config = await getJson("/api/config");
  $("workspace").textContent = state.config.workspace;
  const short = state.config.profiles.map((p) => p.replace(/\/1\.0$/, "").replace("security-signed", "signed").replace("audit-scitt", "scitt"));
  $("profiles").replaceChildren(el("span", { class: "chip profiles", title: state.config.profiles.join(", "), text: short.join(" · ") }));
  chip("chain", state.config.chain_enabled ? "chain on" : "chain off", state.config.chain_enabled ? "good" : "");
  // Each reviewer by name, and by URI where two share a name; the URI is
  // in the tooltip.
  const select = $("reviewer");
  const humans = state.config.humans;
  const shared = (name) => humans.filter((h) => h.display_name === name).length > 1;
  for (const h of humans) select.append(el("option", { value: h.uri, title: h.uri, text: h.display_name ? (shared(h.display_name) ? `${h.display_name} (${h.uri})` : h.display_name) : h.uri }));
  const wanted = hashParams().get("reviewer");
  if (wanted && humans.some((h) => h.uri === wanted)) select.value = wanted;
  const titleSelect = () => { select.title = `Deciding as ${select.value}`; };
  titleSelect();
  select.onchange = () => { titleSelect(); return setReviewer(select.value); };
  await setReviewer(select.value);
  const task = hashParams().get("task");
  if (task) select_(task);
  showView(hashParams().get("view") || "review");
  setInterval(() => { if (!document.hidden && !state.busy) refresh().catch(lost); }, POLL_MS);
  probeAnalytics();
  setInterval(() => { if (!document.hidden) probeAnalytics(); }, 30_000);
}

// Under security-signed/1.0 the desk signs in the browser. The key is
// generated here, kept in localStorage, and registered at participant.join,
// which the coordinator accepts unsigned. A re-join cannot add a key, so a
// cleared browser storage means a new participant URI.
async function signerFor(uri) {
  if (!state.config.require_signatures) return null;
  const key = `chap-desk-key:${state.config.workspace}:${uri}`;
  const saved = localStorage.getItem(key);
  if (saved) return signerFromJwk(uri, JSON.parse(saved));
  const signer = await generateSigner(uri);
  localStorage.setItem(key, JSON.stringify(signer.privateJwk));
  return signer;
}

async function setReviewer(uri) {
  setHash({ reviewer: uri });
  const signer = await signerFor(uri);
  state.client = makeClient({ workspace: state.config.workspace, from: uri, signer });
  chip("signing", signer ? `signed · ${signer.kid.slice(0, 8)}` : "unsigned", signer ? "good" : "");
  $("signing").title = signer ? `Decisions are signed in this browser as ${uri} with the Ed25519 key ${signer.kid}, fingerprint ${await fingerprint(signer.publicJwk)}. Give the fingerprint to whoever keeps the trust policy, so your approvals count.` : "This workspace does not require signatures";
  if (signer) {
    const me = state.config.humans.find((h) => h.uri === uri);
    try { await state.client.call("participant.join", { type: "human", role: me?.role ?? "reviewer", display_name: me?.display_name, jwks: { keys: [signer.publicJwk] } }); }
    catch (e) { toast(`Could not join as ${uri}: ${e.message}`, "bad"); }
  }
  state.chainShown = -1;
  await refresh().catch(lost);
}

/** A key's fingerprint as ssh-keygen and trust.mjs print it: SHA256 of the raw public key, base64. */
async function fingerprint(jwk) {
  const raw = Uint8Array.from(atob(jwk.x.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", raw));
  return "SHA256:" + btoa(String.fromCharCode(...digest)).replace(/=+$/, "");
}

function lost(e) {
  state.lost = true;
  chip("connection", "connection lost", "bad");
  console.warn(e);
}

// -- data -----------------------------------------------------------------------

async function refresh() {
  // Every open task, and the most recent ones for the decided list.
  const [health, open, recent] = await Promise.all([getJson("/api/health?desk=1"), getJson("/api/tasks?state=created,in_progress,review_requested&brief=1"), getJson("/api/tasks?limit=200&brief=1")]);
  if (state.lost) { state.lost = false; toast("Connected again", "ok"); }
  chip("connection", "live", "good");
  $("connection").title = `${health.members} members, ${health.tasks} tasks, ${health.audit} chain entries`;
  state.health = health;
  const before = selected();
  const byId = new Map();
  for (const t of [...open.tasks, ...recent.tasks]) byId.set(t.task_id, t);
  state.tasks = [...byId.values()].sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""));
  renderQueue();
  bringForward();
  const now = selected();
  if (before && !now) { state.selectedId = null; state.detail = null; state.editing = null; renderReview(); }
  else if (now && detailKey(now) !== state.detail?.key) {
    const prior = await loadDetail(now);
    const fresh = current();
    if (prior && fresh && prior.state === fresh.state && !deepEqual(prior.artefact, fresh.artefact)) {
      if (state.editing) notice("The artefact changed while you were editing it; your edit is kept, the decision binds to the new artefact.", "bad");
      toast("The artefact under review changed; this is the current one.");
    }
    renderReview();
  }
  if (health.audit !== state.chainShown) {
    state.chain.total = health.audit;
    $("activity-count").textContent = String(health.audit);
    if (state.view === "activity") await loadChain();
    if (state.config.chain_enabled) await verifyChain();
    state.chainShown = health.audit;
  }
  if (state.view === "insights") renderInsightsView();
}

/**
 * A review that has just arrived for this reviewer is brought forward: it
 * opens when nothing waiting is open, and is announced otherwise. The tab's
 * title counts what waits.
 */
function bringForward() {
  const waiting = state.tasks.filter((t) => mine(t));
  document.title = waiting.length ? `(${waiting.length}) CHAP review desk` : "CHAP review desk";
  const ids = new Set(waiting.map((t) => t.task_id));
  const first = state.waitingSeen === null;
  const arrived = first ? [] : waiting.filter((t) => !state.waitingSeen.has(t.task_id));
  state.waitingSeen = ids;
  const current = selected();
  const busy = current && mine(current);
  if (first) {
    if (!state.selectedId && waiting.length) select_(waiting.at(-1).task_id);
    return;
  }
  if (!arrived.length) return;
  const next = arrived.at(-1);
  if (!busy) { select_(next.task_id); toast(`Waiting for your review: ${titleOf(next)}`, "ok"); }
  else if (next.task_id !== state.selectedId) toast(`Also waiting for your review: ${titleOf(next)}. It is in the queue.`);
}

async function loadChain({ earlier = false } = {}) {
  const total = state.health?.audit ?? state.chain.total;
  const from = earlier ? Math.max(0, (state.chain.from ?? total) - CHAIN_PAGE) : Math.max(0, total - CHAIN_PAGE);
  const to = earlier ? state.chain.from : total;
  try {
    const { entries } = await state.client.call("audit.read", { range: { from_seq: from, ...(to !== undefined && to !== null && to < total ? { to_seq: to } : {}) } });
    if (earlier) state.chain.entries = [...entries, ...state.chain.entries];
    else {
      // Keep the earlier pages already loaded, replace the tail.
      const kept = state.chain.entries.filter((e) => e.seq < from);
      state.chain.entries = [...kept, ...entries];
    }
    state.chain.from = Math.min(from, state.chain.from ?? from);
    renderActivity();
  } catch (e) {
    $("activity").replaceChildren(el("div", { class: "empty", text: `The chain could not be read: ${e.message}` }));
  }
}

async function verifyChain() {
  try {
    const v = await state.client.call("audit.verify_chain", {});
    state.chain.verified = v.status === "verified" && v.ok === true;
    chip("chain", state.chain.verified ? `chain verified · ${v.entries_checked}` : `chain ${v.status}`, state.chain.verified ? "good" : "bad");
  } catch { state.chain.verified = false; chip("chain", "chain not verified", "bad"); }
}

// The pages chap-analytics writes, when the analytics script has run:
// summary.json says when and from how much, and the rest are linked.
async function probeAnalytics() {
  let summary = null, refine = null;
  try { const r = await fetch("/analytics/summary.json"); summary = r.ok ? await r.json() : null; } catch { /* none */ }
  if (summary) { try { const r = await fetch("/analytics/refine.md"); refine = r.ok ? await r.text() : null; } catch { /* none */ } }
  const changed = summary?.written !== state.analytics.summary?.written;
  state.analytics = { report: !!summary, cases: !!summary, summary, refine };
  if (changed && state.view === "insights") renderInsightsView();
}

// -- the queue ------------------------------------------------------------------

function renderQueue() {
  const q = $("queue");
  const needle = $("search").value.trim().toLowerCase();
  const match = (t) => !needle || [titleOf(t), t.task_id, t.assignee, t.kind, t.state].join(" ").toLowerCase().includes(needle);
  const waiting = state.tasks.filter((t) => mine(t) && match(t));
  const open = state.tasks.filter((t) => !mine(t) && ["created", "in_progress", "review_requested"].includes(t.state) && match(t));
  const decided = state.tasks.filter((t) => ["completed", "declined", "cancelled", "superseded"].includes(t.state) && match(t)).slice(0, 60);
  q.replaceChildren();
  const group = (title, list, empty) => {
    q.append(el("h3", {}, title, el("span", { class: "count", text: String(list.length) })));
    if (!list.length) { q.append(el("div", { class: "empty", text: empty })); return; }
    for (const t of list) {
      const o = outcomeOf(t);
      const approvals = t.state === "review_requested" ? (t.review?.decisions ?? []).filter((d) => d.kind === "approve").length : 0;
      q.append(el("button", { class: `item${t.task_id === state.selectedId ? " selected" : ""}`, onclick: () => select_(t.task_id), dataset: { id: t.task_id } },
        el("div", { class: "title" }, el("span", { text: titleOf(t) })),
        el("div", { class: "meta" },
          el("span", { text: t.assignee?.replace(/^agent:/, "") ?? "" }),
          el("span", { text: t.kind }),
          el("span", { text: ago(t.updated_at ?? t.created_at) }),
          approvals ? el("span", { text: `${approvals} approval${approvals === 1 ? "" : "s"}, ${t.review.rule}` }) : null,
          !o ? el("span", { class: "state" }, stateBadge(t.state)) : el("span", { class: "state" }, el("span", { class: `badge ${o === "reject" ? "bad" : o === "override" ? "warn" : "ok"}`, text: o === "override" ? "edited" : o === "reject" ? "rejected" : "approved" })))));
    }
  };
  group("Waiting for you", waiting, "Nothing is waiting for you.");
  group("In progress", open, "No other task is open.");
  group("Decided", decided, "Nothing has been decided yet.");
}

function select_(id) {
  state.selectedId = id;
  state.editing = null;
  if (state.range.taskId !== id) {
    if (!state.reads.has(id)) state.reads.set(id, new Set());
    state.range = { taskId: id, index: 0, read: state.reads.get(id) };
  }
  setHash({ task: id });
  renderQueue();
  renderReview();
  if (state.view !== "review") showView("review");
  const t = selected();
  if (t && state.detail?.id !== id) loadDetail(t).then(() => { if (state.selectedId === id) renderReview(); }).catch(lost);
}

/** Open another commit of the branch on show. */
function selectCommit(i) {
  const t = current();
  const commits = t?.artefact?.commits;
  if (!Array.isArray(commits) || i < 0 || i >= commits.length) return;
  state.range.index = i;
  renderReview();
  document.querySelector(".commit-detail")?.scrollIntoView({ block: "start", behavior: "smooth" });
}

// -- the review -----------------------------------------------------------------

function renderReview() {
  const t = current() ?? selected();
  $("review-empty").hidden = !!t;
  $("review").hidden = !t;
  $("decision-bar").hidden = !(t && mine(t));
  if (!t) return;
  const loading = !current();
  const artefact = t.artefact ?? t.output ?? null;
  $("r-title").textContent = titleOf(t);
  $("r-state").replaceWith(Object.assign(stateBadge(t.state), { id: "r-state" }));
  $("r-kind").textContent = t.kind;
  // A branch's own header says what it is now; the task's input is its first proposal.
  $("r-input-card").hidden = t.kind === "commit_range";
  const approvalsSoFar = t.state === "review_requested" && t.review?.rule && t.review.rule !== "any_one_approves"
    ? `${(t.review.decisions ?? []).filter((d) => d.kind === "approve").length} so far` : null;
  const facts = [["agent", t.assignee], ["reviewers", t.review?.requested_to?.join(", ")], ["rule", t.review?.rule], ["approvals", approvalsSoFar], ["opened", t.review?.requested_at ? `${ago(t.review.requested_at)} ago` : null], ["mode", t.mode], ["task", t.task_id]];
  $("r-facts").replaceChildren(...facts.filter(([, v]) => v).map(([k, v]) => el("span", {}, `${k} `, el("b", { text: v }))));
  const input = t.input ?? {};
  const inputBody = $("r-input");
  const plain = (v) => typeof v === "string" || typeof v === "number" || typeof v === "boolean" || (Array.isArray(v) && v.every((x) => typeof x === "string" || typeof x === "number"));
  const simple = Object.entries(input).filter(([, v]) => plain(v));
  const rest = Object.fromEntries(Object.entries(input).filter(([, v]) => !plain(v)));
  inputBody.replaceChildren(el("div", { class: "stack" },
    simple.length ? el("dl", { class: "kv" }, simple.map(([k, v]) => [el("dt", { text: k }), el("dd", { text: Array.isArray(v) ? v.join(", ") : String(v) })])) : null,
    Object.keys(rest).length ? renderJson(rest) : null,
    !simple.length && !Object.keys(rest).length ? el("span", { class: "muted small", text: "No input." }) : null,
  ));
  $("r-artefact-title").textContent = t.state === "review_requested" ? "The artefact under review" : t.state === "completed" ? "The artefact as decided" : "The artefact";
  // Under a rule that needs more than one approval, an edit would settle the
  // review with one reviewer's decision, so the desk offers no edit there:
  // request changes, and the edited version is approved again by everyone.
  const multi = multiRule(t);
  const branch = shapeOf(artefact) === "commits";
  const blocked = !loading && anomaliesOf(t).length > 0;
  $("r-edit").hidden = !(mine(t) && artefact !== null && !multi && !blocked && !branch && !loading);
  $("r-edit-hint").hidden = !(mine(t) && (multi || blocked || branch));
  $("r-edit-hint").textContent = blocked ? (branch ? "A commit of this branch cannot be shown faithfully, so the branch cannot be approved here." : "This patch cannot be shown faithfully, so it cannot be approved or edited here.")
    : branch ? "A branch is approved as its commits stand. Request changes, and the agent amends them."
    : multi ? `Under ${t.review.rule} an edit would settle the review alone. Request changes, and the agent revises.` : "";
  $("approve").disabled = blocked || loading;
  $("r-edit").textContent = state.editing ? "Editing" : "Edit";
  $("override").hidden = !state.editing;
  $("cancel-edit").hidden = !state.editing;
  if (branch && !loading) {
    const commits = artefact.commits;
    state.range.index = Math.min(state.range.index, commits.length - 1);
    state.range.read.add(commits[state.range.index].sha);
  }
  const body = $("r-artefact");
  const commenting = mine(t) && !loading && !state.editing ? commentingFor(t.task_id) : null;
  body.replaceChildren(artefact === null ? el("span", { class: "muted small", text: "Nothing submitted yet." })
    : loading ? el("span", { class: "muted small", text: "Reading the artefact." })
    : renderArtefact(artefact, { editing: state.editing, commenting, range: branch ? { index: state.range.index, read: state.range.read, onSelect: selectCommit } : null }));
  renderContext(t, artefact, loading);
  countComments();
  const history = $("r-history");
  const events = [];
  for (const h of t.history ?? []) events.push({ ts: h.ts, who: h.from, what: h.state.replace(/_/g, " "), note: h.note });
  for (const d of decisionsOf(t)) events.push({ ts: d.ts, who: d.reviewer, what: d.kind === "override" ? "approved with an edit" : d.kind === "reject" ? (d.request_revision ? "sent back for a revision" : "rejected") : d.kind === "approve" ? "approved" : d.kind, note: d.rationale ?? d.comment, tags: d.tags });
  events.sort((a, b) => a.ts.localeCompare(b.ts));
  history.replaceChildren(...events.map((e) => el("div", { class: "event" }, el("span", { class: "when", title: e.ts, text: `${ago(e.ts)} ago` }), el("span", {}, el("b", { text: e.what }), e.who ? el("span", { class: "muted", text: ` by ${e.who}` }) : null, e.note ? el("div", { class: "note", text: e.note }) : null, e.tags?.length ? el("div", { class: "small muted", text: e.tags.join(", ") }) : null))));
  $("r-history-card").hidden = !events.length;
}

/** The comments on lines of a task, kept until they go with a decision. */
function commentsOf(id) {
  if (!state.comments.has(id)) state.comments.set(id, []);
  return state.comments.get(id);
}

/** What the diff needs to take comments on its lines for a task. */
function commentingFor(id) {
  return {
    comments: commentsOf(id),
    onAdd: (c) => { const saved = { ...c, id: `c${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}` }; commentsOf(id).push(saved); countComments(); return saved; },
    onRemove: (cid) => { const list = commentsOf(id); const i = list.findIndex((x) => x.id === cid); if (i >= 0) list.splice(i, 1); countComments(); },
  };
}

/** Say how many comments go with the decision. */
function countComments() {
  const t = current();
  const n = t ? commentsOf(t.task_id).length : 0;
  $("comment-count").textContent = n ? `${n} comment${n === 1 ? "" : "s"} on lines go with your decision` : "";
}

/** The command that proposes a task's work again, as the agent would run it. */
function commandFor(t) {
  const a = t.output ?? t.artefact ?? {};
  const dir = state.config?.gate_dir;
  if (!dir) return null;
  const q = (s) => (/^[A-Za-z0-9_./:=@%+,-]+$/.test(s) ? s : `"${String(s).replace(/(["\\$`])/g, "\\$1")}"`);
  const flags = [a.drafted_by ? `--by ${q(a.drafted_by)}` : null, a.model ? `--model ${q(a.model)}` : `--model "the model you run as"`].filter(Boolean).join(" ");
  if (Array.isArray(a.commits)) return `node ${q(`${dir}/propose-branch.mjs`)} ${String(a.base ?? "").slice(0, 12)}..${a.branch ?? "HEAD"} ${flags} --wait`;
  return `node ${q(`${dir}/propose.mjs`)} ${q(a.summary ?? "what the change does")} ${flags} --wait --commit`;
}

/** The prompt the last decision on a task makes for the agent, or null. */
function promptFor(t) {
  const decision = (t.decision_log ?? []).at(-1);
  if (!decision) return null;
  const name = state.config?.humans?.find((h) => h.uri === decision.reviewer)?.display_name ?? decision.reviewer;
  return followUpPrompt({ task: t, decision, reviewer: name, command: commandFor(t) });
}

/** The agent's own note, what changed since the last look, and the decision as a prompt. */
function renderContext(t, artefact, loading) {
  const note = !loading && typeof artefact?.context === "string" ? artefact.context : null;
  $("r-context-card").hidden = !note;
  if (note) {
    $("r-context-title").textContent = `Context from ${artefact.drafted_by ?? t.assignee ?? "the agent"}`;
    const box = $("r-context");
    box.innerHTML = renderMarkdown(note);
  }
  const since = !loading && typeof artefact?.since?.patch === "string" && artefact.since.patch.trim() ? artefact.since : null;
  $("r-since-card").hidden = !since;
  if (since) $("r-since").replaceChildren(el("div", { class: "small muted", style: "margin-bottom:10px", text: `What changed since the version you reviewed${since.head ? ` (up to ${String(since.head).slice(0, 7)})` : ""}.` }), renderDiff(since.patch, {}));
  // The prompt of a decision that closed the round; a new round waiting has none yet.
  const prompt = !loading && t.state !== "review_requested" ? promptFor(t) : null;
  $("r-prompt-card").hidden = !prompt;
  if (prompt) $("r-prompt").textContent = prompt;
}

/** Show the prompt a decision makes, to copy and give to the agent. */
function showPrompt(prompt) {
  $("prompt-text").textContent = prompt;
  $("prompt").hidden = false;
  $("prompt-copy").focus();
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); toast("Copied the prompt", "ok"); }
  catch {
    const range = document.createRange();
    range.selectNodeContents($("prompt-text"));
    const sel = getSelection(); sel.removeAllRanges(); sel.addRange(range);
    toast("Selected the prompt: copy it with the keyboard");
  }
}

/** Whether a task's review needs more than one approval, so an edit would settle it alone. */
const multiRule = (t) => !!t.review?.rule && t.review.rule !== "any_one_approves";

/** What the desk cannot show faithfully of a code change or a branch. */
const anomaliesOf = (t) => artefactAnomalies(t.artefact);

function toggleEdit() {
  const t = current();
  if (!t || !mine(t)) return;
  if (shapeOf(t.artefact) === "commits") { notice("A branch is approved as its commits stand. Request changes, and the agent amends them.", "bad"); return; }
  if (multiRule(t)) { notice(`Under ${t.review.rule} an edit would settle the review alone. Request changes, and the agent revises.`, "bad"); return; }
  if (anomaliesOf(t).length) { notice("This patch cannot be shown faithfully, so it cannot be edited here.", "bad"); return; }
  state.editing = state.editing ? null : { state: {}, value: () => t.artefact };
  renderReview();
  if (state.editing) { notice("Edit the artefact, give a rationale, then approve your edit.", ""); $("comment").focus(); }
  else notice("");
}

const tags = () => $("tags").value.split(",").map((s) => s.trim()).filter(Boolean);

async function decide(kind) {
  const t = current();
  if (!t || !mine(t) || state.busy) return;
  const comment = $("comment").value.trim();
  const artefact = t.artefact;
  // Every decision names the artefact it was made on and the submission that
  // opened this review round, so it counts in this round and no other.
  const round = t.submission?.envelope ? { round: await contentHash(t.submission.envelope) } : {};
  const lineComments = commentsOf(t.task_id).map(({ id, ...c }) => c);
  const base = { task_id: t.task_id, approved_artefact_digest: await contentHash(artefact), ...round, ...(tags().length ? { tags: tags() } : {}), ...(lineComments.length ? { comments: lineComments } : {}) };
  if ((kind === "approve" || kind === "override") && anomaliesOf(t).length) { notice("This patch cannot be shown faithfully. Reject it, or request changes.", "bad"); return; }
  if (kind === "override" && shapeOf(artefact) === "commits") { notice("A branch is approved as its commits stand. Request changes, and the agent amends them.", "bad"); return; }
  // An approval of a branch covers every commit in it: one not opened yet is
  // shown before the approval is sent.
  if (kind === "approve" && shapeOf(artefact) === "commits" && !(await confirmBranch(artefact))) return;
  if (kind === "override" && multiRule(t)) { notice(`Under ${t.review.rule} an edit would settle the review alone. Request changes, and the agent revises.`, "bad"); return; }
  let call;
  if (kind === "approve") call = ["decide.approve", { ...base, ...(comment ? { comment } : {}) }];
  else if (kind === "changes") {
    if (!comment && !lineComments.length) { notice("Say what to change, in the note or on the lines; the agent reads both.", "bad"); $("comment").focus(); return; }
    call = ["decide.reject", { ...base, ...(comment ? { comment } : {}), request_revision: true }];
  }
  else if (kind === "reject") { if (!comment) { notice("A rejection needs a reason.", "bad"); $("comment").focus(); return; } call = ["decide.reject", { ...base, comment }]; }
  else if (kind === "override") {
    if (!comment) { notice("An edit needs a rationale.", "bad"); $("comment").focus(); return; }
    let edited;
    try { edited = state.editing.value(); } catch (e) { notice(`The edit is not usable: ${e.message}`, "bad"); return; }
    const diff = jsonPatch(artefact, edited);
    if (!diff.length) { notice("Nothing changed. Approve as written, or edit the artefact.", "bad"); return; }
    // A code change is shown as it will be committed, and sent only once the
    // reviewer confirms it: the patch written from the edit is what they approve.
    if (shapeOf(edited) === "code" && !(await confirmEdit(edited))) return;
    call = ["decide.override", { ...base, diff, rationale: comment, intent_preserved: true }];
  }
  state.busy = true;
  for (const id of ["approve", "changes", "reject", "override"]) $(id).disabled = true;
  notice("");
  try {
    const r = await state.client.call(call[0], call[1]);
    const said = { approve: "Approved", changes: "Sent back for a revision", reject: "Rejected", override: "Approved with your edit" }[kind];
    toast(`${said}: ${titleOf(t)}${r.state ? ` (${r.state.replace(/_/g, " ")})` : ""}`, kind === "reject" ? "" : "ok");
    $("comment").value = ""; $("tags").value = "";
    state.comments.delete(t.task_id);
    state.editing = null;
    // The decision as a prompt for the agent, when it asks something of it.
    try {
      const decided = await getJson(`/api/tasks/${encodeURIComponent(t.task_id)}`);
      const prompt = promptFor(decided);
      if (prompt) showPrompt(prompt);
    } catch { /* the review shows it once it is read again */ }
    await refresh();
    // Move on to the next task waiting, if there is one.
    const next = state.tasks.find((x) => mine(x));
    if (next) select_(next.task_id); else { state.selectedId = null; setHash({ task: null }); renderQueue(); renderReview(); }
  } catch (e) {
    notice(e.message, "bad");
  } finally {
    state.busy = false;
    for (const id of ["approve", "changes", "reject", "override"]) $(id).disabled = false;
  }
}

/** Ask before approving a branch whose commits were not all opened; resolve true to approve. */
function confirmBranch(artefact) {
  const unread = artefact.commits.filter((c) => !state.range.read.has(c.sha));
  if (!unread.length) return Promise.resolve(true);
  return new Promise((resolve) => {
    const done = (ok) => { $("confirm").hidden = true; $("confirm-send").onclick = null; $("confirm-cancel").onclick = null; resolve(ok); };
    $("confirm-title").textContent = `Approve all ${artefact.commits.length} commits?`;
    $("confirm-cancel").textContent = "Back to the branch";
    $("confirm-send").textContent = `Approve all ${artefact.commits.length}`;
    $("confirm-send").disabled = false;
    const list = el("ol", { class: "commits compact" });
    for (const c of unread) {
      const open = () => { done(false); selectCommit(artefact.commits.indexOf(c)); };
      list.append(el("li", {}, el("button", { class: "commit-row", onclick: open },
        el("span", { class: "sha mono", text: String(c.sha).slice(0, 7) }),
        el("span", { class: "subject", text: c.message.split("\n")[0] }))));
    }
    const opened = artefact.commits.length - unread.length;
    $("confirm-body").replaceChildren(el("p", { text: `You have opened ${opened} of the ${artefact.commits.length} commits. The approval covers every one of them as it stands. Not read yet:` }), list);
    $("confirm").hidden = false;
    $("confirm-send").onclick = () => done(true);
    $("confirm-cancel").onclick = () => done(false);
  });
}

/** Show the patch an edit produces, and resolve true when the reviewer confirms it. */
function confirmEdit(edited) {
  return new Promise((resolve) => {
    $("confirm-title").textContent = "The change you are approving";
    $("confirm-cancel").textContent = "Back to the edit";
    $("confirm-send").textContent = "Approve this version";
    const anomalies = parsePatch(edited.patch).anomalies;
    const box = $("confirm-body");
    box.replaceChildren(...[
      anomalies.length ? el("div", { class: "alert" }, "The edit produced a patch the desk cannot show faithfully: ", anomalies.join("; "), ". It cannot be sent.") : null,
      renderDiff(edited.patch, { files: edited.files ?? [] }),
    ].filter(Boolean));
    $("confirm-send").disabled = anomalies.length > 0;
    $("confirm").hidden = false;
    const done = (ok) => { $("confirm").hidden = true; $("confirm-send").onclick = null; $("confirm-cancel").onclick = null; resolve(ok); };
    $("confirm-send").onclick = () => done(true);
    $("confirm-cancel").onclick = () => done(false);
  });
}

// -- activity -------------------------------------------------------------------

/** A few words on what a call did, for its row in the activity list. */
function gist(call) {
  const p = call.params ?? {};
  const a = p.output ?? p.artefact;
  switch (call.method) {
    case "decide.approve": return p.comment ?? "";
    case "decide.reject": return `${p.request_revision ? "revision requested: " : ""}${p.comment ?? ""}`;
    case "decide.override": return p.rationale ?? "";
    case "task.create": return p.input?.summary ?? p.input?.subject ?? p.kind ?? "";
    case "task.complete": case "review.request": return a?.summary ?? a?.subject ?? (typeof a === "string" ? a : "");
    case "participant.join": return [p.type, p.role].filter(Boolean).join(", ");
    case "control.pause": case "control.resume": return [p.scope, p.participant_uri, p.reason].filter(Boolean).join(", ");
    default: return "";
  }
}

function renderActivity() {
  const box = $("activity");
  const needle = $("act-filter").value.trim().toLowerCase();
  const refusedOnly = $("act-refused").checked;
  const rows = state.chain.entries.filter((e) => {
    const call = e.envelope ?? e.request ?? {};
    if (refusedOnly && !e.outcome) return false;
    if (!needle) return true;
    return [call.method, call.params?.from, call.params?.task_id, call.params?.workspace, e.outcome?.code].join(" ").toLowerCase().includes(needle);
  }).slice().reverse();
  const loaded = state.chain.entries.length;
  $("act-summary").textContent = loaded ? `${rows.length} of ${loaded} loaded, ${state.chain.total} on the chain` : "";
  $("act-more").hidden = !(state.chain.from > 0);
  if (!rows.length) { box.replaceChildren(el("div", { class: "empty", text: loaded ? "No entry matches." : "No entry yet." })); return; }
  box.replaceChildren(...rows.map((e) => {
    const call = e.envelope ?? e.request ?? {};
    const d = el("details", { class: "entry" },
      el("summary", {},
        el("span", { class: "seq", text: `#${e.seq}` }),
        el("span", { class: `method m-${String(call.method ?? "").split(".")[0]} m-${String(call.method ?? "").replace(".", "-")}` }, call.method ?? "?", e.outcome ? el("span", { class: "badge bad", style: "margin-left:6px", text: `refused ${e.outcome.code}` }) : null),
        el("span", { class: "who", title: call.params?.task_id ?? "", text: call.params?.from ?? "" }),
        el("span", { class: "gist", title: gist(call), text: gist(call) }),
        el("span", { class: "when", title: e.arrived, text: e.arrived ? `${ago(e.arrived)} ago` : "" })));
    // An entry can carry a whole branch, so its JSON is written when it is opened.
    d.addEventListener("toggle", () => { if (d.open && !d.querySelector("pre")) d.append(el("pre", { class: "json", text: JSON.stringify(e, null, 2) })); });
    return d;
  }));
}

// -- insights -------------------------------------------------------------------

// Insights is drawn again only when what it counts has changed, so a
// reader scrolling or selecting text is not interrupted by the poll.
async function loadAllTasks() {
  // Every task, for counting. The queue holds the open ones and the most
  // recent; Insights counts the whole workspace, read when it is shown.
  if (state.allTasksAt && Date.now() - state.allTasksAt < 30_000 && state.allTasksAudit === state.health?.audit) return;
  state.allTasks = (await getJson("/api/tasks?brief=1")).tasks;
  state.allTasksAt = Date.now();
  state.allTasksAudit = state.health?.audit;
}

async function renderInsightsView({ force = false } = {}) {
  try { await loadAllTasks(); } catch { /* Insights then counts the queue's tasks */ }
  const tasks = state.allTasks ?? state.tasks;
  const key = JSON.stringify([tasks.length, tasks.map((t) => t.updated_at).sort().at(-1), state.health?.audit, state.chain.verified, state.analytics.summary?.written]);
  if (!force && key === state.insightsKey) return;
  state.insightsKey = key;
  const chain = state.config.chain_enabled ? { entries: state.health?.audit ?? 0, verified: state.chain.verified } : null;
  const view = renderInsights(tasks, { chain, analytics: state.analytics, onOpenTask: (t) => select_(t.task_id) });
  $("insights").replaceChildren(view);
  if (state.analytics.refine) { const r = view.querySelector("#refine"); if (r) r.innerHTML = renderMarkdown(state.analytics.refine); }
}

// -- views and keys -------------------------------------------------------------

function showView(name) {
  state.view = name;
  for (const tab of document.querySelectorAll(".tab")) tab.setAttribute("aria-selected", String(tab.dataset.view === name));
  for (const v of ["review", "activity", "insights"]) $(`view-${v}`).hidden = v !== name;
  $("decision-bar").hidden = !(name === "review" && selected() && mine(selected()));
  setHash({ view: name === "review" ? null : name });
  if (name === "activity") loadChain();
  if (name === "insights") renderInsightsView({ force: true });
}

function onKey(e) {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName);
  if (e.key === "Escape") { if (!$("help").hidden) $("help").hidden = true; else if (!$("confirm").hidden) $("confirm-cancel").click(); else if (!$("prompt").hidden) $("prompt").hidden = true; else if (typing) e.target.blur(); return; }
  // While a dialog is open, the page under it does not move.
  if (!$("confirm").hidden || !$("prompt").hidden || (!$("help").hidden && e.key !== "?")) return;
  if (typing) return;
  const ordered = [...document.querySelectorAll("#queue .item")].map((b) => b.dataset.id);
  const at = ordered.indexOf(state.selectedId);
  switch (e.key) {
    case "j": if (ordered.length) select_(ordered[Math.min(ordered.length - 1, at + 1)]); break;
    case "k": if (ordered.length) select_(ordered[Math.max(0, at - 1)]); break;
    case "a": decide("approve"); break;
    case "r": if (selected() && mine(selected())) { $("comment").focus(); notice("Write the note, then press Request changes.", ""); } break;
    case "x": decide("reject"); break;
    case "e": toggleEdit(); break;
    case "n": if (state.view === "review") selectCommit(state.range.index + 1); break;
    case "p": if (state.view === "review") selectCommit(state.range.index - 1); break;
    case "1": showView("review"); break;
    case "2": showView("activity"); break;
    case "3": showView("insights"); break;
    case "/": e.preventDefault(); $("search").focus(); break;
    case "?": $("help").hidden = !$("help").hidden; break;
    default: return;
  }
}

boot().catch((e) => { chip("connection", "could not start", "bad"); toast(e.message, "bad"); console.error(e); });
