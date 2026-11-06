// A gate project for the package's tests: chap.config.json in a directory of
// its own, with signatures required, as the code gate template ships it.
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

export const HUMAN = "human:reviewer@example.org";
export const AGENT = "agent:coder";

export async function gateProject(extra = {}) {
  const dir = await mkdtemp(join(tmpdir(), "chap-gate-"));
  const config = {
    workspace: "wsp_gate",
    profiles: ["core/1.0", "review/1.0", "security-signed/1.0", "audit-scitt/1.0"],
    store: ":memory:",
    port: 0,
    host: "127.0.0.1",
    humans: [{ uri: HUMAN, display_name: "Rev Iewer", email: "rev@example.org", role: "reviewer" }],
    agent: { uri: AGENT, display_name: "The coding agent", role: "drafter" },
    require_signatures: true,
    chain: true,
    ...extra,
  };
  await writeFile(join(dir, "chap.config.json"), JSON.stringify(config, null, 2));
  return { dir, configPath: join(dir, "chap.config.json"), config };
}
