// Relative, not "/render.mjs": the browser resolves this to the same file, and
// a test can then import this module directly without a server rooted at "/".
import { renderInto, hasHidden, pretty, reveal, sameJson } from "./render.mjs";

const $ = id => document.getElementById(id);
// The capability stays in the fragment. A fragment is never sent to the
// server and never reaches its log, and leaving it there means reloading the
// page still works. Stripping it looks tidier and breaks the first thing
// anyone does when a page seems stuck.
const reviewer = new URLSearchParams(location.hash.slice(1)).get("reviewer") || "";

const LABELS = {
  "workspace.create": "The workspace opens. Chaining starts at the first event.",
  "participant.join": "A participant joins. Only members can act.",
  "task.create": "The agent opens a piece of work.",
  "review.request": "The draft becomes the artefact under review.",
  "decide.approve": "A human approved the draft as written.",
  "decide.override": "A human changed the draft. The patch and reason are recorded.",
  "decide.reject": "A human refused the draft. Nothing downstream may run.",
};

let desk = null, selected = null, busy = false, dirty = false;

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "X-CHAP-Reviewer": reviewer, ...(options.body ? { "Content-Type": "application/json" } : {}) },
  });
  const body = await response.json().catch(() => ({ error: response.statusText }));
  if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
  return body;
}

function notice(message) {
  const element = $("notice");
  element.hidden = !message;
  element.textContent = message || "";
}

// -- the verification badge -------------------------------------------------
//
// This badge is the only claim on screen that the record is intact, so it is
// written on every path, including every failure path. It shows green from a
// fresh "verified" verdict and from nothing else; a verdict that did not
// arrive is a red badge, not a stale green one.

function renderVerification(verdict) {
  const badge = $("verification");
  if (!verdict) {
    badge.className = "badge bad";
    badge.textContent = "Cannot verify the chain right now";
    return;
  }
  if (verdict.status === "verified" && verdict.ok === true) {
    badge.className = "badge good";
    badge.textContent = `Chain verified, ${verdict.entries_checked} of ${verdict.entries_total} events`;
    return;
  }
  badge.className = "badge bad";
  badge.textContent = verdict.status === "not_evaluated"
    ? `Chain not evaluated: ${verdict.reason || "incomplete coverage"}`
    : `Chain did not verify: ${verdict.status || "unknown"}`;
}

// -- rendering --------------------------------------------------------------

function renderTask(task, hint) {
  $("state").textContent = task.state === "review_requested" ? "waiting for you" : task.state;
  $("state").className = "pill " + (task.state === "review_requested" ? "pending" : "settled");
  $("hint").hidden = !hint;
  $("hint").textContent = hint || "";
  renderInto($("context"), pretty(task.context));
  renderInto($("draft"), pretty(task.draft));
  $("digest").textContent = task.digest;
  $("hidden-warning").hidden = !hasHidden(task.draft);

  const open = task.state === "review_requested";
  for (const id of ["approve", "edit", "reject", "rationale", "tags", "editor"]) {
    $(id).disabled = !open;
  }
  if (!dirty) $("editor").value = pretty(task.draft);

  if (task.decision) {
    const { kind, comment } = task.decision;
    renderInto($("changes"), `Recorded as decide.${kind}${comment ? ": " + comment : ""}`);
  } else {
    describeEdit();
  }
  renderInto($("output"), task.allowed ? pretty(task.output)
    : task.state === "declined" ? "Rejected. Your code must not run the next step."
    : "Nothing yet. result() raises ReviewPending until a human decides.");

  $("step-review").className = open ? "active" : "done";
  $("step-result").className = task.allowed ? "active" : "";
}

function describeEdit() {
  if (!desk?.task) return;
  let parsed;
  try { parsed = JSON.parse($("editor").value); }
  catch { $("changes").textContent = "That is not valid JSON yet."; return; }
  const same = sameJson(parsed, desk.task.draft);
  $("changes").textContent = same
    ? "Identical to the draft. Approve as written, or change something."
    : hasHidden(parsed) ? "Your version still contains invisible characters."
    : "Changed. Approving this records a decide.override with the patch.";
}

