// Unified diffs in the browser: parse a patch git made into files and hunks,
// compute a line diff between two texts, and write a file's section of a
// patch again after an edit, in the form git apply accepts. No dependencies,
// so the same file runs under node --test.

/** Split text into lines, keeping line endings out and noting a missing final newline. */
export function splitLines(text) {
  if (text === "") return { lines: [], newline: true };
  const lines = text.split("\n");
  const newline = lines.at(-1) === "";
  if (newline) lines.pop();
  return { lines, newline };
}

/**
 * Parse a patch into files: [{ path, oldPath, newPath, status, binary, mode,
 * headerLines, hunks: [{ oldStart, oldLines, newStart, newLines, header, lines: [{ type, text, noNewline }] }], text }].
 * `type` is " ", "+" or "-". `text` is the whole section as it was.
 */
export function parsePatch(patch) {
  const files = [];
  const lines = patch.split("\n");
  if (lines.at(-1) === "") lines.pop();
  let i = 0;
  while (i < lines.length) {
    if (!lines[i].startsWith("diff --git ")) { i++; continue; }
    const start = i;
    const m = lines[i].match(/^diff --git a\/(.*) b\/(.*)$/);
    const file = { path: m ? m[2] : lines[i].slice(11), oldPath: m ? m[1] : null, newPath: m ? m[2] : null, status: "modified", binary: false, mode: null, headerLines: [lines[i]], hunks: [] };
    i++;
    while (i < lines.length && !lines[i].startsWith("@@") && !lines[i].startsWith("diff --git ")) {
      const line = lines[i];
      file.headerLines.push(line);
      if (line.startsWith("new file mode ")) { file.status = "added"; file.mode = line.slice(14); }
      else if (line.startsWith("deleted file mode ")) { file.status = "deleted"; file.mode = line.slice(18); }
      else if (line.startsWith("rename from ")) { file.status = "renamed"; file.oldPath = line.slice(12); }
      else if (line.startsWith("rename to ")) { file.newPath = line.slice(10); file.path = file.newPath; }
      else if (line.startsWith("new mode ")) file.mode = line.slice(9);
      else if (line.startsWith("Binary files ") || line.startsWith("GIT binary patch")) file.binary = true;
      i++;
    }
    while (i < lines.length && lines[i].startsWith("@@")) {
      const h = lines[i].match(/^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$/);
      const hunk = { header: lines[i], oldStart: Number(h[1]), oldLines: h[2] === undefined ? 1 : Number(h[2]), newStart: Number(h[3]), newLines: h[4] === undefined ? 1 : Number(h[4]), section: h[5].trim(), lines: [] };
      i++;
      while (i < lines.length && !lines[i].startsWith("@@") && !lines[i].startsWith("diff --git ")) {
        const line = lines[i];
        if (line.startsWith("\\")) { if (hunk.lines.length) hunk.lines.at(-1).noNewline = true; }
        else if (line === "" ) { hunk.lines.push({ type: " ", text: "" }); }
        else hunk.lines.push({ type: line[0], text: line.slice(1) });
        i++;
      }
      file.hunks.push(hunk);
    }
    file.text = lines.slice(start, i).join("\n") + "\n";
    files.push(file);
  }
  return files;
}

/** Lines added and removed in a parsed file. */
export function fileStats(file) {
  let added = 0, removed = 0;
  for (const h of file.hunks) for (const l of h.lines) { if (l.type === "+") added++; else if (l.type === "-") removed++; }
  return { added, removed };
}

/**
 * The content of a file after its hunks, from its content before. Null when
 * a hunk does not fit. The final newline follows the last line: the mark
 * on a hunk line, or the content before for a line copied from it.
 */
export function applyFile(before, file) {
  if (file.status === "deleted") return null;
  const src = splitLines(before ?? "");
  const out = [];       // [text, hasNewline]
  let pos = 0;
  const copy = (upto) => { for (; pos < upto; pos++) out.push([src.lines[pos], pos < src.lines.length - 1 || src.newline]); };
  for (const h of file.hunks) {
    const at = Math.max(0, h.oldStart - 1);
    if (at < pos) return null;
    copy(at);
    for (const l of h.lines) {
      if (l.type === " " || l.type === "-") { if (src.lines[pos] !== l.text) return null; pos++; }
      if (l.type === " " || l.type === "+") out.push([l.text, !l.noNewline]);
    }
  }
  copy(src.lines.length);
  if (!out.length) return "";
  return out.map(([t]) => t).join("\n") + (out.at(-1)[1] ? "\n" : "");
}

/**
 * A line diff by Myers' algorithm: [{ type: " " | "-" | "+", text }], in order.
 */
