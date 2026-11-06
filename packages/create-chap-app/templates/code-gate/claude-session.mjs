// A Claude Code SessionStart hook: it hands the session's model to the
// commands Claude Code runs, as CHAP_MODEL. `node install-hooks.mjs --repo
// <path> --claude` registers this file for a repository.

import { claudeSession } from "@brightbeamai/chap-code-gate";

await claudeSession();
