// The desk, POST /chap and /mcp for this project: `npm start`.
//
// The server is @brightbeamai/chap-server, configured by chap.config.json
// beside this file (or the file CHAP_CONFIG names). Its README lists the
// environment variables that override the configuration.

import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { serve } from "@brightbeamai/chap-server";

await serve({ dir: dirname(fileURLToPath(import.meta.url)) });
