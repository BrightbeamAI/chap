// The git hooks in hooks/ run this file: `node gate-hook.mjs <pre-commit|commit-msg|post-commit|pre-push>`.
//
// It runs chap-code-gate's hook of that name for the gate project this file
// is in.

import { hookEntry } from "@brightbeamai/chap-code-gate";

await hookEntry(import.meta.url);
