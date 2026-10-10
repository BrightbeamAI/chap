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
import { ago, el, renderArtefact, renderJson, stateBadge, titleOf } from "./render.js";
import { decisionsOf, outcomeOf, renderInsights, renderMarkdown } from "./insights.js";

const $ = (id) => document.getElementById(id);
const POLL_MS = 4000;
const CHAIN_PAGE = 200;

const state = {
  config: null, client: null, tasks: [], reviews: [], selectedId: null, view: "review",
  editing: null, health: null, chain: { entries: [], from: null, total: 0, verified: null }, chainShown: -1,
  analytics: { report: false, refine: null, cases: false }, busy: false, lost: false,
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
  document.addEventListener("keydown", onKey);

  state.config = await getJson("/api/config");
  $("workspace").textContent = state.config.workspace;
  const short = state.config.profiles.map((p) => p.replace(/\/1\.0$/, "").replace("security-signed", "signed").replace("audit-scitt", "scitt"));
  $("profiles").replaceChildren(el("span", { class: "chip profiles", title: state.config.profiles.join(", "), text: short.join(" · ") }));
  chip("chain", state.config.chain_enabled ? "chain on" : "chain off", state.config.chain_enabled ? "good" : "");
  const select = $("reviewer");
  for (const h of state.config.humans) select.append(el("option", { value: h.uri, text: h.display_name ? `${h.display_name} (${h.uri})` : h.uri }));
  const wanted = hashParams().get("reviewer");
  if (wanted && state.config.humans.some((h) => h.uri === wanted)) select.value = wanted;
  select.onchange = () => setReviewer(select.value);
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
  $("signing").title = signer ? `Decisions are signed in this browser as ${uri} with the Ed25519 key ${signer.kid}` : "This workspace does not require signatures";
  if (signer) {
    const me = state.config.humans.find((h) => h.uri === uri);
    try { await state.client.call("participant.join", { type: "human", role: me?.role ?? "reviewer", display_name: me?.display_name, jwks: { keys: [signer.publicJwk] } }); }
    catch (e) { toast(`Could not join as ${uri}: ${e.message}`, "bad"); }
  }
  state.chainShown = -1;
  await refresh().catch(lost);
}

function lost(e) {
  state.lost = true;
  chip("connection", "connection lost", "bad");
  console.warn(e);
}

// -- data -----------------------------------------------------------------------

async function refresh() {
  // Every open task, and the most recent ones for the decided list.
  const [health, open, recent] = await Promise.all([getJson("/api/health"), getJson("/api/tasks?state=created,in_progress,review_requested"), getJson("/api/tasks?limit=200")]);
  if (state.lost) { state.lost = false; toast("Connected again", "ok"); }
  chip("connection", "live", "good");
  $("connection").title = `${health.members} members, ${health.tasks} tasks, ${health.audit} chain entries`;
  state.health = health;
  const before = selected();
  const byId = new Map();
  for (const t of [...open.tasks, ...recent.tasks]) byId.set(t.task_id, t);
  state.tasks = [...byId.values()].sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""));
  renderQueue();
  const now = selected();
  if (before && !now) { state.selectedId = null; state.editing = null; renderReview(); }
  else if (now && before && (before.state !== now.state || !deepEqual(before.artefact, now.artefact))) {
    if (state.editing) { notice("The artefact changed while you were editing it; your edit is kept, the decision binds to the new artefact.", "bad"); }
    renderReview();
    if (before.state === now.state) toast("The artefact under review changed; this is the current one.");
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
  setHash({ task: id });
  renderQueue();
  renderReview();
  if (state.view !== "review") showView("review");
}

// -- the review -----------------------------------------------------------------

