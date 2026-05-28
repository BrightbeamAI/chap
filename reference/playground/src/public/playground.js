/**
 * CHAP Playground — browser client.
 *
 * Every user action becomes a real JSON-RPC envelope sent to POST /rpc.
 * The server's coordinator processes it, appends it to the evidence
 * chain, and broadcasts via SSE to every connected tab. This file is
 * the glue: SSE → state → DOM, and DOM → envelopes → POST.
 *
 * Pure browser code: no build step, no framework.
 */

const WORKSPACE_ID = "wsp_techcorp_support";
const MAYA_URI     = "human:maya@local";
const SAM_URI      = "human:sam@local";
const BOT_URI      = "agent:triage-bot@local";

// ============================================================
//   Tiny state
// ============================================================
const state = {
  role: null,                  // "maya" | "sam"
  workspace: null,             // last /api/workspace response
  tickets: [],                 // ticket catalogue
  selectedTaskId: null,
  selectedDraft: null,         // currently-edited draft object {body, tone, severity}
  selectedTags: new Set(),
  wirePaused: false,
  wireEntries: [],             // accumulated audit entries for the wire panel

  // v2 additions
  mayaTourStep:      1,        // 1, 2, 3, or 0 (dismissed)
  samTourStep:       1,
  mayaTourDismissed: false,
  samTourDismissed:  false,
  lastChainLen:      0,        // for pulse detection
  ollamaOk:          null,     // null | true | false
};

// ============================================================
//   URL → role routing
// ============================================================
function getRoleFromHash() {
  if (location.hash === "#maya") return "maya";
  if (location.hash === "#sam")  return "sam";
  return null;
}

function setRole(role) {
  state.role = role;
  location.hash = role ? `#${role}` : "";
  $("picker").hidden    = !!role;
  $("view-maya").hidden = role !== "maya";
  $("view-sam").hidden  = role !== "sam";
  $("statusbar").hidden = !role;
  $("wire-strip").hidden = !role;
  $("nav-maya").classList.toggle("active", role === "maya");
  $("nav-sam").classList.toggle("active", role === "sam");
  // The "restart guided tour" button shows once a role is active,
  // unless the walkthrough is currently running.
  if (role && !walkthrough.active) {
    $("restart-walkthrough").hidden = false;
  }
  if (role) {
    document.title = role === "maya"
      ? "Maya · CHAP Playground"
      : "Sam · CHAP Playground";
    refreshWorkspace();
    pollHealth();   // kick off the Ollama dot
  }
}

// ============================================================
//   DOM helpers
// ============================================================
function $(id)  { return document.getElementById(id); }
function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class")       e.className = v;
    else if (k.startsWith("on")) e[k] = v;
    else                          e.setAttribute(k, v);
  }
  for (const c of children) {
    if (c == null) continue;
    e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return e;
}