export function diffLines(a, b) {
  const n = a.length, m = b.length;
  if (n === 0 && m === 0) return [];
  const max = n + m;
  const offset = max;
  let v = new Int32Array(2 * max + 2);
  const trace = [];
  let found = false;
  for (let d = 0; d <= max && !found; d++) {
    trace.push(v);
    const next = new Int32Array(v);
    for (let k = -d; k <= d; k += 2) {
      let x;
      if (k === -d || (k !== d && v[k - 1 + offset] < v[k + 1 + offset])) x = v[k + 1 + offset];
      else x = v[k - 1 + offset] + 1;
      let y = x - k;
      while (x < n && y < m && a[x] === b[y]) { x++; y++; }
      next[k + offset] = x;
      if (x >= n && y >= m) { found = true; break; }
    }
    v = next;
  }
  // Walk the trace back from the end to the start, collecting edits.
  const edits = [];
  let x = n, y = m;
  for (let d = trace.length - 1; d >= 0; d--) {
    const vd = trace[d];
    const k = x - y;
    let prevK;
    if (k === -d || (k !== d && vd[k - 1 + offset] < vd[k + 1 + offset])) prevK = k + 1;
    else prevK = k - 1;
    const prevX = vd[prevK + offset];
    const prevY = prevX - prevK;
    while (x > prevX && y > prevY) { edits.push({ type: " ", text: a[x - 1] }); x--; y--; }
    if (d > 0) {
      if (x === prevX) { edits.push({ type: "+", text: b[y - 1] }); y--; }
      else { edits.push({ type: "-", text: a[x - 1] }); x--; }
    }
  }
  while (x > 0 && y > 0) { edits.push({ type: " ", text: a[x - 1] }); x--; y--; }
  return edits.reverse();
}

/**
 * A file's section of a unified diff from its content before and after,
 * with `context` lines around each change, in the form git apply accepts.
 * `status` is "modified", "added" or "deleted"; `mode` is the file mode for
 * an added or deleted file (default 100644). Null content means absent.
 * A last line without a newline differs from the same line with one, as it
 * does for git, and carries the "No newline at end of file" mark.
 */
export function unifiedDiff(path, before, after, { context = 3, status = null, mode = "100644" } = {}) {
  status ??= before === null ? "added" : after === null ? "deleted" : "modified";
  const NONL = "\u0000";
  const tokens = (text) => { const { lines, newline } = splitLines(text ?? ""); if (!newline && lines.length) lines[lines.length - 1] += NONL; return lines; };
  const A = tokens(before), B = tokens(after);
  const edits = diffLines(A, B);
  const header = [`diff --git a/${path} b/${path}`];
  if (status === "added") header.push(`new file mode ${mode}`, "--- /dev/null", `+++ b/${path}`);
  else if (status === "deleted") header.push(`deleted file mode ${mode}`, `--- a/${path}`, "+++ /dev/null");
  else header.push(`--- a/${path}`, `+++ b/${path}`);
  const changed = edits.map((e, i) => (e.type !== " " ? i : -1)).filter((i) => i >= 0);
  if (!changed.length) return status === "modified" ? "" : header.join("\n") + "\n";
  // Hunks: each change with its context, merged where the context overlaps.
  const ranges = [];
  for (const i of changed) {
    const lo = Math.max(0, i - context), hi = Math.min(edits.length - 1, i + context);
    const last = ranges.at(-1);
    if (last && lo <= last.hi + 1) last.hi = Math.max(last.hi, hi); else ranges.push({ lo, hi });
  }
  const out = [...header];
  let oldPos = 0, newPos = 0, at = 0;
  for (const r of ranges) {
    while (at < r.lo) { const e = edits[at++]; if (e.type !== "+") oldPos++; if (e.type !== "-") newPos++; }
    const oldStart = oldPos + 1, newStart = newPos + 1;
    const body = [];
    let oldLen = 0, newLen = 0;
    for (let i = r.lo; i <= r.hi; i++) {
      const e = edits[i];
      const bare = e.text.endsWith(NONL);
      body.push(`${e.type}${bare ? e.text.slice(0, -1) : e.text}`);
      if (bare) body.push("\\ No newline at end of file");
      if (e.type !== "+") { oldLen++; oldPos++; }
      if (e.type !== "-") { newLen++; newPos++; }
    }
    at = r.hi + 1;
    const range = (start, len) => (len === 1 ? `${start}` : `${len === 0 ? start - 1 : start},${len}`);
    out.push(`@@ -${range(oldStart, oldLen)} +${range(newStart, newLen)} @@`, ...body);
  }
  return out.join("\n") + "\n";
}

/**
 * The patch with the sections of the files in `edits` written again from
 * their new content: edits maps a path to its content after the edit (null
 * to delete the file). `contents` maps a path to its content before the
 * change. Other sections are kept as they were.
 */
export function rewritePatch(patch, edits, contents) {
  const files = parsePatch(patch);
  const sections = files.map((f) => {
    if (!(f.path in edits)) return f.text;
    const before = contents[f.path] ?? null;
    const after = edits[f.path];
    const status = before === null ? "added" : after === null ? "deleted" : "modified";
    return unifiedDiff(f.path, before, after, { status, mode: f.mode ?? "100644" });
  });
  return sections.join("");
}
