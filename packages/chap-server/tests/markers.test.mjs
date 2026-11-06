// What the desk marks. A character a reader cannot see, or one that changes
// how the text around it reads, is shown as a marker naming its code point,
// wherever the agent's text is shown. A character outside ASCII that is not
// on the allow-list is outlined in file paths, branch names and changed
// lines, where it can pass for another (a Cyrillic а for a Latin a). The
// desk's own modules render into jsdom.
import { test, before } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

let renderArtefact, renderMarkdown;

before(async () => {
  const dom = new JSDOM("<!doctype html><body></body>", { url: "http://127.0.0.1/" });
  Object.assign(globalThis, { window: dom.window, document: dom.window.document, Node: dom.window.Node });
  ({ renderArtefact } = await import("../desk/render.js"));
  ({ renderMarkdown } = await import("../desk/insights.js"));
});

const markers = (view) => [...view.querySelectorAll(".invisible")].map((m) => m.textContent);
const outlined = (view) => [...view.querySelectorAll(".outside")].map((m) => m.textContent);
const banners = (view) => [...view.querySelectorAll(".alert")].map((a) => a.textContent);

/** A one-file patch whose added line holds `ch` inside an identifier, the way a hidden argument is smuggled in. */
function patchWith(ch, path = "server.js") {
  return [`diff --git a/${path} b/${path}`, "index 1111111..2222222 100644", `--- a/${path}`, `+++ b/${path}`, "@@ -1,2 +1,2 @@",
    " const { exec } = require('child_process');", "-const { timeout } = req.query;", `+const { timeout,${ch}} = req.query; exec(['ping -c1 x',${ch}][1]);`, ""].join("\n");
}

const HIDDEN = {
  "U+3164 HANGUL FILLER": "ㅤ", "U+115F HANGUL CHOSEONG FILLER": "ᅟ", "U+1160 HANGUL JUNGSEONG FILLER": "ᅠ",
  "U+FFA0 HALFWIDTH HANGUL FILLER": "ﾠ", "U+00AD SOFT HYPHEN": "­", "U+180E MONGOLIAN VOWEL SEPARATOR": "᠎",
  "U+E0041 TAG LATIN CAPITAL LETTER A": "\u{E0041}", "U+FE0F VARIATION SELECTOR-16": "️", "U+034F COMBINING GRAPHEME JOINER": "͏",
  "U+2800 BRAILLE PATTERN BLANK": "⠀", "U+200B ZERO WIDTH SPACE": "​", "U+202E RIGHT-TO-LEFT OVERRIDE": "‮", "U+00A0 NO-BREAK SPACE": " ",
};

test("every character a reader cannot see in a changed line is marked, with a warning above the diff", () => {
  for (const [name, ch] of Object.entries(HIDDEN)) {
    const view = renderArtefact({ summary: "x", patch: patchWith(ch), files: [] });
    const label = `U+${ch.codePointAt(0).toString(16).toUpperCase().padStart(4, "0")}`;
    assert.deepEqual(markers(view), [`⟨${label}⟩`, `⟨${label}⟩`], name);
    assert.ok(banners(view).some((b) => /Invisible characters/.test(b)), name);
  }
});

test("a character outside ASCII in a changed line is outlined, unless the allow-list takes it", () => {
  const cyrillic = renderArtefact({ summary: "x", patch: patchWith("а"), files: [] });
  assert.deepEqual(outlined(cyrillic), ["а", "а"]);
  assert.ok(banners(cyrillic).some((b) => /outside ASCII/.test(b) && /server\.js/.test(b)));
  const latin = renderArtefact({ summary: "x", patch: patchWith("é"), files: [] });
  assert.deepEqual(outlined(latin), [], "a Latin-1 letter is on the allow-list");
  assert.deepEqual(banners(latin), []);
});

test("a file path is marked and outlined in the diff and on its chip", () => {
  const path = ".github/workflows/notes_‮dm.yml";
  const patch = [`diff --git a/${path} b/${path}`, "new file mode 100644", "index 0000000..3333333", "--- /dev/null", `+++ b/${path}`, "@@ -0,0 +1 @@", "+on: push", ""].join("\n");
  const view = renderArtefact({ summary: "docs: notes", patch, files: [] });
  assert.deepEqual([...view.querySelectorAll("summary .path .invisible")].map((m) => m.textContent), ["⟨U+202E⟩"]);
  assert.deepEqual([...view.querySelectorAll(".file-chip .invisible")].map((m) => m.textContent), ["⟨U+202E⟩"]);
  assert.ok(banners(view).some((b) => /Invisible characters/.test(b) && b.includes(".github/workflows")));
  const homoglyph = "lib/сalc.mjs";
  const cyr = renderArtefact({ summary: "x", patch: patchWith("a", homoglyph), files: [] });
  assert.deepEqual([...cyr.querySelectorAll("summary .path .outside")].map((m) => m.textContent), ["с"]);
});

test("a branch's name, its commits' messages and their files are marked too", () => {
  const view = renderArtefact({ summary: "a branch", branch: "agent/‮work", base: "0".repeat(40),
    commits: [{ sha: "a".repeat(40), message: "Fix it​ now", files: [{ path: "a.txt", added: 1, removed: 0 }], patch: patchWith("ㅤ", "a.txt") }] });
  assert.ok(markers(view).includes("⟨U+202E⟩"), "the branch name");
  assert.ok(markers(view).includes("⟨U+200B⟩"), "the commit message");
  assert.ok(markers(view).includes("⟨U+3164⟩"), "the commit's changed line");
});

test("a message, a text and a JSON artefact show their invisible characters as markers", () => {
  const message = renderArtefact({ to: "billing@exam​ple.com", subject: "Refund", body: "Refund of EUR ‮0001‬ approved." });
  assert.deepEqual(markers(message), ["⟨U+200B⟩", "⟨U+202E⟩", "⟨U+202C⟩"]);
  assert.ok(banners(message).some((b) => /Invisible characters/.test(b)));
  assert.deepEqual(markers(renderArtefact("Pay ‮0001‬ now")), ["⟨U+202E⟩", "⟨U+202C⟩"]);
  assert.deepEqual(markers(renderArtefact({ amount: "EUR ‮0001‬", n: 1 })), ["⟨U+202E⟩", "⟨U+202C⟩"]);
  assert.deepEqual(outlined(message), [], "prose outside ASCII is not outlined");
});

test("the agent's context note shows its invisible characters as markers", () => {
  const box = document.createElement("div");
  box.innerHTML = renderMarkdown("Tested with `npm test`‮ and all **passed**.");
  assert.deepEqual([...box.querySelectorAll(".invisible")].map((m) => m.textContent), ["⟨U+202E⟩"]);
});
