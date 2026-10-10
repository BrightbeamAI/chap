// The prompt a reviewer's decision becomes for the agent that made the
// change: what was decided and by whom, the reviewer's note, their comments
// on lines with the code each is about, for an edit the difference between
// the agent's version and the reviewer's, and what to do next with the
// command that proposes the work again. The desk shows it to copy, and the
// commands print it, so an agent waiting on them reads it straight away.
// No DOM here: the commands load this under Node.

import { unifiedDiff } from "./diff.js";

const quote = (text) => String(text ?? "").split("\n").map((line) => `> ${line}`).join("\n");

/** Text in a code fence long enough for the text's own backticks. */
export function fence(text, lang = "") {
  const body = String(text).replace(/\n$/, "");
  const longest = Math.max(2, ...[...body.matchAll(/`+/g)].map((m) => m[0].length));
  const ticks = "`".repeat(longest + 1);
  return `${ticks}${lang}\n${body}\n${ticks}`;
}

/** The artefact the agent submitted for the round a decision closed. */
export function submittedArtefact(task) {
  const env = task?.submission?.envelope;
  if (!env) return null;
  return (env.method === "review.request" ? env.params?.artefact : env.params?.output) ?? null;
}

/** What a reviewer's edit changed, file by file: the agent's version against theirs, as unified diffs. */
export function editDiff(proposed, approved) {
  if (!Array.isArray(proposed?.files) || !Array.isArray(approved?.files)) return "";
  const theirs = new Map(proposed.files.map((f) => [f.path, f]));
  let out = "";
  for (const f of approved.files) {
    const p = theirs.get(f.path);
    if (!p || typeof p.after !== "string" || typeof f.after !== "string" || p.after === f.after) continue;
    out += unifiedDiff(f.path, p.after, f.after);
  }
  return out;
}

/** Where a line comment points, in words: the file, the line, and the commit of a branch. */
function placeOf(c) {
  const parts = [c.path];
  if (c.line) parts.push(`${c.side === "old" ? "old line" : "line"} ${c.line}`);
  if (c.commit) parts.push(`commit ${c.commit_index && c.commit_of ? `${c.commit_index} of ${c.commit_of}, ` : ""}${String(c.commit).slice(0, 7)}`);
  return parts.join(", ");
}

/** The kind of a decision as the prompt speaks of it. */
export function decisionKind(d) {
  if (!d) return null;
  if (d.kind === "override") return "edit";
  if (d.kind === "reject") return d.request_revision ? "changes" : "reject";
  return d.kind === "approve" ? "approve" : null;
}

/**
 * The prompt for a decision on a task, or null when it asks nothing of the
 * agent (an approval as written with no comment). `task` is the task's
 * whole view; `decision` a row of its decision log (kind, comment,
 * rationale, request_revision, comments, reviewer); `reviewer` how to name
 * the reviewer; `command` the command that proposes the work again.
 */
export function followUpPrompt({ task, decision, reviewer = null, command = null }) {
  const kind = decisionKind(decision);
  const comments = (decision?.comments ?? []).filter((c) => c && typeof c.text === "string" && c.text.trim());
  if (!task || !kind || (kind === "approve" && !comments.length)) return null;
  const a = task.output ?? task.artefact ?? {};
  const what = a.summary ?? task.input?.summary ?? task.task_id;
  const isBranch = Array.isArray(a.commits);
  const where = [a.repo && `repository ${a.repo}`, a.branch && `branch ${a.branch}`, `review ${task.task_id}`].filter(Boolean).join(", ");
  // A configuration that calls its reviewer "You" reads oddly to the agent.
  const who = !reviewer || /^you$/i.test(reviewer.trim()) ? (decision.reviewer && !/^human:you@/.test(decision.reviewer) ? decision.reviewer : "The reviewer") : reviewer;
  const out = [];
  if (kind === "changes") out.push(`${who} reviewed "${what}" (${where}) and asked for changes.`);
  if (kind === "reject") out.push(`${who} reviewed "${what}" (${where}) and rejected it.`);
  if (kind === "edit") out.push(`${who} reviewed "${what}" (${where}) and approved it with an edit of their own; their version is the one committed.`);
  if (kind === "approve") out.push(`${who} approved "${what}" (${where}), with comments to act on next.`);
  const note = kind === "edit" ? (decision.rationale ?? decision.comment) : decision.comment;
  if (note) out.push("", kind === "edit" ? "Their reason for the edit:" : "Their note:", quote(note));
  if (comments.length) {
    out.push("", "Their comments on the code:");
    for (const c of comments) {
      out.push(`- ${placeOf(c)}:`);
      if (c.code) out.push(...fence(c.code).split("\n").map((line) => `  ${line}`));
      out.push(...c.text.trim().split("\n").map((line) => `  ${line}`));
    }
  }
  out.push("", "What to do:");
  if (kind === "edit") {
    const diff = editDiff(submittedArtefact(task), task.output);
    if (diff) out.splice(out.length - 1, 0, "", "What they changed in your version:", fence(diff, "diff"));
    out.push(
      "1. Read the edit and the reason for it. Their version is committed; keep it.",
      "2. Make the same correction anywhere else in this repository it applies, and keep to it in what you write next.",
      "3. Propose any further change for review as before.",
    );
  } else if (kind === "changes") {
    out.push(isBranch
      ? `1. Change the commits on ${a.branch ?? "the branch"} as the review asks, and nothing it does not ask for. Amend the commit each comment is about (git rebase -i ${String(a.base ?? "<base>").slice(0, 12)}, then edit or fixup), and keep each commit one step with a message that says what it does.`
      : "1. Change the working tree as the review asks, and nothing it does not ask for.");
    out.push("2. Run the tests, and say in your context note what you ran and what it showed.");
    out.push(`3. Propose it again; the same review carries on${command ? ":" : "."}`);
    if (command) out.push(fence(command, "bash"));
  } else if (kind === "reject") {
    out.push("1. Stop work on this change, and do not propose it again as it stands.", "2. If their note points to another approach, ask before you start on it.");
  } else {
    out.push("1. Act on the comments in a follow-up change, and propose it for review as before.");
  }
  return out.join("\n");
}