// ============================================================
//   Transport: JSON-RPC over POST /rpc
// ============================================================
async function rpc(method, params) {
  const id = `c-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
  const res = await fetch("/rpc", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id, method, params }),
  });
  const env = await res.json();
  if (env.error) {
    console.warn(`rpc ${method} failed:`, env.error);
    throw new Error(`${env.error.code}: ${env.error.message}`);
  }
  return env.result;
}

async function refreshWorkspace() {
  const ws = await fetch("/api/workspace").then((r) => r.json());
  state.workspace = ws;
  updateStatusBar();
  renderForCurrentRole();
}

async function loadTickets() {
  const data = await fetch("/api/tickets").then((r) => r.json());
  state.tickets = data.tickets;
}

// ============================================================
//   SSE: every audit append fans out here
// ============================================================
function startSse() {
  const uri = state.role === "maya" ? MAYA_URI :
              state.role === "sam"  ? SAM_URI  : "anonymous";
  const es = new EventSource(`/events?participant=${encodeURIComponent(uri)}`);
  es.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.kind === "audit") {
        appendWireEntry(msg);
        // Refresh workspace on every change (cheap, the demo has small state)
        refreshWorkspace();
      }
    } catch {}
  };
  es.onerror = () => {
    // Browser auto-reconnects.
  };
  return es;
}

// ============================================================
//   RFC 6902 JSON Patch — compute diff in the browser
//   (mirrors packages/coordinator/src/patch.ts diffJsonPatch)
// ============================================================
function diffJsonPatch(from, to, basePath = "") {
  const ops = [];
  if (typeof from !== typeof to || Array.isArray(from) !== Array.isArray(to)) {
    ops.push({ op: "replace", path: basePath || "/", value: to });
    return ops;
  }
  if (from === null || to === null || typeof from !== "object") {
    if (from !== to) ops.push({ op: "replace", path: basePath || "/", value: to });
    return ops;
  }
  if (Array.isArray(from) && Array.isArray(to)) {
    if (JSON.stringify(from) !== JSON.stringify(to)) {
      ops.push({ op: "replace", path: basePath || "/", value: to });
    }
    return ops;
  }
  const allKeys = new Set([...Object.keys(from), ...Object.keys(to)]);
  for (const key of allKeys) {
    const escaped = key.replace(/~/g, "~0").replace(/\//g, "~1");
    const path = `${basePath}/${escaped}`;
    if (!(key in from)) ops.push({ op: "add", path, value: to[key] });
    else if (!(key in to)) ops.push({ op: "remove", path });
    else ops.push(...diffJsonPatch(from[key], to[key], path));
  }
  return ops;
}

function renderDiff(ops) {
  if (!ops.length) return el("span", { class: "diff-empty" }, "No changes yet");
  const frag = document.createDocumentFragment();
  for (const op of ops) {
    if (op.op === "replace") {
      frag.appendChild(el("div", {},
        el("span", { class: "path" }, op.path + " "),
        el("span", { class: "remove" }, "[ replaced ]"),
        op.value !== undefined
          ? el("span", { class: "add" }, " → " + JSON.stringify(op.value).slice(0, 100))
          : null,
      ));
    } else if (op.op === "add") {
      frag.appendChild(el("div", {},
        el("span", { class: "path" }, op.path + " "),
        el("span", { class: "add" }, "+ " + JSON.stringify(op.value).slice(0, 100)),
      ));
    } else if (op.op === "remove") {
      frag.appendChild(el("div", {},
        el("span", { class: "path" }, op.path + " "),
        el("span", { class: "remove" }, "- removed"),
      ));
    }
  }
  return frag;
}

// ============================================================
//   Per-role rendering dispatch
// ============================================================
function renderForCurrentRole() {
  if (!state.workspace) return;
  if (state.role === "maya") renderMaya();
  else if (state.role === "sam") renderSam();
}

// ----- MAYA -------------------------------------------------
function mayaQueue() {
  if (!state.workspace) return [];
  return state.workspace.tasks
    .filter((t) =>
      t.state === "review_requested" &&
      t.review?.requested_to?.includes(MAYA_URI)
    )
    .sort((a, b) => a.created_at.localeCompare(b.created_at));
}

function botDraftingTickets() {
  if (!state.workspace) return [];
  const tasksByTicketId = new Map();
  for (const t of state.workspace.tasks) {
    const tid = t.input?.ticket_id;
    if (tid) tasksByTicketId.set(tid, t);
  }
  return state.tickets.filter((tk) => {
    const t = tasksByTicketId.get(tk.id);
    return !t || t.state === "created" || t.state === "in_progress";
  });
}

function renderMaya() {
  const queue = mayaQueue();
  const drafting = botDraftingTickets();

  $("maya-queue-count").textContent = String(queue.length);

  // Queue list
  const list = $("maya-queue-list");
  list.innerHTML = "";
  for (const t of queue) {
    const selected = t.id === state.selectedTaskId;
    const ticketId = t.input?.ticket_id ?? "—";
    const subject = t.input?.subject ?? t.kind;
    const crit = t.routing_hints?.criticality ?? "—";
    const item = el("div", { class: `queue-item${selected ? " selected" : ""}`,
                            onclick: () => selectTask(t.id) },
      el("div", { class: "queue-item-id" }, ticketId),
      el("div", { class: "queue-item-subject" }, subject),
      el("div", { class: "queue-item-meta" },
        el("span", { class: `badge badge-crit-${crit}` }, crit),
      ),
    );
    list.appendChild(item);
  }
  // "Bot drafting" footer items
  for (const tk of drafting) {
    list.appendChild(el("div", { class: "queue-item queue-item-bot-drafting" },
      el("div", { class: "queue-item-id" }, tk.id),
      el("div", { class: "queue-item-subject" }, tk.subject),
    ));
  }
  if (!queue.length && !drafting.length) {
    list.appendChild(el("div", { class: "dividend-empty" }, "Queue empty."));
  }

  // Editor
  if (state.selectedTaskId) {
    const task = state.workspace.tasks.find((t) => t.id === state.selectedTaskId);
    if (task) renderMayaEditor(task);
  } else {
    $("maya-empty").hidden  = false;
    $("maya-editor").hidden = true;
  }

  // Dividend
  renderDividend("maya");
}

function renderMayaEditor(task) {
  $("maya-empty").hidden  = true;
  $("maya-editor").hidden = false;

  const ticketId = task.input?.ticket_id ?? "—";
  $("maya-ticket-id").textContent = ticketId;
  $("maya-ticket-subject").textContent = task.input?.subject ?? "";
  $("maya-ticket-from").textContent = `from ${task.input?.customer ?? "—"}`;
  $("maya-ticket-body").textContent = task.input?.body ?? "";

  const crit = task.routing_hints?.criticality ?? "—";
  const critBadge = $("maya-crit-badge");
  critBadge.textContent = `criticality: ${crit}`;
  critBadge.className   = `badge badge-crit-${crit}`;

  const conf = task.artefact_routing_hints?.confidence;
  $("maya-conf-badge").textContent = `conf ${typeof conf === "number" ? conf.toFixed(2) : "—"}`;

  const modelId = task.artefact_routing_hints?.model_id ?? "—";
  $("maya-model-badge").textContent = `model ${modelId}`;

  const escalateBtn = $("maya-escalate");
  escalateBtn.style.display = (crit === "high" || crit === "critical") ? "" : "none";

  // Re-bind draft only when the task changes
  if (state.selectedDraft?._task !== task.id) {
    state.selectedDraft = JSON.parse(JSON.stringify(task.output ?? {}));
    state.selectedDraft._task = task.id;
    state.selectedTags = new Set();
    $("maya-rationale").value = "";
    $("maya-tags").querySelectorAll("button").forEach((b) => b.classList.remove("selected"));
    $("maya-draft").value = state.selectedDraft.body ?? "";
    updateMayaDiff();
  }
}

function updateMayaDiff() {
  const task = state.workspace?.tasks.find((t) => t.id === state.selectedTaskId);
  if (!task) return;
  const draft = state.selectedDraft;
  const editedBody = $("maya-draft").value;
  const proposed = { ...draft, body: editedBody };
  delete proposed._task;
  const original = { ...(task.output ?? {}) };
  const ops = diffJsonPatch(original, proposed);
  const out = $("maya-diff");
  out.innerHTML = "";
  out.appendChild(renderDiff(ops));
  const sendBtn = $("maya-send");
  sendBtn.disabled = ops.length === 0 || !$("maya-rationale").value.trim();
}

// ----- SAM --------------------------------------------------
function samQueue() {
  if (!state.workspace) return [];
  return state.workspace.tasks
    .filter((t) =>
      t.state === "review_requested" &&
      t.review?.requested_to?.includes(SAM_URI)
    )
    .sort((a, b) => a.updated_at.localeCompare(b.updated_at));
}

function renderSam() {
  const queue = samQueue();
  $("sam-queue-count").textContent = String(queue.length);

  const list = $("sam-queue-list");
  list.innerHTML = "";
  for (const t of queue) {
    const selected = t.id === state.selectedTaskId;
    const ticketId = t.input?.ticket_id ?? "—";
    const subject = t.input?.subject ?? t.kind;
    const crit = t.routing_hints?.criticality ?? "—";
    const item = el("div", { class: `queue-item${selected ? " selected" : ""}`,
                            onclick: () => selectTask(t.id) },
      el("div", { class: "queue-item-id" }, ticketId),
      el("div", { class: "queue-item-subject" }, subject),
      el("div", { class: "queue-item-meta" },
        el("span", { class: `badge badge-crit-${crit}` }, crit),
      ),
    );
    list.appendChild(item);
  }
  if (!queue.length) {
    list.appendChild(el("div", { class: "dividend-empty" }, "No escalations."));
  }

  if (state.selectedTaskId) {
    const task = state.workspace.tasks.find((t) => t.id === state.selectedTaskId);
    if (task) renderSamEditor(task);
  } else {
    $("sam-empty").hidden  = false;
    $("sam-editor").hidden = true;
  }

  renderDividend("sam");
}

function renderSamEditor(task) {
  $("sam-empty").hidden  = true;
  $("sam-editor").hidden = false;

  const ticketId = task.input?.ticket_id ?? "—";
  $("sam-ticket-id").textContent = ticketId;
  $("sam-ticket-subject").textContent = task.input?.subject ?? "";
  $("sam-ticket-from").textContent = `from ${task.input?.customer ?? "—"}`;
  $("sam-ticket-body").textContent = task.input?.body ?? "";

  const crit = task.routing_hints?.criticality ?? "—";
  const critBadge = $("sam-crit-badge");
  critBadge.textContent = `criticality: ${crit}`;
  critBadge.className   = `badge badge-crit-${crit}`;

  // Build the lineage from the task's history + overrides + route_decisions
  const lineage = buildLineage(task);
  const track = $("sam-lineage-track");
  track.innerHTML = "";
  lineage.forEach((step, i) => {
    if (i > 0) track.appendChild(el("span", { class: "lineage-arrow" }, "→"));
    track.appendChild(el("div", { class: "lineage-step" },
      el("span", { class: "who" }, step.who),
      step.what ? el("span", { class: "what" }, step.what) : null,
    ));
  });

  // Show whether this escalation was auto-routed
  const autoEsc = task.route_decisions?.some((d) =>
    d.decision_type === "escalate.auto" && d.outcome?.escalate);
  $("sam-routing-badge").textContent = autoEsc ? "auto-escalated" : "manually escalated";

  if (state.selectedDraft?._task !== task.id) {
    state.selectedDraft = JSON.parse(JSON.stringify(task.output ?? {}));
    state.selectedDraft._task = task.id;
    state.selectedTags = new Set();
    $("sam-rationale").value = "";
    $("sam-tags").querySelectorAll("button").forEach((b) => b.classList.remove("selected"));
    $("sam-draft").value = state.selectedDraft.body ?? "";
    updateSamDiff();
  }
}

function buildLineage(task) {
  const steps = [];
  steps.push({ who: "bot", what: "drafted" });
  // Find overrides on this task ordered by ts
  const overrides = (state.workspace?.overrides ?? [])
    .filter((o) => o.task_id === task.id)
    .sort((a, b) => a.ts.localeCompare(b.ts));
  for (const ov of overrides) {
    const who = ov.reviewer === MAYA_URI ? "Maya"
              : ov.reviewer === SAM_URI  ? "Sam"
              : ov.reviewer;
    steps.push({ who, what: "overrode" });
  }
  if (task.review?.requested_to?.includes(SAM_URI) && !overrides.some((o) => o.reviewer === SAM_URI)) {
    steps.push({ who: "Sam", what: "awaiting review" });
  }
  return steps;
}

function updateSamDiff() {
  const task = state.workspace?.tasks.find((t) => t.id === state.selectedTaskId);
  if (!task) return;
  const editedBody = $("sam-draft").value;
  const proposed = { ...state.selectedDraft, body: editedBody };
  delete proposed._task;
  const original = { ...(task.output ?? {}) };
  const ops = diffJsonPatch(original, proposed);
  const out = $("sam-diff");
  out.innerHTML = "";
  out.appendChild(renderDiff(ops));
  $("sam-send").disabled = ops.length === 0 || !$("sam-rationale").value.trim();
}

// ----- Common selection ------------------------------------
function selectTask(taskId) {
  state.selectedTaskId = taskId;
  state.selectedDraft  = null;  // force re-init
  renderForCurrentRole();
}

// ============================================================
//   Action: override + send
// ============================================================
async function sendOverride(role) {
  const task = state.workspace.tasks.find((t) => t.id === state.selectedTaskId);
  if (!task) return;

  const editedBody = $(role === "maya" ? "maya-draft" : "sam-draft").value;
  const proposed = { ...state.selectedDraft, body: editedBody };
  delete proposed._task;

  const original = { ...(task.output ?? {}) };
  const ops = diffJsonPatch(original, proposed);
  if (!ops.length) return;

  const rationale = $(role === "maya" ? "maya-rationale" : "sam-rationale").value.trim();
  const tags = Array.from(state.selectedTags);
  const from = role === "maya" ? MAYA_URI : SAM_URI;

  await rpc("decide.override", {
    workspace_id: WORKSPACE_ID,
    task_id:      task.id,
    from,
    diff:         ops,
    rationale,
    tags,
    // CHAP 0.2.1 — surface the new artefact-identity fields. The
    // playground uses the task id as the durable logical handle (one
    // logical artefact per task) and defaults intent_preserved to true
    // because the UI is set up for tone/severity tone-tweak overrides,
    // not decision substitution. A production reviewer UI would offer
    // a toggle for "refining" vs "replacing the decision". See SPEC
    // §9.2.1 and §9.4.
    logical_id:       `lgl_${task.id.replace(/^tsk_/, "")}`,
    intent_preserved: true,
  });

  // First successful override completes the tour
  dismissTour(role);

  // Walkthrough step 5 advances on user override
  walkthroughOnUserOverride();

  state.selectedTaskId = null;
  state.selectedDraft  = null;
  state.selectedTags   = new Set();
}

async function sendApprove(role) {
  if (!state.selectedTaskId) return;
  const from = role === "maya" ? MAYA_URI : SAM_URI;
  await rpc("decide.approve", {
    workspace_id: WORKSPACE_ID,
    task_id:      state.selectedTaskId,
    from,
  });
  state.selectedTaskId = null;
  state.selectedDraft  = null;
}

async function escalateToSam() {
  if (!state.selectedTaskId) return;
  await rpc("escalate.raise", {
    workspace_id: WORKSPACE_ID,
    task_id:      state.selectedTaskId,
    from:         MAYA_URI,
    to:           SAM_URI,
    reason:       "Maya requested senior review.",
  });
  state.selectedTaskId = null;
  state.selectedDraft  = null;
}

async function rejectBackToMaya() {
  if (!state.selectedTaskId) return;
  const reason = $("sam-rationale").value.trim()
    || "Sam asked Maya to revise.";
  await rpc("decide.reject", {
    workspace_id: WORKSPACE_ID,
    task_id:      state.selectedTaskId,
    from:         SAM_URI,
    reason,
  });
  state.selectedTaskId = null;
  state.selectedDraft  = null;
}

// ============================================================
//   Dividend chart
// ============================================================
function renderDividend(role) {
  const tagsByReviewer = new Map();
  const myUri = role === "maya" ? MAYA_URI : SAM_URI;
  for (const ov of state.workspace?.overrides ?? []) {
    if (ov.reviewer !== myUri) continue;
    for (const tag of ov.tags ?? []) {
      tagsByReviewer.set(tag, (tagsByReviewer.get(tag) ?? 0) + 1);
    }
  }
  const totalOverrides = (state.workspace?.overrides ?? [])
    .filter((o) => o.reviewer === myUri).length;

  const emptyEl = $(role === "maya" ? "maya-dividend-empty" : "sam-dividend-empty");
  const chartEl = $(role === "maya" ? "maya-dividend" : "sam-dividend");
  const progEl  = $(role === "maya" ? "maya-dividend-progress" : "sam-dividend-progress");
  if (progEl) progEl.textContent = `${totalOverrides} / 2`;
  if (totalOverrides < 2) {
    emptyEl.hidden = false;
    chartEl.hidden = true;
    return;
  }
  emptyEl.hidden = true;
  chartEl.hidden = false;
  chartEl.innerHTML = "";

  const entries = [...tagsByReviewer.entries()].sort((a, b) => b[1] - a[1]);
  const max = entries[0]?.[1] ?? 1;
  for (const [tag, count] of entries) {
    chartEl.appendChild(el("div", { class: "dividend-bar" },
      el("div", { class: "dividend-bar-label" }, tag),
      el("div", { class: "dividend-bar-fill", style: `width:${(count / max) * 100}%` }),
      el("div", { class: "dividend-bar-count" }, String(count)),
    ));
  }
}

// ============================================================
//   Wire panel — chain-style with routing/override classification
// ============================================================

/** Classify a method name into one of: 'routing' | 'override' | 'default'. */
function classifyMethod(method) {
  if (!method) return "default";
  if (method === "decide.override" || method === "decide.approve" ||
      method === "decide.reject"   || method === "abstain.declare" ||
      method === "escalate.raise"  || method === "escalate.auto") {
    return "override";
  }
  if (method === "task.route" || method === "review.depth") return "routing";
  return "default";
}

/** Short human summary line for a wire entry (one line under the method). */
function summariseEnvelope(env) {
  const p = env.params ?? {};
  const m = env.method ?? "";
  if (m === "task.create" && p.routing_hints?.criticality) {
    return `criticality=${p.routing_hints.criticality}` +
           (p.routing_hints.risk_tier ? ` · risk=${p.routing_hints.risk_tier}` : "");
  }
  if (m === "task.complete" && p.routing_hints) {
    const c = p.routing_hints.confidence;
    return c != null ? `confidence=${Number(c).toFixed(2)} · model=${p.routing_hints.model_id ?? "—"}` : "";
  }
  if (m === "decide.override") {
    const tags = Array.isArray(p.tags) ? p.tags.join(", ") : "";
    return tags ? `tags: ${tags}` : "";
  }
  if (m === "participant.join") return `${p.uri ?? ""} as ${p.type ?? "?"}`;
  if (m === "workspace.create") return `${p.workspace_id ?? ""}`;
  if (m === "escalate.raise")   return `→ ${p.to ?? ""}`;
  if (m === "review.request" && Array.isArray(p.reviewers)) {
    return `reviewers: ${p.reviewers.join(", ")}`;
  }
  return "";
}

function appendWireEntry(msg) {
  state.wireEntries.push(msg);
  if (state.wirePaused) return;

  // Update the always-visible strip
  updateWireStrip(msg);
  // Update the head counter in the (possibly closed) panel
  $("wire-head-num").textContent = String(msg.seq + 1);
  // Append into the open panel if it's expanded
  if (!$("wire-panel").hidden) renderWireEntry(msg);
}

function updateWireStrip(msg) {
  $("wire-strip-seq").textContent    = "#" + msg.seq;
  $("wire-strip-method").textContent = msg.envelope.method ?? "?";
  const pill = $("wire-strip-pill");
  pill.classList.remove("pulse");
  // force reflow so the animation can restart
  void pill.offsetWidth;
  pill.classList.add("pulse");
}

function renderWireEntry(msg) {
  const variant = classifyMethod(msg.envelope.method);
  const classes = ["wire-entry"];
  if (variant === "routing")  classes.push("is-routing");
  if (variant === "override") classes.push("is-override");

  const summary = summariseEnvelope(msg.envelope);

  const entry = el("div", {
      class: classes.join(" "),
      onclick: (e) => e.currentTarget.classList.toggle("expanded"),
    },
    el("div", { class: "wire-entry-head" },
      el("span", { class: "wire-seq" }, "#" + msg.seq),
      el("span", { class: "wire-method" }, msg.envelope.method ?? "?"),
      el("span", { class: "wire-ts" }, msg.ts ?? ""),
    ),
    summary
      ? el("div", { class: "wire-entry-summary" }, summary)
      : null,
    el("div", { class: "wire-body-detail" }, JSON.stringify(msg.envelope, null, 2)),
  );
  const body = $("wire-body");
  // Only auto-scroll if the user is already at (or near) the bottom.
  // This lets them scroll up to read earlier entries without being
  // yanked back every time a new envelope arrives.
  const nearBottom = (body.scrollHeight - body.scrollTop - body.clientHeight) < 80;
  body.appendChild(entry);
  if (nearBottom) body.scrollTop = body.scrollHeight;
}

function renderAllWireEntries() {
  const body = $("wire-body");
  body.innerHTML = "";
  for (const msg of state.wireEntries) renderWireEntry(msg);
}

// ============================================================
//   Status bar — mode pill, chain length, queue size, Ollama dot
// ============================================================
function updateStatusBar() {
  const ws = state.workspace;
  if (!ws) return;

  // Mode pill: workspace doesn't track mode directly in the demo so we
  // surface "production" by default; in a real deployment this would
  // read workspace.mode (per the modes/1.0 profile).
  const modeEl = $("status-mode");
  const mode = ws.mode ?? "production";
  modeEl.textContent = mode;
  modeEl.className = `status-value mode-pill mode-${mode}`;

  // Chain length with pulse if it grew
  const chainEl  = $("status-chain");
  const pulseEl  = $("status-chain-pulse");
  const newLen   = ws.evidence_head ?? 0;
  chainEl.textContent = String(newLen);
  if (newLen > state.lastChainLen && state.lastChainLen > 0) {
    pulseEl.classList.remove("pulse-active");
    void pulseEl.offsetWidth;
    pulseEl.classList.add("pulse-active");
  }
  state.lastChainLen = newLen;

  // In-queue count for the current role
  const myUri = state.role === "maya" ? MAYA_URI :
                state.role === "sam"  ? SAM_URI  : null;
  if (myUri && Array.isArray(ws.tasks)) {
    const inQueue = ws.tasks.filter((t) =>
      t.state === "review_requested" &&
      t.review?.requested_to?.includes(myUri)
    ).length;
    $("status-queue").textContent = String(inQueue);
  }
}

async function pollHealth() {
  try {
    const r = await fetch("/api/health");
    const data = await r.json();
    state.ollamaOk = !!data.ollama?.ok;
    const dot = $("status-ollama-dot");
    const txt = $("status-ollama");
    dot.classList.remove("status-dot-amber", "status-dot-green", "status-dot-red");
    if (state.ollamaOk) {
      dot.classList.add("status-dot-green");
      txt.textContent = "connected";
    } else {
      dot.classList.add("status-dot-red");
      txt.textContent = "not reachable";
    }
  } catch {
    state.ollamaOk = false;
    const dot = $("status-ollama-dot");
    dot.classList.remove("status-dot-amber", "status-dot-green", "status-dot-red");
    dot.classList.add("status-dot-red");
    $("status-ollama").textContent = "unreachable";
  }
}
setInterval(() => { if (state.role) pollHealth(); }, 10000);

// ============================================================
//   Tour bar — guided 3-step walkthrough per role
// ============================================================
function setTourStep(role, step) {
  if (role === "maya") state.mayaTourStep = step;
  else                 state.samTourStep  = step;
  const bar = $(`${role}-tour`);
  if (!bar) return;
  if (step === 0) { bar.classList.add("hidden"); return; }
  bar.classList.remove("hidden");
  bar.dataset.step = String(step);
  for (const dot of bar.querySelectorAll(".tour-dot")) {
    const dotStep = Number(dot.dataset.step);
    dot.classList.toggle("active", dotStep === step);
    dot.classList.toggle("done",   dotStep <  step);
  }
  for (const t of bar.querySelectorAll(".tour-step-text")) {
    t.hidden = Number(t.dataset.step) !== step;
  }
}

function dismissTour(role) {
  if (role === "maya") state.mayaTourDismissed = true;
  else                 state.samTourDismissed  = true;
  setTourStep(role, 0);
}

// ============================================================
//   Initial wiring
// ============================================================
async function init() {
  // Role picker / navbar
  for (const a of document.querySelectorAll("[data-role]")) {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      setRole(a.dataset.role);
    });
  }

  // Wire panel: now opened from either the status-bar button or the
  // bottom strip pill. Close button hides it; pause stays the same.
  $("open-wire").addEventListener("click", () => {
    $("wire-panel").hidden = false;
    renderAllWireEntries();
  });
  $("wire-strip-pill").addEventListener("click", () => {
    $("wire-panel").hidden = false;
    renderAllWireEntries();
  });
  $("wire-close").addEventListener("click", () => { $("wire-panel").hidden = true; });
  $("wire-pause").addEventListener("click", (e) => {
    state.wirePaused = !state.wirePaused;
    e.target.textContent = state.wirePaused ? "Resume" : "Pause";
  });

  // Reset
  $("reset-btn").addEventListener("click", async () => {
    if (!confirm("This wipes the workspace and re-drafts every ticket from scratch. Continue?")) return;
    await fetch("/api/reset", { method: "POST" });
    state.selectedTaskId = null;
    state.selectedDraft  = null;
    state.wireEntries = [];
    state.lastChainLen = 0;
    $("wire-body").innerHTML = "";
    $("wire-strip-seq").textContent = "—";
    $("wire-strip-method").textContent = "—";
    refreshWorkspace();
  });

  // Tour: skip buttons
  $("maya-tour-skip").addEventListener("click", () => dismissTour("maya"));
  $("sam-tour-skip").addEventListener("click",  () => dismissTour("sam"));

  // Tour: advance to step 2 when the user starts editing
  $("maya-draft").addEventListener("input", () => {
    if (!state.mayaTourDismissed && state.mayaTourStep === 1) setTourStep("maya", 2);
  });
  $("sam-draft").addEventListener("input", () => {
    if (!state.samTourDismissed && state.samTourStep === 1) setTourStep("sam", 2);
  });
  // Tour: advance to step 3 when rationale gets typed
  $("maya-rationale").addEventListener("input", () => {
    if (!state.mayaTourDismissed && state.mayaTourStep === 2) setTourStep("maya", 3);
  });
  $("sam-rationale").addEventListener("input", () => {
    if (!state.samTourDismissed && state.samTourStep === 2) setTourStep("sam", 3);
  });

  // Maya bindings
  $("maya-draft").addEventListener("input", updateMayaDiff);
  $("maya-rationale").addEventListener("input", updateMayaDiff);
  $("maya-tags").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-tag]");
    if (!btn) return;
    const tag = btn.dataset.tag;
    if (state.selectedTags.has(tag)) state.selectedTags.delete(tag);
    else                              state.selectedTags.add(tag);
    btn.classList.toggle("selected");
  });
  $("maya-send").addEventListener("click", () => sendOverride("maya"));
  $("maya-approve").addEventListener("click", () => sendApprove("maya"));
  $("maya-escalate").addEventListener("click", escalateToSam);

  // Sam bindings
  $("sam-draft").addEventListener("input", updateSamDiff);
  $("sam-rationale").addEventListener("input", updateSamDiff);
  $("sam-tags").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-tag]");
    if (!btn) return;
    const tag = btn.dataset.tag;
    if (state.selectedTags.has(tag)) state.selectedTags.delete(tag);
    else                              state.selectedTags.add(tag);
    btn.classList.toggle("selected");
  });
  $("sam-send").addEventListener("click", () => sendOverride("sam"));
  $("sam-approve").addEventListener("click", () => sendApprove("sam"));
  $("sam-reject").addEventListener("click", rejectBackToMaya);

  // Walkthrough wiring
  $("start-walkthrough").addEventListener("click", async (e) => {
    e.preventDefault();
    // The walkthrough is narrated from Maya's view
    setRole("maya");
    // Slight delay so the role view has rendered before we measure for the glow
    setTimeout(async () => { await walkthroughStart(); }, 100);
  });
  $("restart-walkthrough").addEventListener("click", async () => {
    await walkthroughStart();
  });
  $("walkthrough-close").addEventListener("click", () => walkthroughStop());
  $("walkthrough-end-link").addEventListener("click", () => walkthroughStop());
  $("walkthrough-next").addEventListener("click", () => walkthroughAdvance());

  window.addEventListener("hashchange", () => setRole(getRoleFromHash()));

  // Boot
  await loadTickets();
  await refreshWorkspace();
  startSse();

  // Apply initial role
  setRole(getRoleFromHash());
}

// ============================================================
//   GUIDED WALKTHROUGH — 6-step narrative tour
//
//   The walkthrough drives the protocol for real: it fires actual
//   JSON-RPC envelopes against /rpc and watches them appear in the
//   audit chain via the same SSE stream the UI uses. There is no
//   side door. A skeptical reader can open DevTools and see every
//   envelope.
// ============================================================

const walkthrough = {
  step: 0,             // 0 = not running; 1..6 = active step
  active: false,
  awaitingUser: false, // step 5 waits for a real override before advancing
  autoTimer: null,
  demoTaskId: null,    // task created in step 2
  critTaskId: null,    // task created in step 6
};

const WALKTHROUGH_STEPS = [
  null, // index 0 unused

  // ----------- Step 1: orient the user -----------
  {
    title: "Welcome — this is a live CHAP workspace.",
    text: `
      <p>The bar at the top is the protocol's heartbeat. <em>Mode</em>
      tells you the workspace is in production. <em>Chain</em> counts
      every envelope that has been hash-linked into the evidence log.
      <em>Ollama</em> tells you whether the local model is reachable.</p>
      <p>Three participants are already in this workspace: a triage bot,
      Maya (front-line), and Sam (senior reviewer). Watch what happens
      when a ticket arrives.</p>`,
    highlight: "#statusbar",
    nextLabel: "Next: a ticket arrives →",
    auto: false,
  },

  // ----------- Step 2: ticket arrives (low-criticality) -----------
  {
    title: "A ticket becomes a Task.",
    text: `
      <p>I just sent a <code>task.create</code> envelope on the wire.
      The customer's question — <em>"where is my order?"</em> — is now
      a structured Task with <code>routing_hints.criticality: low</code>.</p>
      <p>Watch the protocol view at the bottom — the envelope landed at
      the head of the chain. The whole audit trail starts here.</p>`,
    highlight: "#wire-panel",
    nextLabel: "Next: the bot drafts →",
    auto: true,
    autoDelayMs: 4500,
    onEnter: () => walkthroughFireDemoTicket("INC-48219", "low"),
  },

  // ----------- Step 3: bot drafts -----------
  {
    title: "The bot drafts a response.",
    text: `
      <p>The bot has completed the task. Its draft is now in Maya's
      queue (left), and it carried <em>measurement signals</em> back
      to the protocol: confidence, model_id, latency.</p>
      <p>These aren't free text — they're typed fields in
      <code>Artefact.routing_hints</code>. The protocol will use them
      next.</p>`,
    highlight: "#maya-queue",
    nextLabel: "Next: routing decides →",
    auto: true,
    autoDelayMs: 4500,
    onEnter: () => walkthroughCompleteAsBot("low"),
  },

  // ----------- Step 4: routing decisions auto-fire -----------
  {
    title: "Routing decisions fire automatically.",
    text: `
      <p>Look at the protocol view below — two amber-bordered entries
      just appeared on the chain: <code>review.depth</code> and
      <code>escalate.auto</code>. The routing policy read the hints
      and decided: low criticality + high confidence → spot-check; no
      auto-escalation needed.</p>
      <p>Click any entry in the chain to expand the full JSON. The
      <em>route_decision</em> artefacts record which hints the policy
      consulted and what it concluded.</p>`,
    highlight: "#wire-panel",
    nextLabel: "Next: now YOU override →",
    auto: true,
    autoDelayMs: 5500,
  },

  // ----------- Step 5: USER must override (real interaction) -----------
  {
    title: "Your turn: override the bot.",
    text: `
      <p>Click the ticket in your queue. Edit the draft. Pick at least
      one tag, write a brief rationale, then click <strong>Override &
      send</strong>.</p>
      <p>Your edit becomes a typed JSON-Patch diff, signed into an
      <code>override</code> artefact that cites the bot's draft as its
      <code>based_on</code>. That's the override-as-data dividend.</p>`,
    highlight: "#maya-queue",
    hint: "Waiting for you to override one ticket…",
    nextLabel: "Next →",
    auto: false,
    awaitsUser: true,
  },

  // ----------- Step 6: critical ticket → auto-escalation -----------
  {
    title: "Now a high-stakes ticket.",
    text: `
      <p>I just fired a <em>second</em> ticket — an urgent fraud query
      with <code>criticality: critical</code>. Watch the wire panel:
      the routing policy auto-escalated to Sam without any human
      intervention.</p>
      <p>Open a second browser tab as Sam to see the escalation queue
      live. You'll see the full lineage: <em>bot → Maya → Sam</em>.</p>
      <p><strong>That's CHAP.</strong> Wire format you can inspect,
      decisions you can audit, a chain you can prove.</p>`,
    highlight: "#wire-panel",
    nextLabel: "Done — free play",
    auto: false,
    onEnter: () => walkthroughFireDemoTicket("INC-48224", "critical").then(() => {
      walkthroughCompleteAsBot("critical");
    }),
  },
];

