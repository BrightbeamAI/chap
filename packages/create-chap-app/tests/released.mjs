// Projects as a released create-chap-app wrote them, for the upgrade and
// add tests: the release's generator, from its tag in this clone, or from
// npm where the clone has no tags, run as a user ran it.
import { execFileSync } from "node:child_process";
import { mkdtemp, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const repo = fileURLToPath(new URL("../../../", import.meta.url));
const generators = new Map();

/** The directory holding create-chap-app's index.mjs as `version` released it. */
export async function releasedGenerator(version) {
  if (generators.has(version)) return generators.get(version);
  const dir = await mkdtemp(join(tmpdir(), `cca-${version}-`));
  let root;
  try {
    const tar = execFileSync("git", ["archive", `v${version}`, "packages/create-chap-app"], { cwd: repo, maxBuffer: 64 * 1024 * 1024, stdio: ["ignore", "pipe", "ignore"] });
    execFileSync("tar", ["-x", "-C", dir], { input: tar });
    root = join(dir, "packages", "create-chap-app");
  } catch {
    execFileSync("npm", ["pack", `create-chap-app@${version}`, "--pack-destination", dir, "--silent"], { cwd: dir, stdio: ["ignore", "pipe", "inherit"], shell: process.platform === "win32" });
    const tgz = (await readdir(dir)).find((f) => f.endsWith(".tgz"));
    execFileSync("tar", ["-x", "-z", "-f", tgz, "-C", dir], { cwd: dir });
    root = join(dir, "package");
  }
  generators.set(version, root);
  return root;
}

/** Generate `template` with the release `version`'s generator into `dir`. */
export async function releasedProject(version, template, dir, args = []) {
  await rm(dir, { recursive: true, force: true });
  execFileSync(process.execPath, [join(await releasedGenerator(version), "index.mjs"), `my-${template}`, "--template", template, "--yes", "--dir", dir, ...args], { stdio: "ignore" });
  return dir;
}

/** The releases that wrote templates, oldest first. */
export const RELEASES = ["0.3.1", "0.3.2", "0.3.3", "0.3.4"];
