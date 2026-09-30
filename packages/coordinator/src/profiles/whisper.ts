/**
 * whisper/1.0 profile (profiles/whisper.md).
 *
 * Methods:
 *   - whisper.ask     -> pose a deadline-bound question with options + default
 *   - whisper.answer  -> answer a whisper
 *
 * Lapse handling: coordinator.checkWhisperLapses(workspaceId, now?) is
 * registered on the Coordinator instance for deployments to call from a
 * scheduler.
 *
 * Error codes (per spec S6):
 *   -32020 WHISPER_ALREADY_ANSWERED
 *   -32021 WHISPER_LAPSED
 *   -32022 WHISPER_OPTION_NOT_IN_SET
 */
import type { Coordinator } from "../coordinator.js";
import { canonicalize } from "../canonical.js";
import { E, rpcError } from "../jsonrpc.js";
import type { WhisperPrompt, Envelope } from "../types.js";

function parseIso(ts: string): number { return new Date(ts).getTime(); }

/**
 * Whether an answer field holds anything, read as Python reads a value's
 * truth, so both references agree that an empty answer is no answer.
 */
function present(v: unknown): boolean {
  if (v === undefined || v === null || v === false || v === 0 || v === "") return false;
  if (Array.isArray(v)) return v.length > 0;
  if (typeof v === "object") return Object.keys(v as object).length > 0;
  return true;
}

export function registerWhisper(coord: Coordinator): void {
  coord.handlers.set("whisper.ask", (p) => {
    const ws = coord.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    for (const f of ["from", "to", "task_id", "question", "deadline_ms", "default_if_lapsed"]) {
      if (!(f in p)) return { error: rpcError(E.PARAMS, `Missing field: ${f}`) };
    }
    if (!ws.tasks.has(p.task_id as string)) {
      return { error: rpcError(E.PARAMS, "Unknown task") };
    }
    if (p.options !== undefined && p.options !== null && !Array.isArray(p.options)) {
      return { error: rpcError(E.PARAMS, "options must be a list") };
    }
    const to = p.to;
    const askee: string[] = Array.isArray(to) ? (to as string[]) : [to as string];
    const id = (p.whisper_id as string) || coord.ids.artefactId();
    const prompt: WhisperPrompt = {
      id,
      task_id: p.task_id as string,
      asker: p.from as string,
      askee,
      question: p.question as string,
      options: p.options as WhisperPrompt["options"],
      asked_at: coord.now(),
      deadline_ms: Number(p.deadline_ms),
      default_if_lapsed: p.default_if_lapsed,
      urgency: (p.urgency as WhisperPrompt["urgency"]) || "low",
      state: "pending",
    };
    ws.whispers.set(id, prompt);
    return { result: { whisper_id: id, deadline_ms: prompt.deadline_ms } };
  });

  coord.handlers.set("whisper.answer", (p) => {
    const ws = coord.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    for (const f of ["from", "whisper_id"]) {
      if (!(f in p)) return { error: rpcError(E.PARAMS, `Missing field: ${f}`) };
    }
    const prompt = ws.whispers.get(p.whisper_id as string);
    if (!prompt) return { error: rpcError(E.PARAMS, "Unknown whisper id") };
    // Only a participant the whisper was addressed to may answer it. A
    // broadcast scope (workspace:/group:) is satisfied by any member; the
    // coordinator does not model group membership (see SPECIFICATION.md).
    const answerer = p.from as string;
    const broadcast = prompt.askee.some((a) => typeof a === "string" && (a.startsWith("workspace:") || a.startsWith("group:")));
    if (broadcast) {
      if (!ws.members.has(answerer)) {
        return { error: rpcError(E.NOT_AUTHORISED, `Not a workspace member: ${answerer}`) };
      }
    } else if (!prompt.askee.includes(answerer)) {
      return { error: rpcError(E.NOT_AUTHORISED, `Whisper was not addressed to ${answerer}`) };
    }
    if (prompt.state === "answered") {
      return { error: rpcError(E.WHISPER_ALREADY_ANSWERED, "Whisper already answered") };
    }
    if (prompt.state === "lapsed") {
      return { error: rpcError(E.WHISPER_LAPSED, "Whisper already lapsed") };
    }

    // A null option is no option, and an empty answer no answer, as in Python.
    const answerOption = p.answer_option as string | undefined;
    const hasOption = answerOption !== undefined && answerOption !== null;
    const answerText = (present(p.answer) ? p.answer : p.answer_text) as string | undefined;
    if (prompt.options && prompt.options.length) {
      if (!hasOption) {
        return { error: rpcError(E.PARAMS, "answer_option is required when options are defined") };
      }
      const valid = new Set(prompt.options.map(o => o.id));
      if (!valid.has(answerOption)) {
        return { error: rpcError(E.WHISPER_OPTION_NOT_IN_SET,
          `Answer option ${canonicalize(answerOption as never).toString("utf-8")} not in option set`) };
      }
    } else if (!present(answerText) && !hasOption) {
      return { error: rpcError(E.PARAMS, "answer or answer_option required") };
    }

    prompt.state = "answered";
    prompt.answered_at = coord.now();
    prompt.answered_by = p.from as string;
    prompt.answer_option = answerOption;
    prompt.answer_text = answerText;
    prompt.comment = p.comment as string | undefined;
    return { result: { answered: true, whisper_id: prompt.id, task_id: prompt.task_id } };
  });

  // Expose the lapse-check function on the coordinator instance.
  coord.checkWhisperLapses = (workspaceId: string, now?: string): Envelope[] => {
    const ws = coord.workspaces.get(workspaceId);
    if (!ws) return [];
    const cutoff = parseIso(now ?? coord.now());
    const emitted: Envelope[] = [];
    for (const prompt of ws.whispers.values()) {
      if (prompt.state !== "pending") continue;
      const asked = parseIso(prompt.asked_at);
      const deadline = asked + prompt.deadline_ms;
      if (cutoff < deadline) continue;
      prompt.state = "lapsed";
      prompt.default_applied = prompt.default_if_lapsed;
      const notify: Envelope = {
        jsonrpc: "2.0",
        method: "notify.message",
        params: {
          workspace: ws.id,
          from: "service:coordinator",
          to: [prompt.asker, ...prompt.askee],
          ts: coord.now(),
          kind: "whisper_lapsed",
          whisper_id: prompt.id,
          // Carry the task so a task-filtered audit.read surfaces the
          // lapse. A lapsed whisper applies its default with no human
          // input, so it is the case most in need of being visible on
          // the task's thread.
          task_id: prompt.task_id,
          default_applied: prompt.default_if_lapsed,
        },
      };
      coord.recordAudit(ws, notify);
      emitted.push(notify);
    }
    return emitted;
  };
}