async function walkthroughStart() {
  // Make sure tickets are loaded — the walkthrough's step 2 needs to
  // look up the demo ticket by ID. If the user clicked fast before
  // init() finished, state.tickets might still be empty.
  if (!state.tickets || state.tickets.length === 0) {
    try { await loadTickets(); } catch (e) { console.warn("walkthrough: loadTickets failed", e); }
  }
  // Also make sure we have the workspace state.
  if (!state.workspace) {
    try { await refreshWorkspace(); } catch (e) { console.warn("walkthrough: refreshWorkspace failed", e); }
  }

  // Offer to clear prior state so the walkthrough is reproducible.
  const ws = state.workspace;
  const hasState = ws && ws.evidence_head > 4;  // 4 = the boot envelopes (create + 3 joins)
  if (hasState) {
    const ok = confirm(
      "The guided walkthrough works best from a clean workspace. " +
      "Reset the chain and tasks to start fresh? " +
      "(Cancel to leave existing state alone — the walkthrough will still run, " +
      "but the wire will already be populated.)"
    );
    if (ok) {
      await fetch("/api/reset", { method: "POST" });
      state.wireEntries = [];
      state.lastChainLen = 0;
      $("wire-body").innerHTML = "";
      $("wire-strip-seq").textContent = "—";
      $("wire-strip-method").textContent = "—";
      await refreshWorkspace();
    }
  }
  walkthrough.active = true;
  walkthrough.step = 0;
  $("walkthrough").hidden = false;
  $("restart-walkthrough").hidden = true;  // hide while running

  // Auto-open the wire panel in compact mode so the user can SEE the
  // protocol envelopes accumulating as the walkthrough narrates them.
  // Compact = bottom-left half of viewport so the walkthrough panel on
  // the right stays usable.
  const wp = $("wire-panel");
  wp.classList.add("compact");
  wp.hidden = false;
  renderAllWireEntries();

  walkthroughGoTo(1);
}

