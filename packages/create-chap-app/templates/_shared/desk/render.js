// How an artefact is shown and edited, by its shape: a code change as a diff
// with each file editable whole, a message as a letter, text as text, and
// anything else as JSON. Editing gives back the edited artefact; the desk
// turns the difference into an RFC 6902 patch for decide.override.

import { fileStats, parsePatch, rewritePatch } from "./diff.js";

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

/**
 * The files of a patch, each a collapsible section with its own line
 * numbers. `editable` adds an Edit control per text file with content
 * beside it; `onEdit(path)` is called with the path.
 */
export function renderDiff(patch, { files = [], editable = false, onEdit = null, editing = {} } = {}) {
  const root = el("div", { class: "diff" });
  const meta = Object.fromEntries(files.map((f) => [f.path, f]));
  for (const file of parsePatch(patch)) {
    const stats = fileStats(file);
    const info = meta[file.path];
    const canEdit = editable && info && typeof info.after === "string" && !file.binary;
    const summary = el("summary", {},
      el("span", { class: "path", text: file.path }),
      file.status !== "modified" ? el("span", { class: `badge ${file.status === "added" ? "ok" : file.status === "deleted" ? "bad" : ""}`, text: file.status }) : null,
      el("span", { class: "stats" }, el("span", { class: "add", text: `+${stats.added}` }), el("span", { class: "del", text: `-${stats.removed}` })),
      canEdit ? el("button", { class: "btn small edit", text: editing[file.path] !== undefined ? "Editing" : "Edit", onclick: (e) => { e.preventDefault(); e.stopPropagation(); onEdit?.(file.path); } }) : null);
    const details = el("details", { class: "diff-file card", open: true, id: `file-${file.path}` }, summary);
    if (editing[file.path] !== undefined) {
      const area = el("textarea", { class: "code", "aria-label": `Content of ${file.path} after your edit`, spellcheck: "false" });
      area.value = editing[file.path];
      area.addEventListener("input", () => { editing[file.path] = area.value; });
      details.append(el("div", { class: "editor" }, el("div", { class: "editor-bar" }, `The whole file as it should be after the change. The patch is written again from it.`), area));
    } else if (file.binary) {
      details.append(el("div", { class: "diff-binary", text: "Binary file; the patch carries it as is." }));
    } else {
      details.append(diffTable(file));
    }
    root.append(details);
  }
  return root;
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
    if (artefact.summary) head.append(el("div", {}, el("b", { text: artefact.summary })));
    const facts = [];
    if (artefact.branch) facts.push(["branch", artefact.branch]);
    if (artefact.base) facts.push(["base", String(artefact.base).slice(0, 12)]);
    if (artefact.drafted_by) facts.push(["drafted by", artefact.drafted_by]);
    if (artefact.repo) facts.push(["repository", artefact.repo]);
    if (facts.length) head.append(el("div", { class: "row small muted" }, facts.map(([k, v]) => el("span", {}, `${k} `, el("b", { class: "mono", text: v })))));
    const chips = el("div", { class: "files" });
    for (const f of artefact.files ?? []) chips.append(el("span", { class: "file-chip", onclick: () => document.getElementById(`file-${f.path}`)?.scrollIntoView({ block: "start", behavior: "smooth" }) }, f.path, el("span", { class: "add", text: `+${f.added ?? "bin"}` }), el("span", { class: "del", text: `-${f.removed ?? "bin"}` })));
    if (artefact.files?.length) head.append(chips);
    if (editing) {
      const state = editing.state;
      state.files ??= {};
      const hasContents = (artefact.files ?? []).some((f) => typeof f.after === "string");
      if (!hasContents) {
        // No file contents travelled with the patch, so the patch itself is edited.
        const area = el("textarea", { class: "code", "aria-label": "The patch" });
        area.value = state.patch ?? artefact.patch;
        area.addEventListener("input", () => { state.patch = area.value; });
        editing.value = () => ({ ...artefact, patch: state.patch ?? artefact.patch });
        return el("div", {}, head, el("div", { class: "small muted", style: "margin-bottom:6px", text: "This change carries no file contents, so the patch is edited as text. Hunk headers must stay right for it to apply." }), area);
      }
      const render = () => {
        const d = renderDiff(artefact.patch, { files: artefact.files, editable: true, editing: state.files, onEdit: (path) => {
          if (state.files[path] === undefined) state.files[path] = artefact.files.find((f) => f.path === path).after;
          else delete state.files[path];
          box.replaceChildren(render());
        } });
        return d;
      };
      const box = el("div", {}, render());
      editing.value = () => {
        const edits = {};
        const contents = {};
        for (const f of artefact.files) {
          contents[f.path] = f.before ?? null;
          if (state.files[f.path] !== undefined && state.files[f.path] !== f.after) edits[f.path] = state.files[f.path];
        }
        if (!Object.keys(edits).length) return artefact;
        const patch = rewritePatch(artefact.patch, edits, contents);
        const stats = Object.fromEntries(parsePatch(patch).map((f) => [f.path, fileStats(f)]));
        const files = artefact.files.map((f) => (edits[f.path] === undefined ? f : { ...f, after: edits[f.path], added: stats[f.path]?.added ?? f.added, removed: stats[f.path]?.removed ?? f.removed }));
        return { ...artefact, patch, files };
      };
      return el("div", {}, head, el("div", { class: "small muted", style: "margin-bottom:8px", text: "Press Edit on a file to change it whole; the patch is written again from your version." }), box);
    }
    return el("div", {}, head, renderDiff(artefact.patch, { files: artefact.files }));
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
