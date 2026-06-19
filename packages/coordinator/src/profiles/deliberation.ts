/**
 * deliberation/1.0 profile (profiles/deliberation.md).
 *
 * Methods:
 *   - deliberate.open
 *   - deliberate.comment
 *   - deliberate.vote
 *   - deliberate.close
 *
 * Rules: any_one_approves | all_approve | quorum:N |
 *        weighted_vote:T | weighted_vote_with_veto:T
 *
 * Error codes (spec S5):
 *   -32030 voter not in participant list
 *   -32031 already voted
 *   -32032 closed or lapsed
 *   -32033 unknown rule
 */
import type { Coordinator } from "../coordinator.js";
import { E, rpcError } from "../jsonrpc.js";
import type { Deliberation } from "../types.js";

function parseRule(rule: string): { kind: string; params: Record<string, number> } {
  if (rule.includes(":")) {
    const [kind, arg] = rule.split(":", 2);
    if (kind === "quorum") return { kind, params: { n: parseInt(arg, 10) } };
    if (kind === "weighted_vote" || kind === "weighted_vote_with_veto") {
      return { kind, params: { threshold: parseFloat(arg) } };
    }
  }
  if (rule === "any_one_approves" || rule === "all_approve") return { kind: rule, params: {} };
  throw new Error(`Unknown rule: ${rule}`);
}

function computeOutcome(delib: Deliberation): Deliberation["outcome"] {
  const parsed = parseRule(delib.rule);
  const yea = delib.votes.filter(v => v.vote === "yea");
  const nay = delib.votes.filter(v => v.vote === "nay");
  const vetoes = delib.votes.filter(v => v.veto_invoked && (delib.veto ?? {})[v.voter]);
  const weights = delib.weights ?? {};

  const weighted = (vs: typeof yea) =>
    vs.reduce((s, v) => s + (weights[v.voter] ?? v.weight ?? 1.0), 0);

  if (parsed.kind === "any_one_approves") {
    return {
      outcome: yea.length >= 1 ? "approved" : "rejected",
      rule: delib.rule,
      tally: { yea: yea.length, nay: nay.length },
      vetoes: vetoes.map(v => v.voter),
    };
  }
  if (parsed.kind === "all_approve") {
    const passed = yea.length === delib.participants.length && nay.length === 0;
    return {
      outcome: passed ? "approved" : "rejected",
      rule: delib.rule,
      tally: { yea: yea.length, nay: nay.length },
      vetoes: vetoes.map(v => v.voter),
    };
  }
  if (parsed.kind === "quorum") {
    const n = parsed.params.n;
    const cast = yea.length + nay.length;
    if (cast < n) {
      return {
        outcome: "rejected", reason: "quorum not met", rule: delib.rule,
        tally: { yea: yea.length, nay: nay.length }, vetoes: [],
      };
    }
    return {
      outcome: yea.length > nay.length ? "approved" : "rejected",
      rule: delib.rule,
      tally: { yea: yea.length, nay: nay.length },
      vetoes: [],
    };
  }
  if (parsed.kind === "weighted_vote") {
    const yeaW = weighted(yea), nayW = weighted(nay);
    return {
      outcome: yeaW >= parsed.params.threshold ? "approved" : "rejected",
      rule: delib.rule,
      tally: { yea: yeaW, nay: nayW }, vetoes: [],
    };
  }
  if (parsed.kind === "weighted_vote_with_veto") {
    if (vetoes.length) {
      return {
        outcome: "rejected", rule: delib.rule,
        tally: { yea: 0, nay: 0 },
        vetoes: vetoes.map(v => v.voter),
      };
    }
    const yeaW = weighted(yea), nayW = weighted(nay);
    return {
      outcome: yeaW >= parsed.params.threshold ? "approved" : "rejected",
      rule: delib.rule,
      tally: { yea: yeaW, nay: nayW }, vetoes: [],
    };
  }
  return { outcome: "error", rule: delib.rule, reason: `unsupported rule ${parsed.kind}` };
}

