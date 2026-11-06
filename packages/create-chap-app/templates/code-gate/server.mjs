// The gate: the desk, POST /chap and /mcp for this project: `npm start`.
//
// The server is @brightbeamai/chap-server, configured by chap.config.json
// beside this file (or the file CHAP_CONFIG names). The reviewers and agents
// the project's trust policy pins are joined with their keys first.

import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { serveGate } from "@brightbeamai/chap-code-gate";

await serveGate({ dir: dirname(fileURLToPath(import.meta.url)) });
