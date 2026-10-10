// How an artefact is shown and edited, by its shape: a code change as a diff
// with each file editable whole, a message as a letter, text as text, and
// anything else as JSON. Editing gives back the edited artefact; the desk
// turns the difference into an RFC 6902 patch for decide.override.

import { applyFile, fileStats, parsePatch, rewritePatch } from "./diff.js";

export const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(node.dataset, v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) if (c !== null && c !== undefined && c !== false) node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return node;
};

export function shapeOf(artefact) {
  if (artefact && typeof artefact === "object" && !Array.isArray(artefact)) {
    if (typeof artefact.patch === "string") return "code";
    if (typeof artefact.body === "string") return "message";
    return "json";
  }
  if (typeof artefact === "string") return "text";
  return "json";
}

/** A short line for a queue entry or a title. */
export function titleOf(task) {
  const a = task.artefact ?? task.output ?? null;
  return task.input?.summary ?? a?.summary ?? a?.subject ?? task.input?.subject ?? task.input?.title ?? `${task.kind} ${task.task_id}`;
}

export function renderJson(value) {
  return el("pre", { class: "json", text: JSON.stringify(value, null, 2) });
}

// -- the diff -----------------------------------------------------------------

function diffTable(file) {
  const table = el("table", { class: "diff-table" });
  for (const h of file.hunks) {
    table.append(el("tr", { class: "hunk" }, el("td", { class: "ln" }), el("td", { class: "ln" }), el("td", { class: "sign" }), el("td", { text: `${h.header}` })));
    let o = h.oldStart, n = h.newStart;
    for (const l of h.lines) {
      const cls = l.type === "+" ? "add" : l.type === "-" ? "del" : "ctx";
      table.append(el("tr", { class: cls },
        el("td", { class: "ln", text: l.type === "+" ? "" : String(o) }),
        el("td", { class: "ln", text: l.type === "-" ? "" : String(n) }),
        el("td", { class: "sign", text: l.type === " " ? "" : l.type }),
        el("td", {}, l.text, l.noNewline ? el("span", { class: "nonl", text: "  (no newline at end of file)" }) : null)));
      if (l.type !== "+") o++;
      if (l.type !== "-") n++;
    }
  }
  return table;
}

const MODE_NAMES = { "100644": "file", "100755": "executable", "120000": "symbolic link", "160000": "submodule" };

/**
 * Whether the contents travelling with a file agree with its section of
 * the patch: its hunks, applied to the content before, give the content
 * after. Only then is the file offered for editing whole.
 */
export function contentsAgree(info, section) {
  if (!info || section.binary || section.status === "deleted" || typeof info.after !== "string") return false;
  return applyFile(info.status === "added" || section.status === "added" ? null : (info.before ?? null), section) === info.after;
}

function modeBadge(file) {
  if (file.status === "added" && file.mode && file.mode !== "100644") return el("span", { class: "badge warn", text: MODE_NAMES[file.mode] ?? `mode ${file.mode}` });
  if (file.oldMode && file.newMode) return el("span", { class: "badge warn", text: `${MODE_NAMES[file.oldMode] ?? file.oldMode} to ${MODE_NAMES[file.newMode] ?? file.newMode}` });
  return null;
}

/**
 * The files of a patch, each a collapsible section with its own line
 * numbers. `editable` adds an Edit control to each text file whose
 * contents travelled with the change and agree with the patch; `onEdit(path)`
 * is called with the path. `editing` maps a path to its content under edit.
 */
