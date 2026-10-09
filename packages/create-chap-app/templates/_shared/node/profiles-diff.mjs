// diff-profiles: the same workload under two profile sets, side by side.
//
// Each step is one CHAP call. The table shows what each profile set did
// with it: accepted, or refused with which code. The rows that differ are
// what a profile changes. The decisions here are scripted, because this is
// a comparison and no person is at the desk; in the project itself the
// decision is always made in the desk.
//
// The workload runs in-process on an in-memory coordinator, so it needs no
// server and no model. The same steps exist in the Python templates, and
// the explorer merges both into docs/profile-explorer.md.

import { Coordinator, MemoryStore } from "@brightbeamai/chap-coordinator";

export const AGENT = "agent:drafter";
export const HUMAN = "human:reviewer";
export const SECOND = "human:second";

/** The steps. `call` receives a sender and returns an envelope's method and params. */
export const STEPS = [
  { id: "task.create.trial", label: "The agent opens a trial-mode task that requires review",
    method: "task.create", from: AGENT, params: (s) => ({ kind: "draft", input: { n: 1 }, assignee: AGENT, mode: "trial", review_required: true }), keep: "task" },
  { id: "task.create.above-ceiling", label: "The agent opens a production-mode task above a trial ceiling",
    method: "task.create", from: AGENT, params: () => ({ kind: "draft", input: {}, assignee: AGENT, mode: "production" }) },
  { id: "task.complete", label: "The agent submits the task's output",
    method: "task.complete", from: AGENT, params: (s) => ({ task_id: s.task, output: { body: "draft" } }) },
  { id: "decide.approve.self", label: "The agent approves its own work",
    method: "decide.approve", from: AGENT, params: (s) => ({ task_id: s.task }) },
  { id: "decide.approve.human", label: "The reviewer approves the work",
    method: "decide.approve", from: HUMAN, params: (s) => ({ task_id: s.task }) },
  { id: "task.create.trial.plain", label: "The agent opens a trial-mode task without asking for review",
    method: "task.create", from: AGENT, params: () => ({ kind: "draft", input: { n: 2 }, assignee: AGENT, mode: "trial" }), keep: "plain" },
  { id: "task.complete.plain", label: "The agent submits that task's output",
    method: "task.complete", from: AGENT, params: (s) => ({ task_id: s.plain, output: { body: "draft" } }) },
  { id: "task.create.unsigned", label: "An unsigned call from the reviewer",
    method: "task.create", from: HUMAN, params: () => ({ kind: "note", input: {}, assignee: HUMAN }), unsigned: true },
  { id: "whisper.ask", label: "The agent asks the reviewer a quick question with a default",
    method: "whisper.ask", from: AGENT, params: (s) => ({ task_id: s.task, to: [HUMAN], question: "Send now?", deadline_ms: 1, default_if_lapsed: "yes" }), keep: "whisper" },
  { id: "whisper.answer.stranger", label: "Someone the question was not put to answers it",
    method: "whisper.answer", from: SECOND, params: (s) => ({ whisper_id: s.whisper ?? "none", answer: "no" }) },
  { id: "deliberate.open", label: "The reviewer opens a vote under quorum:2",
    method: "deliberate.open", from: HUMAN, params: (s) => ({ task_id: s.task, to: [HUMAN, SECOND], rule: "quorum:2", question: "Ship it?" }), keep: "deliberation" },
  { id: "deliberate.close.one-vote", label: "The vote closes after one yea",
    method: "deliberate.close", from: HUMAN, params: (s) => ({ deliberation_id: s.deliberation ?? "none" }), before: (s) => ({ method: "deliberate.vote", from: HUMAN, params: { deliberation_id: s.deliberation ?? "none", vote: "yea" } }) },
  { id: "control.pause.agent", label: "The reviewer pauses the agent",
    method: "control.pause", from: HUMAN, params: () => ({ scope: "participant", participant_uri: AGENT, reason: "hold" }) },
  { id: "task.create.paused", label: "A task is assigned to the paused agent",
    method: "task.create", from: HUMAN, params: () => ({ kind: "draft", input: {}, assignee: AGENT }) },
  { id: "handoff.propose", label: "The reviewer hands a task to the second reviewer",
    method: "handoff.propose", from: HUMAN, params: (s) => ({ to: SECOND, tasks: [{ task_id: s.task, summary: "yours" }] }), before: (s) => ({ method: "task.create", from: HUMAN, params: { kind: "note", input: {}, assignee: HUMAN }, keep: "task" }) },
  { id: "task.route", label: "The agent asks the coordinator to choose an assignee",
    method: "task.route", from: AGENT, params: (s) => ({ task_id: s.task, candidates: [HUMAN, SECOND] }) },
  { id: "audit.verify_chain", label: "Anyone verifies the chain",
    method: "audit.verify_chain", from: HUMAN, params: () => ({}) },
];

