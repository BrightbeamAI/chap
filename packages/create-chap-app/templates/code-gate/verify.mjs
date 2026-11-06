// Verify that commits were approved through the gate: `node verify.mjs [range] [options]`.
//
// The command is chap-code-gate verify, run for the gate project this file is
// in, so it can be named by path from the repository an agent works in.

import { entry } from "@brightbeamai/chap-code-gate";

await entry("verify", import.meta.url);
