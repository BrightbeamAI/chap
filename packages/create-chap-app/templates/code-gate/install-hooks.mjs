// Put a repository under the gate: `node install-hooks.mjs --repo <path>`.
//
// The command is chap-code-gate install-hooks, run for the gate project this file is
// in, so it can be named by path from the repository an agent works in.

import { entry } from "@brightbeamai/chap-code-gate";

await entry("install-hooks", import.meta.url);