function renderReview() {
  const t = selected();
  $("review-empty").hidden = !!t;
  $("review").hidden = !t;
  $("decision-bar").hidden = !(t && mine(t));
  if (!t) return;
  const artefact = t.artefact ?? t.output ?? null;
  $("r-title").textContent = titleOf(t);
  $("r-state").replaceWith(Object.assign(stateBadge(t.state), { id: "r-state" }));
  $("r-kind").textContent = t.kind;
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
  const multi = !!t.review?.rule && t.review.rule !== "any_one_approves";
  $("r-edit").hidden = !(mine(t) && artefact !== null && !multi);
  $("r-edit-hint").hidden = !(mine(t) && multi);
  $("r-edit-hint").textContent = multi ? `Under ${t.review.rule} an edit would settle the review alone. Request changes, and the agent revises.` : "";
  $("r-edit").textContent = state.editing ? "Editing" : "Edit";
  $("override").hidden = !state.editing;
  $("cancel-edit").hidden = !state.editing;
  const body = $("r-artefact");
  body.replaceChildren(artefact === null ? el("span", { class: "muted small", text: "Nothing submitted yet." }) : renderArtefact(artefact, { editing: state.editing }));
  const history = $("r-history");
  const events = [];
  for (const h of t.history ?? []) events.push({ ts: h.ts, who: h.from, what: h.state.replace(/_/g, " "), note: h.note });
  for (const d of decisionsOf(t)) events.push({ ts: d.ts, who: d.reviewer, what: d.kind === "override" ? "approved with an edit" : d.kind === "reject" ? (d.request_revision ? "sent back for a revision" : "rejected") : d.kind === "approve" ? "approved" : d.kind, note: d.rationale ?? d.comment, tags: d.tags });
  events.sort((a, b) => a.ts.localeCompare(b.ts));
  history.replaceChildren(...events.map((e) => el("div", { class: "event" }, el("span", { class: "when", title: e.ts, text: `${ago(e.ts)} ago` }), el("span", {}, el("b", { text: e.what }), e.who ? el("span", { class: "muted", text: ` by ${e.who}` }) : null, e.note ? el("div", { class: "note", text: e.note }) : null, e.tags?.length ? el("div", { class: "small muted", text: e.tags.join(", ") }) : null))));
  $("r-history-card").hidden = !events.length;
}

function toggleEdit() {
  const t = selected();
  if (!t || !mine(t)) return;
  state.editing = state.editing ? null : { state: {}, value: () => t.artefact };
  renderReview();
  if (state.editing) { notice("Edit the artefact, give a rationale, then approve your edit.", ""); $("comment").focus(); }
  else notice("");
}

const tags = () => $("tags").value.split(",").map((s) => s.trim()).filter(Boolean);

async function decide(kind) {
  const t = selected();
  if (!t || !mine(t) || state.busy) return;
  const comment = $("comment").value.trim();
  const artefact = t.artefact;
  const base = { task_id: t.task_id, approved_artefact_digest: await contentHash(artefact), ...(tags().length ? { tags: tags() } : {}) };
  let call;
  if (kind === "approve") call = ["decide.approve", { ...base, ...(comment ? { comment } : {}) }];
  else if (kind === "changes") { if (!comment) { notice("Say what to change; the agent reads the note.", "bad"); $("comment").focus(); return; } call = ["decide.reject", { ...base, comment, request_revision: true }]; }
  else if (kind === "reject") { if (!comment) { notice("A rejection needs a reason.", "bad"); $("comment").focus(); return; } call = ["decide.reject", { ...base, comment }]; }
  else if (kind === "override") {
    if (!comment) { notice("An edit needs a rationale.", "bad"); $("comment").focus(); return; }
    let edited;
    try { edited = state.editing.value(); } catch (e) { notice(`The edit is not usable: ${e.message}`, "bad"); return; }
    const diff = jsonPatch(artefact, edited);
    if (!diff.length) { notice("Nothing changed. Approve as written, or edit the artefact.", "bad"); return; }
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
    state.editing = null;
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
        el("span", { class: "when", title: e.arrived, text: e.arrived ? `${ago(e.arrived)} ago` : "" })),
      el("pre", { class: "json", text: JSON.stringify(e, null, 2) }));
    return d;
  }));
}

// -- insights -------------------------------------------------------------------

// Insights is drawn again only when what it counts has changed, so a
// reader scrolling or selecting text is not interrupted by the poll.
function renderInsightsView({ force = false } = {}) {
  const key = JSON.stringify([state.tasks.length, state.tasks.map((t) => t.updated_at).sort().at(-1), state.health?.audit, state.chain.verified, state.analytics.summary?.written]);
  if (!force && key === state.insightsKey) return;
  state.insightsKey = key;
  const chain = state.config.chain_enabled ? { entries: state.health?.audit ?? 0, verified: state.chain.verified } : null;
  const view = renderInsights(state.tasks, { chain, analytics: state.analytics, onOpenTask: (t) => select_(t.task_id) });
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
  if (e.key === "Escape") { if (!$("help").hidden) $("help").hidden = true; else if (typing) e.target.blur(); return; }
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
    case "1": showView("review"); break;
    case "2": showView("activity"); break;
    case "3": showView("insights"); break;
    case "/": e.preventDefault(); $("search").focus(); break;
    case "?": $("help").hidden = !$("help").hidden; break;
    default: return;
  }
}

boot().catch((e) => { chip("connection", "could not start", "bad"); toast(e.message, "bad"); console.error(e); });