export function renderDiff(patch, { files = [], editable = false, onEdit = null, editing = Object.create(null) } = {}) {
  const root = el("div", { class: "diff" });
  const meta = new Map(files.map((f) => [f.path, f]));
  const parsed = parsePatch(patch);
  for (const file of parsed) {
    const stats = fileStats(file);
    const info = meta.get(file.path);
    const editingThis = Object.prototype.hasOwnProperty.call(editing, file.path);
    const canEdit = editable && contentsAgree(info, file);
    const summary = el("summary", {},
      el("span", { class: "path", text: file.path }),
      file.status !== "modified" ? el("span", { class: `badge ${file.status === "added" ? "ok" : file.status === "deleted" ? "bad" : ""}`, text: file.status }) : null,
      modeBadge(file),
      el("span", { class: "stats" }, el("span", { class: "add", text: `+${stats.added}` }), el("span", { class: "del", text: `-${stats.removed}` })),
      canEdit ? el("button", { class: "btn small edit", text: editingThis ? "Editing" : "Edit", onclick: (e) => { e.preventDefault(); e.stopPropagation(); onEdit?.(file.path); } })
        : editable && !file.binary && file.status !== "deleted" ? el("span", { class: "small muted", text: "not editable here" }) : null);
    const details = el("details", { class: "diff-file card", open: true, id: `file-${file.path}` }, summary);
    if (editingThis) {
      const area = el("textarea", { class: "code", "aria-label": `Content of ${file.path} after your edit`, spellcheck: "false" });
      area.value = editing[file.path];
      area.addEventListener("input", () => { editing[file.path] = area.value; });
      details.append(el("div", { class: "editor" }, el("div", { class: "editor-bar" }, "The whole file as it should be after the change. The patch is written again from it, and you see the result before it is sent."), area));
    } else if (file.binary) {
      details.append(el("div", { class: "diff-binary", text: "A binary file: the desk cannot show its content. Approve it only if you know what it is." }));
    } else {
      details.append(diffTable(file));
    }
    root.append(details);
  }
  return root;
}

/** The banner for a patch the desk cannot show faithfully, or null. */
export function anomalyBanner(patch) {
  const anomalies = parsePatch(patch).anomalies;
  if (!anomalies.length) return null;
  return el("div", { class: "alert" }, el("b", { text: "Do not approve this patch. " }), "The desk cannot show it as git would apply it: ", anomalies.join("; "), ".");
}

// -- shapes -------------------------------------------------------------------

const LONG = (s) => s.length > 80 || s.includes("\n");

function renderMessage(a) {
  const dl = el("dl", { class: "letter" });
  for (const key of Object.keys(a)) {
    if (key === "body") continue;
    const v = a[key];
    dl.append(el("div", { class: "field" }, el("dt", { text: key }), el("dd", { text: typeof v === "string" ? v : JSON.stringify(v) })));
  }
  dl.append(el("div", { class: "body", text: a.body }));
  return dl;
}

/**
 * Show an artefact. Returns the element. When `editing` is given, the
 * artefact is shown in its editable form and `editing.value()` gives the
 * edited artefact; `editing.state` is kept between renders.
 */