function walkthroughStop() {
  walkthrough.active = false;
  walkthrough.step = 0;
  walkthrough.awaitingUser = false;
  if (walkthrough.autoTimer) {
    clearTimeout(walkthrough.autoTimer);
    walkthrough.autoTimer = null;
  }
  $("walkthrough").hidden = true;
  $("glow-target").hidden = true;
  // Leave the wire panel open but return it to full-width (the user
  // probably wants to keep watching the wire after the tour ends).
  // If they want it closed they can hit the ✕ button.
  $("wire-panel").classList.remove("compact");
  // Make the restart-tour button available
  $("restart-walkthrough").hidden = false;
}

function walkthroughGoTo(stepNum) {
  if (walkthrough.autoTimer) {
    clearTimeout(walkthrough.autoTimer);
    walkthrough.autoTimer = null;
  }
  walkthrough.step = stepNum;
  walkthrough.awaitingUser = false;

  const step = WALKTHROUGH_STEPS[stepNum];
  if (!step) { walkthroughStop(); return; }

  // Update panel contents
  $("walkthrough-step-num").textContent = String(stepNum);
  $("walkthrough-title").textContent = step.title;
  $("walkthrough-text").innerHTML = step.text;
  $("walkthrough-progress-fill").style.width = (stepNum / 6 * 100) + "%";

  // Hint + button state
  if (step.awaitsUser) {
    walkthrough.awaitingUser = true;
    $("walkthrough").classList.add("awaiting-user");
    $("walkthrough-hint").textContent = step.hint || "Waiting for you…";
    $("walkthrough-next").disabled = true;
    $("walkthrough-next").textContent = "Waiting…";
  } else {
    $("walkthrough").classList.remove("awaiting-user");
    $("walkthrough-hint").textContent = step.auto ? "auto-advancing" : "";
    $("walkthrough-next").disabled = false;
    $("walkthrough-next").textContent = step.nextLabel || (stepNum >= 6 ? "Done" : "Next →");
  }

  // Apply the highlight glow
  highlightTarget(step.highlight);

  // Fire any side-effect for this step (real envelopes)
  if (step.onEnter) {
    Promise.resolve(step.onEnter()).catch((e) => {
      console.warn("walkthrough step side-effect failed:", e);
    });
  }

  // Auto-advance if configured
  if (step.auto && !step.awaitsUser) {
    walkthrough.autoTimer = setTimeout(() => {
      if (walkthrough.active && walkthrough.step === stepNum) {
        walkthroughGoTo(stepNum + 1);
      }
    }, step.autoDelayMs ?? 4500);
  }
}

