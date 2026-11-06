// Whether a module is the script Node was asked to run. Node gives a
// module its real path, and gives process.argv[1] as typed, so the two are
// compared as real paths: a script reached through a symbolic link, a
// junction or a differently cased drive letter is still the script.

import { realpathSync } from "node:fs";
import { fileURLToPath } from "node:url";

/** Whether the module at `importMetaUrl` is the script this process runs. */
export function isMain(importMetaUrl) {
  if (!process.argv[1]) return false;
  try { return realpathSync(fileURLToPath(importMetaUrl)) === realpathSync(process.argv[1]); } catch { return false; }
}
