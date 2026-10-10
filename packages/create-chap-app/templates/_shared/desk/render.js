// How an artefact is shown and edited, by its shape: a code change as a diff
// with each file editable whole, a branch as its commits read one by one,
// a message as a letter, text as text, and anything else as JSON. Editing
// gives back the edited artefact; the desk turns the difference into an
// RFC 6902 patch for decide.override. A branch is decided as its commits
// stand, so it has no edit.

import { applyFile, fileStats, parsePatch, rewritePatch, splitLines } from "./diff.js";

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
    if (Array.isArray(artefact.commits)) return "commits";
    if (typeof artefact.patch === "string") return "code";
    if (typeof artefact.body === "string") return "message";
    return "json";
  }
  if (typeof artefact === "string") return "text";
  return "json";
}

/** A short line for a queue entry or a title. A branch is named by its current proposal, which a revision can change. */
export function titleOf(task) {
  const a = task.artefact ?? task.output ?? null;
  if (Array.isArray(a?.commits) && a.summary) return a.summary;
  return task.input?.summary ?? a?.summary ?? a?.subject ?? task.input?.subject ?? task.input?.title ?? `${task.kind} ${task.task_id}`;
}

export function renderJson(value) {
  return el("pre", { class: "json", text: JSON.stringify(value, null, 2) });
}

// -- the diff -----------------------------------------------------------------

// Characters a reader cannot see, or that change how the line around them
// reads: carriage returns, line and paragraph separators, zero-width
// characters and bidirectional controls. Each is shown as a marker.
const INVISIBLE = /[\r\u2028\u2029\u200B-\u200F\u202A-\u202E\u2060-\u2069\uFEFF\u061C]/g;

/** Whether a line holds an invisible character other than a carriage return that ends it. */
export const hasInvisible = (text) => [...text.matchAll(INVISIBLE)].some((m) => !(m[0] === "\r" && m.index === text.length - 1));