function walkthroughAdvance() {
  if (!walkthrough.active) return;
  if (walkthrough.awaitingUser) return;
  if (walkthrough.step >= 6) {
    walkthroughStop();
    return;
  }
  walkthroughGoTo(walkthrough.step + 1);
}

/**
 * Highlight a DOM element with a glowing border.
 * `selector` is a CSS selector for the target.
 */
function highlightTarget(selector) {
  const glow = $("glow-target");
  if (!selector) { glow.hidden = true; return; }
  const target = document.querySelector(selector);
  if (!target) { glow.hidden = true; return; }
  const rect = target.getBoundingClientRect();
  if (rect.width === 0 || rect.height === 0) { glow.hidden = true; return; }
  // Position the glow over the target using page coordinates
  glow.hidden = false;
  const pad = 4;
  glow.style.top    = (rect.top + window.scrollY - pad) + "px";
  glow.style.left   = (rect.left + window.scrollX - pad) + "px";
  glow.style.width  = (rect.width + pad * 2) + "px";
  glow.style.height = (rect.height + pad * 2) + "px";
}

// Reposition the glow on window resize/scroll so it stays attached.
window.addEventListener("resize", () => {
  if (walkthrough.active) {
    const step = WALKTHROUGH_STEPS[walkthrough.step];
    if (step) highlightTarget(step.highlight);
  }
});
window.addEventListener("scroll", () => {
  if (walkthrough.active) {
    const step = WALKTHROUGH_STEPS[walkthrough.step];
    if (step) highlightTarget(step.highlight);
  }
}, { passive: true });