function renderTimeline(entries) {
  const timeline = $("timeline");
  timeline.replaceChildren();
  for (const entry of entries) {
    const envelope = entry.envelope;
    const node = document.createElement("details");
    node.className = "event" + (envelope.method.startsWith("decide.") ? " human" : "");
    const summary = document.createElement("summary");
    const method = document.createElement("strong");
    method.textContent = envelope.method;
    const seq = document.createElement("span");
    seq.className = "seq";
    seq.textContent = "#" + String(entry.seq).padStart(2, "0");
    const label = document.createElement("div");
    label.className = "event-label";
    label.textContent = LABELS[envelope.method] || "CHAP event";
    const actor = document.createElement("div");
    actor.className = "event-actor";
    actor.textContent = envelope.params.from || "workspace";
    const code = document.createElement("pre");
    renderInto(code, pretty(entry));
    summary.append(method, seq, label, actor);
    node.append(summary, code);
    timeline.append(node);
  }
}

function renderTasks(tasks) {
  const select = $("task-select");
  select.replaceChildren(...[...tasks].reverse().map((task, index) => {
    const option = document.createElement("option");
    option.value = task.task_id;
    // kind is written by whoever proposed the draft, so it gets the same
    // treatment as the draft itself.
    const kind = reveal(task.kind.slice(0, 40).replaceAll("_", " "))
      .map(run => run.hidden ? `[${run.text}]` : run.text).join("");
    option.textContent = `${kind} · `
      + `${task.state === "review_requested" ? "pending" : task.decision_kind || task.state} · `
      + `#${tasks.length - index}`;
    return option;
  }));
  select.value = selected ?? "";
}

// -- polling ----------------------------------------------------------------

async function refresh() {
  let next;
  try {
    next = await api("/api/desk" + (selected ? "?task=" + encodeURIComponent(selected) : ""));
  } catch (error) {
    renderVerification(null);
    $("connection").textContent = "Not connected";
    throw error;
  }
  desk = next;
  $("connection").textContent = "Running on your computer";
  $("workspace").textContent = desk.workspace;
  renderVerification(desk.verification);
  renderTasks(desk.tasks);
  if (desk.task) {
    if (desk.task.task_id !== selected) { selected = desk.task.task_id; dirty = false; }
    $("task-select").value = selected;
    renderTask(desk.task, desk.hint);
    renderTimeline(desk.entries);
  }
}

async function poll() {
  try { if (!busy) { await refresh(); notice(""); } }
  catch (error) { notice(error.message); }
  setTimeout(poll, 2000);
}

// -- actions ----------------------------------------------------------------

async function act(work) {
  busy = true;
  try { await work(); notice(""); await refresh(); }
  catch (error) { notice(error.message); }
  finally { busy = false; }
}

async function decide(action) {
  const body = {
    action,
    rationale: $("rationale").value,
    tags: $("tags").value.split(",").map(tag => tag.trim()).filter(Boolean),
    expected_digest: desk.task.digest,
  };
  if (action === "edit") body.edited = JSON.parse($("editor").value);
  await api(`/api/reviews/${encodeURIComponent(selected)}/decision`,
            { method: "POST", body: JSON.stringify(body) });
  dirty = false;
  $("rationale").value = ""; $("tags").value = "";
}

$("approve").addEventListener("click", () => act(() => decide("approve")));
$("edit").addEventListener("click", () => act(() => decide("edit")));
$("reject").addEventListener("click", () => act(() => decide("reject")));
$("editor").addEventListener("input", () => { dirty = true; describeEdit(); });
$("task-select").addEventListener("change", event => {
  selected = event.target.value; dirty = false; act(() => {});
});

$("submit-own").addEventListener("click", () => act(async () => {
  const created = await api("/api/drafts", {
    method: "POST",
    body: JSON.stringify({ kind: $("own-kind").value, draft: JSON.parse($("own-draft").value) }),
  });
  selected = created.task_id; dirty = false;
}));

$("export").addEventListener("click", () => act(async () => {
  const evidence = await api("/api/evidence");
  const url = URL.createObjectURL(new Blob([JSON.stringify(evidence, null, 2)],
                                           { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url; link.download = "chap-evidence.json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}));

// -- boot -------------------------------------------------------------------

fetch("/api/desk", { headers: { "X-CHAP-Reviewer": reviewer } })
  .then(response => response.json())
  .then(body => {
    for (const [name, scenario] of Object.entries(body.scenarios || {})) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = scenario.title;
      button.addEventListener("click", () => act(async () => {
        const created = await api("/api/examples/" + encodeURIComponent(name),
                                  { method: "POST", body: "{}" });
        selected = created.task_id; dirty = false;
      }));
      $("examples").append(button);
    }
  })
  .catch(() => {});

renderVerification(null);
poll();
