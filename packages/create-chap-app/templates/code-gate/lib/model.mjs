// Which model wrote an agent's work, as the agent's own harness records it.
// Claude Code ends each commit it makes with "Co-Authored-By: <model>
// <noreply@anthropic.com>", naming the model of that moment; another agent
// may end a commit with "Drafted-by: <model>"; a Claude Code SessionStart
// hook (claude-session.mjs) hands the session's model to the commands it
// runs as CHAP_MODEL. The agent's own word, --model, counts only where none
// of these says. Each is a record of what the harness or the agent said:
// the agent writes its commit messages and runs its own commands.

import { modelLabel } from "./gate.mjs";
import { AGENT_ATTRIBUTION } from "./git.mjs";
import { modelName } from "./providers.mjs";

export { AGENT_ATTRIBUTION };

/** A Drafted-by line in a commit not sealed yet: the agent's own, or kept from a sealed commit amended since. */
const DRAFTED_BY = /^drafted-by:\s*(.+?)\s*$/i;

/** Whether a name names a model and its version: "Claude" alone, "Claude Code" and a URI such as agent:x name none. */
const namesModel = (name) => /\d/.test(name) && !/^claude(?: code)?$/i.test(name) && !/^[a-z][a-z0-9+.-]*:\S+$/i.test(name);

/** A model's name as a commit carries it, from an id or a name; null when it cannot be one. */
export function modelOf(value) {
  if (typeof value !== "string" || !value.trim()) return null;
  try { return modelLabel(modelName(value.trim())); } catch { return null; }
}

/**
 * The model a commit names itself, or null: Claude Code's Co-Authored-By
 * line first, since it names the model of the moment the commit was made,
 * then a Drafted-by line. "Claude" alone and "Claude Code" (what Claude
 * Code writes when it cannot tell the model or its version) name none.
 */
export function commitModel(message) {
  const lines = String(message ?? "").split("\n");
  for (const pattern of [AGENT_ATTRIBUTION, DRAFTED_BY]) {
    for (let i = lines.length - 1; i >= 0; i--) {
      const m = pattern.exec(lines[i]);
      if (!m || !namesModel(m[1])) continue;
      const name = modelOf(m[1]);
      if (name) return name;
    }
  }
  return null;
}

/** A message without the harness's attribution lines: the gate's Drafted-by line names the model. */
export function withoutAttribution(message) {
  return String(message ?? "").split("\n").filter((line) => !AGENT_ATTRIBUTION.test(line)).join("\n");
}

/**
 * The model of the session a command runs in, from CHAP_MODEL, or else the
 * agent's word from --model: { model, source, differs }, where source is
 * "session" or "agent" (null when neither says) and differs is what
 * --model said when the session names another model.
 */
export function sessionModel({ flag = null, env = process.env } = {}) {
  const session = modelOf(env.CHAP_MODEL);
  const said = modelOf(flag);
  if (flag && !said) throw new Error(`Not a model name a commit can carry: ${JSON.stringify(flag)}`);
  if (session) return { model: session, source: "session", differs: said && said !== session ? said : null };
  if (said) return { model: said, source: "agent", differs: null };
  return { model: null, source: null, differs: null };
}

/**
 * The model a branch names as a whole, from its commits and the session:
 * the model every commit names when they agree, else the session's or
 * the agent's word, else the models the commits name, in order.
 */
export function branchModel(commits, session) {
  const named = [...new Set(commits.map((c) => c.model).filter(Boolean))];
  if (named.length && commits.every((c) => c.model)) return { model: named.join(" and "), source: "commits" };
  if (session?.model) return { model: session.model, source: session.source };
  if (named.length) return { model: named.join(" and "), source: "commits" };
  return { model: null, source: null };
}

/** How the desk and the commands say where a model's name came from. */
export const MODEL_SOURCES = {
  commits: "from the commits",
  session: "from the session",
  agent: "as the agent says",
};