/**
 * Fire a real task.create envelope against /rpc with the given ticket
 * ID and criticality. Returns the created task_id.
 */
async function walkthroughFireDemoTicket(ticketId, criticality) {
  const ticket = state.tickets.find((t) => t.id === ticketId);
  if (!ticket) {
    console.warn(`walkthrough: ticket ${ticketId} not found in state.tickets (length=${state.tickets.length})`);
    // Surface the failure to the user instead of silently advancing.
    $("walkthrough-text").innerHTML +=
      `<p style="color:var(--ember-d); margin-top:8px;">⚠ Could not load ticket ${ticketId} — try clicking "↻ Guided tour" again after the page finishes loading.</p>`;
    return;
  }
  const env = {
    jsonrpc: "2.0",
    id:      "walkthrough-create-" + Date.now(),
    method:  "task.create",
    params: {
      workspace_id: WORKSPACE_ID,
      kind:         "draft_response",
      assignee:     "agent:triage-bot@local",
      delegator:    "service:coord@local",
      input: {
        ticket_id: ticket.id,
        subject:   ticket.subject,
        body:      ticket.body,
        customer:  ticket.customer,
      },
      routing_hints: { ...ticket.routing_hints, criticality },
    },
  };
  const resp = await fetch("/rpc", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(env),
  }).then((r) => r.json());
  const taskId = resp.result?.task_id;
  if (taskId) {
    if (criticality === "critical") walkthrough.critTaskId = taskId;
    else                            walkthrough.demoTaskId = taskId;
  }
  return taskId;
}