export function registerDeliberation(coord: Coordinator): void {
  coord.handlers.set("deliberate.open", (p) => {
    const ws = coord.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    for (const f of ["from", "rule"]) {
      if (!(f in p)) return { error: rpcError(E.PARAMS, `Missing field: ${f}`) };
    }
    const to = p.to ?? p.participants;
    const participants: string[] = Array.isArray(to) ? (to as string[]) : typeof to === "string" ? [to] : [];
    if (!participants.length) {
      return { error: rpcError(E.PARAMS, "deliberate.open needs 'to' (participants list)") };
    }
    try { parseRule(p.rule as string); }
    catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      return { error: rpcError(E.DELIB_UNKNOWN_RULE, msg) };
    }
    const id = (p.deliberation_id as string) || coord.ids.deliberationId();
    const delib: Deliberation = {
      id,
      task_id: p.task_id as string | undefined,
      opener: p.from as string,
      participants,
      rule: p.rule as string,
      question: p.question as string | undefined,
      weights: p.weights as Deliberation["weights"],
      veto: p.veto as Deliberation["veto"],
      deadline: p.deadline as string | undefined,
      state: "open",
      comments: [],
      votes: [],
      opened_at: coord.now(),
    };
    ws.deliberations.set(id, delib);
    return { result: { deliberation_id: id, state: "open" } };
  });

  coord.handlers.set("deliberate.comment", (p) => {
    const ws = coord.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    const delib = ws.deliberations.get(p.deliberation_id as string);
    if (!delib) return { error: rpcError(E.PARAMS, "Unknown deliberation") };
    if (delib.state !== "open") {
      return { error: rpcError(E.DELIB_CLOSED_OR_LAPSED, "Deliberation not open") };
    }
    delib.comments.push({
      ts: coord.now(),
      from: p.from as string,
      text: (p.comment as string) || (p.text as string) || "",
    });
    return { result: { accepted: true } };
  });

  coord.handlers.set("deliberate.vote", (p) => {
    const ws = coord.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    const delib = ws.deliberations.get(p.deliberation_id as string);
    if (!delib) return { error: rpcError(E.PARAMS, "Unknown deliberation") };
    if (delib.state !== "open") {
      return { error: rpcError(E.DELIB_CLOSED_OR_LAPSED, "Deliberation not open") };
    }
    for (const f of ["from", "vote"]) {
      if (!(f in p)) return { error: rpcError(E.PARAMS, `Missing field: ${f}`) };
    }
    const v = p.vote as string;
    if (v !== "yea" && v !== "nay" && v !== "abstain") {
      return { error: rpcError(E.PARAMS, "vote must be yea/nay/abstain") };
    }
    const voter = p.from as string;
    if (!delib.participants.includes(voter)) {
      return { error: rpcError(E.DELIB_VOTER_NOT_IN_LIST,
        `Voter ${voter} not in participant list`) };
    }
    if (delib.votes.some(x => x.voter === voter)) {
      return { error: rpcError(E.DELIB_ALREADY_VOTED, `Voter ${voter} has already voted`) };
    }
    delib.votes.push({
      ts: coord.now(),
      voter,
      vote: v as "yea" | "nay" | "abstain",
      weight: p.weight as number | undefined,
      comment: p.comment as string | undefined,
      veto_invoked: !!p.veto_invoked,
    });
    return { result: { recorded: true } };
  });

  coord.handlers.set("deliberate.close", (p) => {
    const ws = coord.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    const delib = ws.deliberations.get(p.deliberation_id as string);
    if (!delib) return { error: rpcError(E.PARAMS, "Unknown deliberation") };
    if (delib.state === "closed") return { result: delib.outcome! };
    delib.state = "closed";
    delib.outcome = computeOutcome(delib);
    return { result: delib.outcome };
  });
}
