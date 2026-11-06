// @brightbeamai/chap-code-gate: the CHAP code gate. A change a coding agent
// makes is proposed as a patch, reviewed as a diff in the desk, and
// committed only once a person approves it, with the evidence beside the
// commit and a verifier for CI. The library is under the package's subpaths
// (gate, git, range, verify and the rest); this module carries the commands.

export { COMMANDS, findGateDir, run } from "./lib/cli.mjs";
export { entry, hookEntry } from "./lib/entry.mjs";
export { hook } from "./lib/hook.mjs";
export { installClaudeHook, installHooks } from "./lib/install-hooks.mjs";
export { createDemoRepo } from "./lib/demo-repo.mjs";
export { main as claudeSession } from "./lib/claude-session.mjs";
