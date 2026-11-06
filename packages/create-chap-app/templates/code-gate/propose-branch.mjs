// Review a branch before it is pushed: `node propose-branch.mjs [<base>..]<head> [options]`.
//
// The command is chap-code-gate propose-branch, run for the gate project this file is
// in, so it can be named by path from the repository an agent works in.

import { entry } from "@brightbeamai/chap-code-gate";

await entry("propose-branch", import.meta.url);