/** A line's text with each invisible character shown as a marker. */
function showText(text) {
  const parts = [];
  let last = 0;
  for (const m of text.matchAll(INVISIBLE)) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    const ch = m[0];
    const eol = ch === "\r" && m.index === text.length - 1;
    const label = ch === "\r" ? "CR" : `U+${ch.codePointAt(0).toString(16).toUpperCase().padStart(4, "0")}`;
    parts.push(el("span", { class: eol ? "invisible eol" : "invisible", title: eol ? "a carriage return ending the line" : `an invisible character, ${label}`, text: eol ? "\u21b5" : `\u27e8${label}\u27e9` }));
    last = m.index + 1;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

/** One row of the diff: the two line numbers, the sign and the text. */
function lineRow(cls, oldNo, newNo, sign, text, noNewline = false) {
  return el("tr", { class: cls },
    el("td", { class: "ln", text: oldNo === null ? "" : String(oldNo) }),
    el("td", { class: "ln", text: newNo === null ? "" : String(newNo) }),
    el("td", { class: "sign", text: sign }),
    el("td", {}, showText(text), noNewline ? el("span", { class: "nonl", text: "  (no newline at end of file)" }) : null));
}

/** A saved comment, as a row under the line it is about. */
function commentRow(c, onRemove) {
  const row = el("tr", { class: "comment-row" }, el("td", { colspan: "4" },
    el("div", { class: "comment" },
      el("div", { class: "comment-text", text: c.text }),
      onRemove ? el("button", { class: "link small", text: "Remove", onclick: () => { onRemove(c.id); row.remove(); } }) : null)));
  return row;
}

/**
 * The rows of one file's diff. With the file's whole text (`after`), the
 * unchanged lines between and around the hunks can be opened. With
 * `commenting`, each line takes a comment: { comments, onAdd, onRemove, path }.
 */
function diffTable(file, { after = null, commenting = null } = {}) {
  const table = el("table", { class: "diff-table" });
  const whole = typeof after === "string" ? splitLines(after).lines : null;
  // Unchanged lines from..to (new numbering), shown as a row that opens them; old = new + delta.
  const gap = (from, to, delta) => {
    if (!whole || from > to) return;
    const count = to - from + 1;
    const row = el("tr", { class: "gap" }, el("td", { class: "ln" }), el("td", { class: "ln" }), el("td", { class: "sign" }),
      el("td", {}, el("button", { class: "link gap-open", text: `Show ${count} unchanged line${count === 1 ? "" : "s"}`, onclick: () => {
        const rows = [];
        for (let n = from; n <= to; n++) rows.push(withComments(lineRow("ctx context", n + delta, n, "", whole[n - 1] ?? ""), { line: n, side: "new", code: whole[n - 1] ?? "" }));
        row.replaceWith(...rows.flat());
      } })));
    table.append(row);
  };
  // A line row, with its comment control and the comments already on it.
  const withComments = (row, at) => {
    if (!commenting) return row;
    const mine = (commenting.comments ?? []).filter((c) => c.path === commenting.path && c.line === at.line && c.side === at.side);
    const button = el("button", { class: "add-comment", title: "Comment on this line", "aria-label": `Comment on line ${at.line}`, text: "+", onclick: () => {
      if (row.nextElementSibling?.classList.contains("comment-editor")) return;
      const area = el("textarea", { class: "comment-input", "aria-label": "Your comment", placeholder: "What should change here, and why" });
      const editor = el("tr", { class: "comment-editor" }, el("td", { colspan: "4" }, el("div", { class: "comment edit" }, area,
        el("div", { class: "row" },
          el("button", { class: "btn small primary", text: "Add comment", onclick: () => {
            const text = area.value.trim();
            if (!text) { area.focus(); return; }
            const c = commenting.onAdd({ path: commenting.path, line: at.line, side: at.side, code: at.code, text });
            editor.replaceWith(commentRow(c, commenting.onRemove));
          } }),
          el("button", { class: "btn small", text: "Cancel", onclick: () => editor.remove() })))));
      row.after(editor);
      area.focus();
    } });
    row.firstElementChild.prepend(button);
    return [row, ...mine.map((c) => commentRow(c, commenting.onRemove))];
  };
  let newEnd = 0, delta = 0;
  for (const h of file.hunks) {
    gap(newEnd + 1, h.newStart - 1, h.oldStart - h.newStart);
    table.append(el("tr", { class: "hunk" }, el("td", { class: "ln" }), el("td", { class: "ln" }), el("td", { class: "sign" }), el("td", { text: `${h.header}` })));
    let o = h.oldStart, n = h.newStart;
    for (const l of h.lines) {
      const cls = l.type === "+" ? "add" : l.type === "-" ? "del" : "ctx";
      const at = l.type === "-" ? { line: o, side: "old", code: l.text } : { line: n, side: "new", code: l.text };
      table.append(...[withComments(lineRow(cls, l.type === "+" ? null : o, l.type === "-" ? null : n, l.type === " " ? "" : l.type, l.text, l.noNewline), at)].flat());
      if (l.type !== "+") o++;
      if (l.type !== "-") n++;
    }
    newEnd = h.newStart + h.newLines - 1;
    delta = (h.oldStart + h.oldLines) - (h.newStart + h.newLines);
  }
  if (whole) gap(newEnd + 1, whole.length, delta);
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
/** A file whose diff has more lines than this opens closed, and is drawn when opened. */
const BIG_FILE_LINES = 600;

export function renderDiff(patch, { files = [], editable = false, onEdit = null, editing = Object.create(null), commenting = null } = {}) {
  const root = el("div", { class: "diff" });
  const meta = new Map(files.map((f) => [f.path, f]));
  const parsed = parsePatch(patch);
  for (const file of parsed) {
    const stats = fileStats(file);
    const info = meta.get(file.path);
    const editingThis = Object.prototype.hasOwnProperty.call(editing, file.path);
    const canEdit = editable && contentsAgree(info, file);
    const lines = file.hunks.reduce((n, h) => n + h.lines.length, 0);
    const big = !file.binary && !editingThis && lines > BIG_FILE_LINES;
    const summary = el("summary", {},
      el("span", { class: "path", text: file.path }),
      big ? el("span", { class: "badge", title: "Open it to read it", text: `${lines} lines` }) : null,
      file.status !== "modified" ? el("span", { class: `badge ${file.status === "added" ? "ok" : file.status === "deleted" ? "bad" : ""}`, text: file.status }) : null,
      modeBadge(file),
      el("span", { class: "stats" }, el("span", { class: "add", text: `+${stats.added}` }), el("span", { class: "del", text: `-${stats.removed}` })),
      canEdit ? el("button", { class: "btn small edit", text: editingThis ? "Editing" : "Edit", onclick: (e) => { e.preventDefault(); e.stopPropagation(); onEdit?.(file.path); } })
        : editable && !file.binary && file.status !== "deleted" ? el("span", { class: "small muted", text: "not editable here" }) : null);
    const details = el("details", { class: "diff-file card", open: !big, id: `file-${file.path}` }, summary);
    if (editingThis) {
      const area = el("textarea", { class: "code", "aria-label": `Content of ${file.path} after your edit`, spellcheck: "false" });
      area.value = editing[file.path];
      area.addEventListener("input", () => { editing[file.path] = area.value; });
      details.append(el("div", { class: "editor" }, el("div", { class: "editor-bar" }, "The whole file as it should be after the change. The patch is written again from it, and you see the result before it is sent."), area));
    } else if (file.binary) {
      details.append(el("div", { class: "diff-binary", text: "A binary file: the desk cannot show its content. Approve it only if you know what it is." }));
    } else {
      // The code around the change opens only where the file's own text
      // agrees with its patch.
      const opts = { after: contentsAgree(info, file) ? info.after : null, commenting: commenting ? { ...commenting, path: file.path } : null };
      if (big) {
        // Drawn when opened, so a large change does not slow the page.
        details.addEventListener("toggle", () => { if (details.open && !details.querySelector(".diff-table")) details.append(diffTable(file, opts)); });
      } else {
        details.append(diffTable(file, opts));
      }
    }
    root.append(details);
  }
  return root;
}

/** What the desk cannot show faithfully of a code change: the patch's own anomalies, and a file listed twice. */
export function codeAnomalies(artefact) {
  const anomalies = [...parsePatch(artefact.patch).anomalies];
  const seen = new Set();
  for (const f of artefact.files ?? []) { if (seen.has(f.path)) anomalies.push(`${f.path} is listed twice`); seen.add(f.path); }
  return anomalies;
}

/** What the desk cannot show faithfully of a branch: each commit's patch anomalies, and a file listed twice in one commit. */
export function rangeAnomalies(artefact) {
  const out = [];
  for (const c of artefact.commits ?? []) {
    const sha = String(c.sha ?? "").slice(0, 7);
    if (typeof c.patch === "string") for (const a of parsePatch(c.patch).anomalies) out.push(`${sha}: ${a}`);
    const seen = new Set();
    for (const f of c.files ?? []) { if (seen.has(f.path)) out.push(`${sha}: ${f.path} is listed twice`); seen.add(f.path); }
  }
  return out;
}

/** What the desk cannot show faithfully of an artefact, by its shape. */
export function artefactAnomalies(artefact) {
  const shape = shapeOf(artefact);
  return shape === "code" ? codeAnomalies(artefact) : shape === "commits" ? rangeAnomalies(artefact) : [];
}

/** The banner for a patch the desk cannot show faithfully, or null. */
export function anomalyBanner(artefact) {
  const anomalies = codeAnomalies(artefact);
  if (!anomalies.length) return null;
  return el("div", { class: "alert" }, el("b", { text: "Do not approve this patch. " }), "The desk cannot show it as git would apply it: ", anomalies.join("; "), ".");
}

/** A warning for a patch with invisible characters in its changed lines, or null. */
function invisibleBanner(patch) {
  const files = parsePatch(patch).filter((f) => f.hunks.some((h) => h.lines.some((l) => l.type !== " " && hasInvisible(l.text))));
  if (!files.length) return null;
  return el("div", { class: "alert warn" }, el("b", { text: "Invisible characters. " }), `The changed lines of ${files.map((f) => f.path).join(", ")} hold characters a reader cannot see, shown as markers. Read those lines as the code will.`);
}

// -- shapes -------------------------------------------------------------------

const LONG = (s) => s.length > 80 || s.includes("\n");

const sumOf = (files, key) => (files ?? []).reduce((n, f) => n + (typeof f[key] === "number" ? f[key] : 0), 0);

/** A raw git date, "1700000000 +0100", as "2023-11-14 22:13 +0100". */
export function gitDate(raw) {
  const m = /^(\d+) ([+-])(\d{2})(\d{2})$/.exec(raw ?? "");
  if (!m) return raw ?? "";
  const offset = (m[2] === "-" ? -1 : 1) * (Number(m[3]) * 60 + Number(m[4])) * 60_000;
  return `${new Date(Number(m[1]) * 1000 + offset).toISOString().slice(0, 16).replace("T", " ")} ${m[2]}${m[3]}${m[4]}`;
}

/**
 * A branch: what it is, its commits as a list to read in order, and the one
 * selected with its message, author, files and diff. `index` is the commit
 * shown, `read` the commits opened so far, `onSelect(i)` opens another.
 */
function renderCommits(artefact, { index = 0, read = new Set(), onSelect = null, commenting = null } = {}) {
  const commits = artefact.commits;
  const at = Math.min(Math.max(0, index), commits.length - 1);
  const head = el("div", { class: "stack", style: "margin-bottom:12px" });
  const anomalies = rangeAnomalies(artefact);
  if (anomalies.length) head.append(el("div", { class: "alert" }, el("b", { text: "Do not approve this branch. " }), "The desk cannot show every commit as git would apply it: ", anomalies.join("; "), "."));
  if (artefact.summary) head.append(el("div", {}, el("b", { text: artefact.summary })));
  const added = commits.reduce((n, c) => n + sumOf(c.files, "added"), 0);
  const removed = commits.reduce((n, c) => n + sumOf(c.files, "removed"), 0);
  const facts = [["branch", artefact.branch], ["onto", artefact.base ? String(artefact.base).slice(0, 12) : "an empty repository"], ["commits", String(commits.length)], ["written by", artefact.model], ["drafted by", artefact.drafted_by], ["repository", artefact.repo]];
  head.append(el("div", { class: "row small muted" },
    facts.filter(([, v]) => v).map(([k, v]) => el("span", {}, `${k} `, el("b", { class: k === "onto" ? "mono" : "", text: v }))),
    el("span", { class: "stats mono" }, el("span", { class: "add", text: `+${added}` }), " ", el("span", { class: "del", text: `-${removed}` }))));

  const list = el("ol", { class: "commits", "aria-label": "Commits, oldest first" });
  commits.forEach((c, i) => {
    const isRead = read.has(c.sha);
    list.append(el("li", {}, el("button", {
      class: `commit-row${i === at ? " selected" : ""}${isRead ? " read" : ""}`, title: `${c.sha}\n${c.message}`, onclick: () => onSelect?.(i), "aria-current": i === at ? "true" : null,
    },
    el("span", { class: "n", text: String(i + 1) }),
    el("span", { class: "sha mono", text: String(c.sha).slice(0, 7) }),
    el("span", { class: "subject", text: c.message.split("\n")[0] || "(no message)" }),
    el("span", { class: "stats mono" }, el("span", { class: "add", text: `+${sumOf(c.files, "added")}` }), el("span", { class: "del", text: `-${sumOf(c.files, "removed")}` })),
    el("span", { class: "mark", "aria-label": isRead ? "read" : "not read yet", text: isRead ? "✓" : "" }))));
  });
  const readCount = commits.filter((c) => read.has(c.sha)).length;
  const nav = el("div", { class: "commit-nav" },
    el("span", { class: `small ${readCount === commits.length ? "ok-text" : "muted"}`, text: readCount === commits.length ? `All ${commits.length} commits read` : `${readCount} of ${commits.length} commits read` }),
    el("span", { class: "spacer" }),
    el("button", { class: "btn small", disabled: at === 0, onclick: () => onSelect?.(at - 1), title: "Previous commit (p)" }, "Previous ", el("kbd", { text: "p" })),
    el("button", { class: "btn small", disabled: at === commits.length - 1, onclick: () => onSelect?.(at + 1), title: "Next commit (n)" }, "Next ", el("kbd", { text: "n" })));

  const c = commits[at];
  const author = c.author ? `${c.author.name} <${c.author.email}>` : "unknown";
  const detail = el("div", { class: "commit-detail" },
    el("div", { class: "commit-head" },
      el("span", { class: "badge accent", text: `Commit ${at + 1} of ${commits.length}` }),
      el("span", { class: "mono small", title: c.sha, text: String(c.sha).slice(0, 12) }),
      el("span", { class: "small muted" }, "by ", el("b", { text: author }), c.author?.date ? `, ${gitDate(c.author.date)}` : "")),
    el("pre", { class: "commit-message", text: c.message || "(no message)" }));
  if (typeof c.patch === "string") {
    const banner = invisibleBanner(c.patch);
    if (banner) detail.append(banner);
    const chips = el("div", { class: "files" });
    for (const f of parsePatch(c.patch)) {
      const st = fileStats(f);
      chips.append(el("span", { class: "file-chip", onclick: () => document.getElementById(`file-${f.path}`)?.scrollIntoView({ block: "start", behavior: "smooth" }) }, f.path, el("span", { class: "add", text: f.binary ? "bin" : `+${st.added}` }), el("span", { class: "del", text: f.binary ? "" : `-${st.removed}` })));
    }
    // Comments on this commit's lines carry the commit, so the prompt can say which one.
    const onThis = commenting ? {
      comments: (commenting.comments ?? []).filter((x) => x.commit === c.sha),
      onAdd: (x) => commenting.onAdd({ ...x, commit: c.sha, commit_index: at + 1, commit_of: commits.length }),
      onRemove: commenting.onRemove,
    } : null;
    detail.append(chips, c.patch.trim() ? renderDiff(c.patch, { files: c.files ?? [], commenting: onThis }) : el("div", { class: "muted small", text: "This commit changes no file." }));
  } else {
    detail.append(el("div", { class: "muted small", text: "Loading this commit's change." }));
  }
  return el("div", { class: "branch" }, head, list, nav, detail);
}

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
export function renderArtefact(artefact, { editing = null, range = null, commenting = null } = {}) {
  const shape = shapeOf(artefact);
  if (shape === "commits") return renderCommits(artefact, { ...(range ?? {}), commenting });
  if (shape === "code") {
    const head = el("div", { class: "stack", style: "margin-bottom:12px" });
    const banner = anomalyBanner(artefact);
    if (banner) head.append(banner);
    const hidden = invisibleBanner(artefact.patch);
    if (hidden) head.append(hidden);
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
    return el("div", {}, head, renderDiff(artefact.patch, { files: artefact.files ?? [], commenting }));
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
