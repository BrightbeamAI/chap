// Send the evidence notes with the commits: `node push-notes.mjs [--repo <path>] [--remote origin]`.
//
// The command is chap-code-gate push-notes, run for the gate project this file is
// in, so it can be named by path from the repository an agent works in.

import { entry } from "@brightbeamai/chap-code-gate";

await entry("push-notes", import.meta.url);