/**
 * Have "the bot" complete the most recent walkthrough task. This is
 * a stand-in for the Ollama agent — the walkthrough can't depend on a
 * model being installed, so we simulate the bot's task.complete with a
 * canned response. The protocol surface is identical to what the real
 * agent would emit.
 */
async function walkthroughCompleteAsBot(criticality) {
  const taskId = criticality === "critical" ? walkthrough.critTaskId : walkthrough.demoTaskId;
  if (!taskId) return;
  // Confidence by criticality so the routing fires the way the narration claims:
  //   low      → confidence 0.92 → spot_check, no escalation
  //   critical → confidence 0.55 → full review + escalate.auto fires
  const confidence = criticality === "critical" ? 0.55 : 0.92;
  const bodyText = criticality === "critical"
    ? "Thanks for getting in touch. We will look into this charge."
    : "Hi! Your order is still in transit. Most arrive within 5–7 business days. We're sorry for any anxiety the wait causes.";

  // First mark in_progress (matches what the real bot does)
  await fetch("/rpc", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      jsonrpc: "2.0", id: "wt-progress-" + Date.now(), method: "task.update",
      params: { workspace_id: WORKSPACE_ID, task_id: taskId, from: "agent:triage-bot@local", state: "in_progress" },
    }),
  });
  // Then complete with measurement signals
  await fetch("/rpc", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      jsonrpc: "2.0", id: "wt-complete-" + Date.now(), method: "task.complete",
      params: {
        workspace_id: WORKSPACE_ID,
        task_id:      taskId,
        from:         "agent:triage-bot@local",
        output: { body: bodyText, tone: "warm_professional", severity: criticality === "critical" ? "high" : "low" },
        confidence,
        routing_hints: { confidence, model_id: "walkthrough-sim", cost_consumed_usd: 0, latency_ms: 1200 },
        review_requested: true,
        reviewers:        [MAYA_URI],
        rule:             "any_one_approves",
      },
    }),
  });
}

/**
 * Hook from sendOverride — if the walkthrough is awaiting a user
 * override, this advances it to step 6.
 */
function walkthroughOnUserOverride() {
  if (walkthrough.active && walkthrough.awaitingUser && walkthrough.step === 5) {
    walkthroughGoTo(6);
  }
}



init().catch((e) => console.error("init failed", e));

// Surface uncaught errors in a visible banner so silent JS failures
// become diagnosable. Errors are also still in DevTools console.
window.addEventListener("error", (e) => {
  const msg = `${e.message} (${e.filename?.split("/").pop()}:${e.lineno})`;
  console.error("[playground]", e.error || e.message);
  // Drop a one-line banner at top — non-intrusive but visible
  if (!document.getElementById("err-banner")) {
    const b = document.createElement("div");
    b.id = "err-banner";
    b.style.cssText = "position:fixed;top:0;left:0;right:0;background:#EA4700;color:#fff;font-family:sans-serif;font-size:12px;padding:6px 12px;z-index:9999;text-align:center;";
    b.textContent = "⚠ JS error: " + msg;
    document.body.appendChild(b);
  }
});
window.addEventListener("unhandledrejection", (e) => {
  console.error("[playground] unhandled rejection:", e.reason);
});
