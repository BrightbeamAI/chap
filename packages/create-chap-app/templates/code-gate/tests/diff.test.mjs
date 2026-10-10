// The desk's diff code against git itself: a patch the desk writes after a
// reviewer edits a file whole must apply with git apply, and the desk must
// read the patches git writes the way git applies them.
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtemp, writeFile, mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import { parsePatch, diffLines, unifiedDiff, applyFile, rewritePatch, fileStats, splitLines } from "../desk/diff.js";
const run = promisify(execFile);
const git = (cwd, args, input) => { const p = run("git", args, { cwd }); if (input !== undefined) p.child.stdin.end(input); return p.then((r) => r.stdout); };

async function repo() {
  const dir = await mkdtemp(join(tmpdir(), "d-"));
  await git(dir, ["init", "-q"]); await git(dir, ["config", "user.email", "t@e"]); await git(dir, ["config", "user.name", "t"]);
  return dir;
}
async function roundTrip(before, after, path = "f.txt") {
  const dir = await repo();
  await mkdir(join(dir, "sub"), { recursive: true });
  if (before !== null) { await writeFile(join(dir, path), before); await git(dir, ["add", "-A"]); await git(dir, ["commit", "-qm", "b"]); }
  else { await writeFile(join(dir, "other"), "x\n"); await git(dir, ["add", "-A"]); await git(dir, ["commit", "-qm", "b"]); }
  const patch = unifiedDiff(path, before, after);
  if (patch) await git(dir, ["apply", "--check", "-"], patch);
  if (patch) await git(dir, ["apply", "-"], patch);
  const { readFile, access } = await import("node:fs/promises");
  if (after === null) { await assert.rejects(access(join(dir, path))); return patch; }
  assert.equal(await readFile(join(dir, path), "utf8"), after, `round trip for ${JSON.stringify(before)} -> ${JSON.stringify(after)}`);
  return patch;
}

test("diffLines finds a minimal edit", () => {
  const e = diffLines(["a", "b", "c", "d"], ["a", "x", "c", "d", "e"]);
  assert.deepEqual(e.map((x) => x.type + x.text), [" a", "-b", "+x", " c", " d", "+e"]);
  assert.deepEqual(diffLines([], ["a"]), [{ type: "+", text: "a" }]);
  assert.deepEqual(diffLines(["a"], []), [{ type: "-", text: "a" }]);
  assert.deepEqual(diffLines(["a", "b"], ["a", "b"]).map((x) => x.type), [" ", " "]);
});

test("unifiedDiff round-trips through git apply", async () => {
  const ten = Array.from({ length: 10 }, (_, i) => `line ${i}`).join("\n") + "\n";
  await roundTrip(ten, ten.replace("line 4", "LINE 4"));
  await roundTrip(ten, ten.replace("line 0", "zero").replace("line 9\n", "nine\n"));
  await roundTrip(ten, ten + "more\n");
  await roundTrip(ten, "first\n" + ten);
  await roundTrip(ten, ten.replace("line 2\nline 3\n", ""));
  await roundTrip("a\nb\n", "a\nb");          // newline removed at the end
  await roundTrip("a\nb", "a\nb\n");          // newline added at the end
  await roundTrip("a\nb", "a\nc");            // no newline either side
  await roundTrip(null, "new\nfile\n");       // added
  await roundTrip("gone\n", null);            // deleted
  await roundTrip("", "x\n");                 // empty before
  await roundTrip("x\n", "");                 // emptied
  await roundTrip(ten, ten.replace("line 1", "one").replace("line 8", "eight"));  // two hunks
});

test("parsePatch reads what git writes, applyFile applies it, and rewritePatch replaces a section", async () => {
  const dir = await repo();
  await writeFile(join(dir, "a.txt"), "one\ntwo\nthree\n"); await writeFile(join(dir, "b.txt"), "keep\n");
  await git(dir, ["add", "-A"]); await git(dir, ["commit", "-qm", "base"]);
  await writeFile(join(dir, "a.txt"), "one\n2\nthree\nfour\n"); await writeFile(join(dir, "c.txt"), "new\n"); 
  await git(dir, ["add", "-A"]);
  const patch = await git(dir, ["diff", "--cached", "--no-renames", "--binary"]);
  const files = parsePatch(patch);
  assert.deepEqual(files.map((f) => [f.path, f.status]), [["a.txt", "modified"], ["c.txt", "added"]]);
  assert.deepEqual(fileStats(files[0]), { added: 2, removed: 1 });
  assert.equal(applyFile("one\ntwo\nthree\n", files[0]), "one\n2\nthree\nfour\n");
  assert.equal(applyFile(null, files[1]), "new\n");
  assert.equal(files.map((f) => f.text).join(""), patch, "sections join back to the patch");
  // Edit a.txt's result and rewrite its section; the new patch applies to the base.
  const edited = rewritePatch(patch, { "a.txt": "one\nTWO\nthree\nfour\n" }, { "a.txt": "one\ntwo\nthree\n" });
  await git(dir, ["reset", "-q", "--hard"]);
  await git(dir, ["apply", "--check", "-"], edited);
  await git(dir, ["apply", "-"], edited);
  const { readFile } = await import("node:fs/promises");
  assert.equal(await readFile(join(dir, "a.txt"), "utf8"), "one\nTWO\nthree\nfour\n");
  assert.equal(await readFile(join(dir, "c.txt"), "utf8"), "new\n");
  assert.deepEqual(splitLines("a\nb\n"), { lines: ["a", "b"], newline: true });
});

test("applyFile follows git's own patches, newline marks included", async () => {
  const ten = Array.from({ length: 10 }, (_, i) => `line ${i}`).join("\n") + "\n";
  const cases = [[ten, ten.replace("line 4", "LINE 4")], ["a\nb\n", "a\nb"], ["a\nb", "a\nb\n"], ["a\nb", "a\nc"], ["", "x\n"], ["x\n", ""], [ten, ten.replace("line 1", "one").replace("line 8", "eight")], ["x", "x"]];
  const { readFile } = await import("node:fs/promises");
  for (const [before, after] of cases) {
    const dir = await repo();
    await writeFile(join(dir, "f.txt"), before); await git(dir, ["add", "-A"]); await git(dir, ["commit", "-qm", "b", "--allow-empty"]);
    await writeFile(join(dir, "f.txt"), after); await git(dir, ["add", "-A"]);
    const patch = await git(dir, ["diff", "--cached", "--no-renames"]);
    const files = parsePatch(patch);
    const result = files.length ? applyFile(before, files[0]) : before;
    assert.equal(result, after, `applyFile for ${JSON.stringify(before)} -> ${JSON.stringify(after)}:\n${patch}`);
  }
});
