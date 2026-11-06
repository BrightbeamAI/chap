// Propose the working tree's change for review: `node propose.mjs "what it does" [options]`.
//
// The command is chap-code-gate propose, run for the gate project this file is
// in, so it can be named by path from the repository an agent works in.

import { entry } from "@brightbeamai/chap-code-gate";

await entry("propose", import.meta.url);