export function renderArtefact(artefact, { editing = null } = {}) {
  const shape = shapeOf(artefact);
  if (shape === "code") {
    const head = el("div", { class: "stack", style: "margin-bottom:12px" });
    const banner = anomalyBanner(artefact.patch);
    if (banner) head.append(banner);
    if (artefact.summary) head.append(el("div", {}, el("b", { text: artefact.summary })));
    const facts = [];
    if (artefact.branch) facts.push(["branch", artefact.branch]);
    if (artefact.base) facts.push(["base", String(artefact.base).slice(0, 12)]);
    if (artefact.drafted_by) facts.push(["drafted by", artefact.drafted_by]);
    if (artefact.repo) facts.push(["repository", artefact.repo]);
    if (facts.length) head.append(el("div", { class: "row small muted" }, facts.map(([k, v]) => el("span", {}, `${k} `, el("b", { class: "mono", text: v })))));
    const chips = el("div", { class: "files" });
    for (const f of parsePatch(artefact.patch)) {
      const st = fileStats(f);
      chips.append(el("span", { class: "file-chip", onclick: () => document.getElementById(`file-${f.path}`)?.scrollIntoView({ block: "start", behavior: "smooth" }) }, f.path, el("span", { class: "add", text: f.binary ? "bin" : `+${st.added}` }), el("span", { class: "del", text: f.binary ? "" : `-${st.removed}` })));
    }
    head.append(chips);
    if (editing) {
      const state = editing.state;
      state.files ??= Object.create(null);
      const files = artefact.files ?? [];
      const render = () => renderDiff(artefact.patch, { files, editable: true, editing: state.files, onEdit: (path) => {
        if (Object.prototype.hasOwnProperty.call(state.files, path)) delete state.files[path];
        else state.files[path] = files.find((f) => f.path === path).after;
        box.replaceChildren(render());
      } });
      const box = el("div", {}, render());
      editing.value = () => {
        const edits = Object.create(null);
        const contents = Object.create(null);
        for (const f of files) {
          contents[f.path] = f.before ?? null;
          if (Object.prototype.hasOwnProperty.call(state.files, f.path) && state.files[f.path] !== f.after) edits[f.path] = state.files[f.path];
        }
        if (!Object.keys(edits).length) return artefact;
        const patch = rewritePatch(artefact.patch, edits, contents);
        const stats = new Map(parsePatch(patch).map((f) => [f.path, fileStats(f)]));
        const changed = files.map((f) => (Object.prototype.hasOwnProperty.call(edits, f.path) ? { ...f, after: edits[f.path], added: stats.get(f.path)?.added ?? f.added, removed: stats.get(f.path)?.removed ?? f.removed } : f));
        return { ...artefact, patch, files: changed };
      };
      return el("div", {}, head, el("div", { class: "small muted", style: "margin-bottom:8px", text: "Press Edit on a file to change it whole; the patch is written again from your version, and you see the result before it is sent." }), box);
    }
    return el("div", {}, head, renderDiff(artefact.patch, { files: artefact.files ?? [] }));
  }
  if (shape === "message") {
    if (!editing) return renderMessage(artefact);
    const state = editing.state;
    state.fields ??= { ...artefact };
    const form = el("div", { class: "form" });
    for (const [key, v] of Object.entries(artefact)) {
      const field = el("div", { class: "field" }, el("label", { text: key }));
      if (typeof v === "string") {
        const input = (key === "body" || LONG(v)) ? el("textarea", { "aria-label": key }) : el("input", { "aria-label": key });
        input.value = state.fields[key];
        input.addEventListener("input", () => { state.fields[key] = input.value; });
        field.append(input);
      } else {
        const area = el("textarea", { class: "code", "aria-label": key, style: "min-height:80px" });
        area.value = JSON.stringify(state.fields[key], null, 2);
        area.addEventListener("input", () => { try { state.fields[key] = JSON.parse(area.value); area.setCustomValidity(""); } catch { area.setCustomValidity("not JSON"); } });
        field.append(area);
      }
      form.append(field);
    }
    editing.value = () => ({ ...state.fields });
    return form;
  }
  if (shape === "text") {
    if (!editing) return el("div", { class: "letter" }, el("div", { class: "body", text: artefact }));
    const area = el("textarea", { "aria-label": "The text" });
    area.value = editing.state.text ?? artefact;
    area.addEventListener("input", () => { editing.state.text = area.value; });
    editing.value = () => editing.state.text ?? artefact;
    return area;
  }
  if (!editing) return renderJson(artefact);
  const area = el("textarea", { class: "code", "aria-label": "The artefact as JSON" });
  area.value = editing.state.json ?? JSON.stringify(artefact, null, 2);
  area.addEventListener("input", () => { editing.state.json = area.value; });
  editing.value = () => JSON.parse(editing.state.json ?? JSON.stringify(artefact));
  return area;
}

/** Relative time, short: "4 s", "3 min", "2 h", "5 d". */
export function ago(iso, now = Date.now()) {
  const s = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  if (s < 86400) return `${Math.round(s / 3600)} h`;
  return `${Math.round(s / 86400)} d`;
}

export function stateBadge(state) {
  const cls = state === "completed" ? "ok" : state === "declined" ? "bad" : state === "review_requested" ? "warn" : state === "in_progress" ? "accent" : "";
  const text = { review_requested: "waiting for a decision", in_progress: "being revised", completed: "approved", declined: "rejected", created: "open" }[state] ?? state;
  return el("span", { class: `badge ${cls}`, text });
}
