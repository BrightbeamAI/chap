#!/usr/bin/env node
// Record what each released create-chap-app wrote into a project:
// `node scripts/released-copies.mjs [--check]`, from a clone with the
// release tags.
//
// For each tag vX.Y.Z whose packages/create-chap-app/templates exists, and
// each template in it, this hashes every file the generator copied, under
// the path it has in a project: the template's own files, with gitignore,
// dockerignore and github/ renamed as the generator renames them, and the
// shared files its template.json names. A file holding a placeholder,
// which differs from project to project, is left out, and so is
// template.json. The scripts of the template's package.json are recorded
// as their commands. lib/released.json holds the result, which upgrade
// reads. Run it after tagging a release; --check fails when the file and
// the tags disagree.

import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dotted } from "../index.mjs";
import { PLACEHOLDER, byVersion, contentHash } from "../lib/released.mjs";

const OUT = fileURLToPath(new URL("../lib/released.json", import.meta.url));
const ROOT = "packages/create-chap-app/templates";
const repo = fileURLToPath(new URL("../../../", import.meta.url));
const git = (args, encoding = "utf8") => execFileSync("git", args, { cwd: repo, encoding, maxBuffer: 64 * 1024 * 1024 });

function filesAt(tag, dir) {
  const out = git(["ls-tree", "-r", "-z", "--name-only", tag, "--", `${dir}/`]);
  return out.split("\0").filter(Boolean).map((p) => p.slice(dir.length + 1));
}

export function releasedCopies() {
  const tags = git(["tag", "-l", "v*"]).split("\n").filter((t) => /^v\d+\.\d+\.\d+$/.test(t));
  const result = { versions: [], templates: {} };
  for (const tag of tags.sort((a, b) => byVersion(a.slice(1), b.slice(1)))) {
    const all = filesAt(tag, ROOT);
    const names = [...new Set(all.filter((p) => /^[^_/][^/]*\/template\.json$/.test(p)).map((p) => p.split("/")[0]))];
    if (!names.length) continue;
    const version = tag.slice(1);
    result.versions.push(version);
    for (const name of names) {
      const manifest = JSON.parse(git(["show", `${tag}:${ROOT}/${name}/template.json`]));
      const t = (result.templates[name] ??= { versions: [], files: {}, scripts: {} });
      t.versions.push(version);
      const sources = all.filter((p) => p.startsWith(`${name}/`) && p !== `${name}/template.json`).map((p) => ({ from: p, to: p.slice(name.length + 1) }));
      for (const [shared, dest] of Object.entries(manifest.shared ?? {})) {
        const under = all.filter((p) => p.startsWith(`_shared/${shared}/`));
        if (under.length) for (const p of under) sources.push({ from: p, to: `${dest}/${p.slice(`_shared/${shared}/`.length)}` });
        else sources.push({ from: `_shared/${shared}`, to: dest });
      }
      for (const { from, to } of sources) {
        const bytes = git(["show", `${tag}:${ROOT}/${from}`], "buffer");
        if (PLACEHOLDER.test(bytes.toString("latin1"))) {
          if (to === "package.json") {
            for (const [script, command] of Object.entries(JSON.parse(bytes.toString("utf8")).scripts ?? {})) ((t.scripts[script] ??= {})[command] ??= []).push(version);
          }
          continue;
        }
        ((t.files[dotted(to)] ??= {})[contentHash(bytes)] ??= []).push(version);
      }
    }
  }
  return result;
}

const text = `${JSON.stringify(releasedCopies(), null, 1)}\n`;
if (process.argv.includes("--check")) {
  if (readFileSync(OUT, "utf8") !== text) {
    console.error("lib/released.json does not match the release tags: run node scripts/released-copies.mjs");
    process.exit(1);
  }
  console.log("lib/released.json matches the release tags");
} else {
  writeFileSync(OUT, text);
  console.log(`Wrote ${OUT}`);
}