const PROFILE_OPTIONS = (profiles) => ({
  enableChain: profiles.includes("audit-scitt/1.0"),
  requireSignatures: profiles.includes("security-signed/1.0"),
});

/**
 * Run the workload under one profile set. Returns one outcome per step:
 * { status: "ok" | "refused", code?, message? }.
 */
export async function runWorkload(profiles, { workspace = "wsp_diff", signers = null } = {}) {
  const coord = new Coordinator({ store: new MemoryStore(), defaultProfiles: profiles, ...PROFILE_OPTIONS(profiles) });
  await coord.start();
  const signed = profiles.includes("security-signed/1.0");
  let n = 0;
  const send = async (method, from, params, { unsigned = false } = {}) => {
    let env = { jsonrpc: "2.0", id: `d-${++n}`, method, params: { workspace, from, ...params } };
    if (signed && !unsigned && signers && method !== "workspace.create" && method !== "participant.join") {
      env = await signers[from].sign(env);
    }
    return coord.dispatch(env);
  };
  const outcome = (r) => (r.error ? { status: "refused", code: r.error.code, message: r.error.message } : { status: "ok", result: r.result });

  const create = await send("workspace.create", HUMAN, { profiles, mode: "trial", mode_ceiling: "trial" });
  if (create.error) throw new Error(`workspace.create under ${profiles.join(",")}: ${create.error.message}`);
  for (const [uri, type] of [[HUMAN, "human"], [SECOND, "human"], [AGENT, "agent"]]) {
    const params = { type, role: type === "human" ? "reviewer" : "drafter" };
    if (signed && signers) params.jwks = { keys: [signers[uri].publicJwk] };
    const r = await send("participant.join", uri, params);
    if (r.error) throw new Error(`participant.join ${uri}: ${r.error.message}`);
  }

  const state = {};
  const rows = [];
  for (const step of STEPS) {
    if (step.before) {
      const b = step.before(state);
      const r = await send(b.method, b.from, b.params);
      if (b.keep && r.result) state[b.keep] = r.result.task_id ?? r.result[`${b.keep}_id`];
    }
    const r = await send(step.method, step.from, step.params(state), { unsigned: step.unsigned });
    if (step.keep && r.result) state[step.keep] = r.result.task_id ?? r.result.whisper_id ?? r.result.deliberation_id ?? r.result.new_task_id;
    rows.push({ id: step.id, label: step.label, method: step.method, outcome: outcome(r) });
  }
  return rows;
}

/** Ed25519 signers for the three participants, from the desk's client module. */
export async function makeSigners(generateSigner) {
  const out = {};
  for (const uri of [HUMAN, SECOND, AGENT]) out[uri] = await generateSigner(uri);
  return out;
}

export function compare(a, b) {
  return a.map((row, i) => ({ id: row.id, label: row.label, method: row.method, a: row.outcome, b: b[i].outcome, differs: summary(row.outcome) !== summary(b[i].outcome) }));
}

export function summary(o) {
  if (o.status !== "ok") return `refused ${o.code}`;
  const state = o.result && typeof o.result === "object" ? o.result.state : undefined;
  return typeof state === "string" ? `accepted, ${state}` : "accepted";
}

export function renderTable(setA, setB, rows) {
  const w = Math.max(...rows.map((r) => r.label.length), 10);
  const head = `${"Step".padEnd(w)}  ${setA.join(",").padEnd(22)}  ${setB.join(",")}`;
  const lines = [head, "-".repeat(head.length)];
  for (const r of rows) {
    lines.push(`${r.label.padEnd(w)}  ${summary(r.a).padEnd(22)}  ${summary(r.b)}${r.differs ? "   <- differs" : ""}`);
  }
  return lines.join("\n");
}

/** Parse `--profiles a,b --against c,d --json` from argv. */
export function parseArgs(argv, defaults) {
  const out = { profiles: defaults, against: ["core/1.0"], json: false };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--profiles") out.profiles = argv[++i].split(",").map((s) => s.trim()).filter(Boolean);
    else if (argv[i] === "--against") out.against = argv[++i].split(",").map((s) => s.trim()).filter(Boolean);
    else if (argv[i] === "--json") out.json = true;
  }
  return out;
}

export async function main({ template, language = "typescript", defaults, argv = process.argv.slice(2), generateSigner }) {
  const args = parseArgs(argv, defaults);
  const needSigners = [args.profiles, args.against].some((p) => p.includes("security-signed/1.0"));
  const signers = needSigners && generateSigner ? await makeSigners(generateSigner) : null;
  const a = await runWorkload(args.profiles, { signers });
  const b = await runWorkload(args.against, { signers });
  const rows = compare(a, b);
  if (args.json) {
    console.log(JSON.stringify({ template, language, sets: { a: args.profiles, b: args.against }, rows }, null, 2));
  } else {
    console.log(`Workload: ${template}. Scripted decisions, for comparison only.\n`);
    console.log(renderTable(args.profiles, args.against, rows));
  }
}
