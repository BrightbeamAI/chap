// A project for the tests: a chap.config.json in a directory of its own, on
// an in-memory store.
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

// The enrolment code every server in these tests takes, and the desk the
// harness opens carries, as a server's printed link does.
process.env.CHAP_ENROLMENT_CODE ??= "TESTS-ENROL";

export const HUMAN = "human:reviewer@example.org";
export const AGENT = "agent:drafter";

/** Write a project's chap.config.json with `extra` merged in. Returns { dir, configPath }. */
export async function project(extra = {}) {
  const dir = await mkdtemp(join(tmpdir(), "chap-server-"));
  const configPath = join(dir, "chap.config.json");
  const config = {
    workspace: "wsp_test",
    profiles: ["core/1.0", "review/1.0"],
    store: ":memory:",
    port: 0,
    humans: [{ uri: HUMAN, display_name: "Rev Iewer", role: "reviewer" }],
    agent: { uri: AGENT, display_name: "The drafter" },
    mcp: false,
    require_signatures: false,
    chain: true,
    ...extra,
  };
  await writeFile(configPath, JSON.stringify(config, null, 2));
  return { dir, configPath };
}
