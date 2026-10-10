// A Claude Code SessionStart hook: it hands the session's model to the
// commands Claude Code runs, as CHAP_MODEL, by writing it to the file Claude
// Code reads before each command (CLAUDE_ENV_FILE). propose.mjs and
// propose-branch.mjs name that model in a commit's Drafted-by line where the
// commits do not name one themselves. `node install-hooks.mjs --claude`
// registers it for a repository.
//
// It never holds up a session: input it cannot read, a model id it does not
// recognise as one, or no CLAUDE_ENV_FILE, and it writes nothing. It prints
// nothing, since what a SessionStart hook prints joins the session's context.

import { appendFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

/** The export line for a model id, or null when the id is not one a shell line can carry as it is. */
export function exportLine(model) {
  const id = model && typeof model === "object" ? model.id : model;
  if (typeof id !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._:/@[\]-]{0,119}$/.test(id)) return null;
  return `export CHAP_MODEL='${id}'\n`;
}

export async function main({ stdin = process.stdin, env = process.env } = {}) {
  let input = "";
  try { for await (const chunk of stdin) input += chunk; } catch { return; }
  let model = null;
  try { model = JSON.parse(input)?.model ?? null; } catch { return; }
  const line = exportLine(model);
  if (!line || !env.CLAUDE_ENV_FILE) return;
  try { await appendFile(env.CLAUDE_ENV_FILE, line); } catch { /* the session carries on without it */ }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) await main();
