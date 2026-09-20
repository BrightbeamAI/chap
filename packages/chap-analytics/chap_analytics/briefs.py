"""
Short written findings, one per decision in the roadmap's table.

A :class:`Brief` is a headline with the number, its interval and the sample
size behind it; a few sentences on what the workspace's chain shows; and the
decision it bears on. The wording changes with the numbers, and a brief whose
statistic lacks the data to speak says so.

    from chap_analytics import frames, briefs
    from chap_analytics.sample import support_desk

    for b in briefs.everything(frames(support_desk())):
        print(b.headline)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from . import graph as _graph
from . import stats as _stats
from .frames import Frames

__all__ = ["Brief", "everything", "overrides", "paths", "calibration", "latency",
           "promotion", "agreement", "whispers", "handoffs", "assurance", "drift",
           "concentration", "coverage", "duties"]


@dataclass
class Brief:
    """One finding: what the chain shows and what it bears on."""
    name: str
    title: str
    headline: str
    text: str
    decision: str
    sufficient: bool
    table: pd.DataFrame | None = field(default=None, repr=False)
    numbers: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"{self.title}\n{self.headline}\n\n{self.text}\n\nDecision: {self.decision}"

    def to_markdown(self) -> str:
        """The brief as a Markdown section: title, headline in bold, text, decision in italics."""
        return f"### {self.title}\n\n**{self.headline}**\n\n{self.text}\n\n*{self.decision}*\n"


def _n(count: int, noun: str, plural: str | None = None) -> str:
    """``1 task``, ``2 tasks``."""
    count = int(count)
    return f"{count} {noun if count == 1 else (plural or noun + 's')}"


def _pct(x: float) -> str:
    return f"{x:.0%}" if pd.notna(x) else "n/a"


def _hours(s: float) -> str:
    if pd.isna(s):
        return "n/a"
    if s < 3600:
        return f"{s / 60:.0f} minutes"
    if s < 86400 * 2:
        return f"{s / 3600:.1f} hours"
    return f"{s / 86400:.1f} days"


def _interval(low: float, high: float) -> str:
    return f"{_pct(low)} to {_pct(high)}"


# ============================================================================
#   Rates
# ============================================================================

def overrides(f: Frames) -> Brief:
    """How often reviewers change the agent's work, and whether they refine it or reverse it."""
    r = _stats.rates(f)
    rr = _stats.refine_reverse(f)
    if r.empty:
        return Brief("overrides", "How often the agent's work is changed", "No decided tasks yet.",
                     "Reviewers have settled no task on this chain, so there is no rate to report.",
                     "Wait for decisions.", False, r)
    row = r.iloc[0]
    n = int(row["n"])
    changed = row["override_rate"] + row["reject_rate"]
    head = (f"Reviewers changed {_pct(changed)} of {n} decided tasks: {_pct(row['override_rate'])} corrected "
            f"({_interval(row['override_low'], row['override_high'])}) and {_pct(row['reject_rate'])} sent back.")
    parts = [f"{_pct(row['approve_rate'])} of the agent's work went out as drafted."]
    if not rr.empty and rr.iloc[0]["refining"] + rr.iloc[0]["reversing"] > 0:
        s = rr.iloc[0]
        parts.append(f"Of the corrections that said which, {_pct(s['refine_share'])} refined the agent's decision "
                     f"and {_pct(1 - s['refine_share'])} reversed it "
                     f"({int(s['refining'])} against {int(s['reversing'])}, {int(s['unstated'])} unstated).")
        if s["refine_share"] >= 0.6:
            decision = "Most corrections keep the decision and change the wording: the prompt is the place to look."
        elif s["refine_share"] <= 0.4:
            decision = "Most corrections reverse the decision: look at the policy the agent applies and the context it is given."
        else:
            decision = "Corrections split between wording and substance: look at the tags and paths to separate them."
    else:
        decision = "Ask reviewers to set intent_preserved on overrides so refining can be told from reversing."
    if not row["sufficient"]:
        parts.append(f"With {n} decided tasks the interval is wide; read the direction rather than the figure.")
    return Brief("overrides", "How often the agent's work is changed", head, " ".join(parts), decision,
                 bool(row["sufficient"]), r, {"n": n, "override_rate": float(row["override_rate"]),
                                               "reject_rate": float(row["reject_rate"])})


def paths(f: Frames) -> Brief:
    """Which part of the artefact reviewers correct most, by task kind."""
    p = _stats.patch_paths(f)
    if p.empty:
        return Brief("paths", "Where the corrections land", "No overrides yet.",
                     "No override carries a patch on this chain.", "Nothing to fix yet.", False, p)
    top = p.sort_values("n", ascending=False).iloc[0]
    head = (f"The most corrected part of the artefact is {top['top_path']} in {top['task_kind']} tasks: "
            f"{int(top['n'])} of {int(top['overrides'])} corrections of that kind ({_pct(top['share'])}).")
    kinds = p["task_kind"].nunique() if "task_kind" in p.columns else 1
    text = (f"Across {kinds} task kind{'s' if kinds != 1 else ''}, corrections touch "
            f"{p['top_path'].nunique()} distinct parts of the artefact. The heatmap ranks them per kind, and the "
            f"rationales behind any cell are one call away.")
    return Brief("paths", "Where the corrections land", head, text,
                 f"Start with the prompt section that produces {top['top_path']} for {top['task_kind']}.",
                 True, p, {"top_path": top["top_path"], "task_kind": top["task_kind"], "share": float(top["share"])})


# ============================================================================
#   Calibration
# ============================================================================

def calibration(f: Frames) -> Brief:
    """Whether the agent's reported confidence tracks what reviewers accepted."""
    c = _stats.calibration(f)
    n, ece, brier = c.attrs["n"], c.attrs["ece"], c.attrs["brier"]
    if n == 0:
        return Brief("calibration", "Whether confidence can be trusted", "No confidence reported.",
                     "The agent reports no confidence on its tasks, so there is nothing to calibrate.",
                     "Have the agent report routing_hints.confidence.", False, c)
    filled = c[c["n"] >= 5]
    over = filled[filled["acceptance"] < filled["mean_confidence"] - 0.1]
    head = f"Expected calibration error {ece:.2f} over {n} tasks; Brier score {brier:.2f}."
    if ece < 0.05:
        text = "Reported confidence tracks acceptance closely. A threshold on it will behave as its number suggests."
        decision = "Confidence is usable as a routing signal as it stands."
    elif not over.empty:
        worst = over.sort_values("n", ascending=False).iloc[0]
        text = (f"The agent is overconfident: at a reported {worst['mean_confidence']:.0%} reviewers accepted "
                f"{_pct(worst['acceptance'])} ({int(worst['n'])} tasks). Its stated confidence runs ahead of its record.")
        decision = "Set routing thresholds from the observed acceptance at each confidence level rather than the reported number."
    else:
        text = "The gap between reported confidence and acceptance is on the cautious side: the agent undersells its work."
        decision = "Thresholds can sit lower than the reported confidence suggests."
    if not c.attrs["sufficient"]:
        text += f" {n} tasks is below the {c.attrs['minimum']} the curve needs to be read bin by bin."
    return Brief("calibration", "Whether confidence can be trusted", head, text, decision,
                 bool(c.attrs["sufficient"]), c, {"n": n, "ece": ece, "brier": brier})


# ============================================================================
#   Latency
# ============================================================================

def latency(f: Frames) -> Brief:
    """How long reviews wait for a first decision, and how many are still waiting."""
    lt = _stats.latency(f)
    q = _stats.open_queue(f)
    by = _stats.latency_by(f, by="reviewer")
    if lt.empty:
        return Brief("latency", "How long decisions take", "No reviews opened yet.",
                     "No review pass is on this chain.", "Nothing waiting.", False, lt)
    decided = lt[lt["event"]]
    med = decided["duration_s"].median() if not decided.empty else float("nan")
    p90 = decided["duration_s"].quantile(0.9) if not decided.empty else float("nan")
    head = (f"Half of the reviews were decided within {_hours(med)}, nine in ten within {_hours(p90)}; "
            f"{len(q)} still waiting.")
    parts = [f"{len(decided)} review passes reached a decision."]
    if not q.empty:
        parts.append(f"The oldest open review has waited {_hours(q.iloc[0]['age_s'])}.")
    named = by.dropna(subset=["reviewer"]).sort_values("median_s", ascending=False)
    if len(named) >= 2:
        slow, fast = named.iloc[0], named.iloc[-1]
        parts.append(f"{slow['reviewer']} takes longest at a median of {_hours(slow['median_s'])}; "
                     f"{fast['reviewer']} is quickest at {_hours(fast['median_s'])}.")
    if not q.empty and len(q) > max(3, 0.1 * len(lt)):
        decision = "More than a tenth of reviews are waiting: add a reviewer to that kind of work or reroute it."
    elif pd.notna(p90) and pd.notna(med) and p90 > 4 * med:
        decision = "The long tail is a few stuck items rather than general load: chase the oldest open reviews."
    else:
        decision = "Decisions are keeping up with the work."
    return Brief("latency", "How long decisions take", head, " ".join(parts), decision,
                 len(decided) >= 10, by, {"median_s": float(med) if pd.notna(med) else None,
                                          "p90_s": float(p90) if pd.notna(p90) else None, "open": len(q)})


# ============================================================================
#   Promotion
# ============================================================================

def promotion(f: Frames, threshold: float = 0.10) -> Brief:
    """Whether each agent's substantive correction rate sits under ``threshold``, and how sure that is."""
    p = _stats.promotion(f, threshold=threshold)
    if p.empty:
        return Brief("promotion", "Whether an agent is ready to promote", "No decided tasks yet.",
                     "No agent has enough decided work to judge.", "Wait for decisions.", False, p)
    lines, ready, hold = [], [], []
    for r in p.itertuples(index=False):
        who = getattr(r, "assignee", "the agent")
        lines.append(f"{who}: {_n(r.against, 'substantive correction')} in {_n(r.n, 'decided task')}, "
                     f"true rate {_pct(r.low)} to {_pct(r.high)} (90% credible), "
                     f"{_pct(r.p_below_threshold)} chance it is under {_pct(threshold)}.")
        (ready if r.p_below_threshold >= 0.9 and r.sufficient else hold).append(who)
    if len(lines) == 1:
        head = lines[0]
        r0 = p.iloc[0]
        text = (f"The bar is a substantive correction rate under {_pct(threshold)}: overrides that reversed the "
                f"agent's decision plus rejections, out of decided tasks. With {int(r0['n'])} decided tasks the "
                f"posterior is {'well pinned down' if r0['sufficient'] else 'still mostly the prior'}.")
    else:
        head = f"{len(ready)} of {len(p)} agents sit under the {_pct(threshold)} bar with a posterior probability of 0.9 or more."
        text = " ".join(lines)
    if ready and not hold:
        decision = f"Promote {', '.join(ready)}."
    elif ready:
        decision = f"Promote {', '.join(ready)}; hold {', '.join(hold)} for more decided tasks or a lower correction rate."
    else:
        decision = "Hold. Either the rate is above the bar or too few tasks have been decided to say."
    return Brief("promotion", "Whether an agent is ready to promote", head, text, decision,
                 bool(p["sufficient"].any()), p, {"threshold": threshold})


# ============================================================================
#   Agreement
# ============================================================================

def agreement(f: Frames) -> Brief:
    """Whether reviewers agree with each other on the passes they shared, and voters on the questions they voted on."""
    a = _stats.agreement(f)
    pairs = _stats.pairwise_agreement(f)
    v = _stats.vote_agreement(f)
    if a.empty and v.empty:
        return Brief("agreement", "Whether reviewers agree", "No artefact was decided by two reviewers.",
                     "Every review pass on this chain was settled by one reviewer, and no deliberation was held, "
                     "so agreement cannot be measured.", "Use quorum or all_approve on a sample of tasks to measure it.",
                     False, a)
    parts, head = [], ""
    if not a.empty:
        row = a.iloc[0]
        head = (f"On {int(row['n_passes'])} passes two reviewers decided, kappa is {row['kappa']:.2f} "
                f"(observed agreement {_pct(row['agreement_observed'])}).")
        parts.append("These are passes a first reviewer accepted, since a correction settles a pass; kappa here says "
                     "how often the next reviewer agreed with an acceptance.")
        if not pairs.empty:
            low = pairs.sort_values("kappa").iloc[0]
            if pd.notna(low["kappa"]) and low["n_passes"] >= 5:
                parts.append(f"The pair that agrees least is {low['reviewer_a']} and {low['reviewer_b']} "
                             f"(kappa {low['kappa']:.2f} over {int(low['n_passes'])} passes).")
    if not v.empty:
        vr = v.iloc[0]
        nd = int(vr['n_deliberations'])
        vote_line = (f"Across {nd} deliberation{'s' if nd != 1 else ''} with {int(vr['voters'])} voters, "
                     f"kappa on the votes is {vr['kappa']:.2f}.")
        if head:
            parts.append(vote_line)
        else:
            head = vote_line
    sufficient = bool((not a.empty and a.iloc[0]["sufficient"]) or (not v.empty and v.iloc[0]["sufficient"]))
    observed = a.iloc[0]["agreement_observed"] if not a.empty else v.iloc[0]["agreement_observed"]
    kappa = a.iloc[0]["kappa"] if not a.empty else v.iloc[0]["kappa"]
    if not sufficient:
        decision = "Too few shared decisions to act on; keep the multi-reviewer sample running."
    elif observed >= 0.85 and kappa < 0.4:
        decision = ("Reviewers agree on nearly every shared pass. Kappa is low only because almost all of those "
                    "passes are acceptances, which leaves little disagreement to measure.")
    elif kappa < 0.4:
        decision = "Agreement is low: the policy those decisions apply is ambiguous, and it is the policy to rewrite."
    else:
        decision = "Reviewers apply the policy consistently."
    return Brief("agreement", "Whether reviewers agree", head, " ".join(p for p in parts if p), decision,
                 sufficient, a if not a.empty else v)


# ============================================================================
#   Whispers, handoffs, assurance, drift
# ============================================================================

def whispers(f: Frames) -> Brief:
    """How often agents ask mid-task, how often the question lapses, and how fast an answer comes."""
    w = _stats.whispers(f, by=None)
    if w.empty:
        return Brief("whispers", "Whether agents get answers", "No whispers on this chain.",
                     "No agent asked a question mid-task.", "Nothing to change.", False, w)
    r = w.iloc[0]
    per_task = len(f.whispers) / max(1, len(f.tasks))
    head = (f"{_n(r['n'], 'question')} asked, {int(r['lapsed'])} lapsed ({_pct(r['lapse_rate'])}, "
            f"{_interval(r['low'], r['high'])}); answers took a median of {_hours(r['median_response_s'])}.")
    text = f"That is one question per {1 / per_task:.0f} tasks." if per_task > 0 else ""
    if not r["sufficient"]:
        decision = "Too few resolved questions to judge; the lapse rate will settle as more are asked."
    elif r["lapse_rate"] > 0.25:
        decision = "More than a quarter lapse: the default answer is deciding those questions. Put the answer in the task input, or route whispers to whoever is at the desk."
    else:
        decision = "Questions are being answered in time."
    return Brief("whispers", "Whether agents get answers", head, text, decision, bool(r["sufficient"]), w)


def handoffs(f: Frames) -> Brief:
    """Whether handoffs are accepted, and how fast."""
    h = _stats.handoffs(f, by=None)
    if h.empty:
        return Brief("handoffs", "Whether handoffs are accepted", "No handoffs on this chain.",
                     "No work changed hands.", "Nothing to change.", False, h)
    r = h.iloc[0]
    head = (f"{_n(r['n'], 'handoff')} proposed, {int(r['accepted'])} accepted ({_pct(r['accept_rate'])}, "
            f"{_interval(r['low'], r['high'])}), {int(r['declined'])} declined, {int(r['open'])} open; "
            f"resolved in a median of {_hours(r['median_response_s'])}.")
    by = _stats.handoffs(f, by="recipient")
    text = ""
    if len(by) > 1:
        worst = by.sort_values("accept_rate").iloc[0]
        text = f"{worst['recipient']} accepts least ({_pct(worst['accept_rate'])} of {int(worst['n'])})."
    if not r["sufficient"]:
        decision = "Too few resolved handoffs to judge; the count will say more as work changes hands."
    elif pd.notna(r["accept_rate"]) and r["accept_rate"] < 0.7:
        decision = "Handoffs are refused often: the shift plan names people who cannot take the work."
    else:
        decision = "Work moves between people when it needs to."
    return Brief("handoffs", "Whether handoffs are accepted", head, text, decision, bool(r["sufficient"]), h)


def assurance(f: Frames) -> Brief:
    """How much of the chain is hash-linked, signed and submitted to a transparency log."""
    e = f.events
    if e.empty:
        return Brief("assurance", "Whether the record holds up", "No entries.", "The chain is empty.",
                     "Nothing to verify.", False, e)
    n = len(e)
    chained = int(e["chained"].fillna(False).sum())
    signed = int(e["signed"].fillna(False).sum())
    sub = int(e["scitt_submitted"].fillna(False).sum())
    head = (f"{_n(n, 'entry', 'entries')}: {_pct(chained / n)} hash-linked, {_pct(signed / n)} signed, "
            f"{_pct(sub / n)} submitted to a transparency log.")
    text = ("A hash-linked entry carries the hash of the one before it, so a change to an earlier entry shows up "
            "when the chain is checked against an independently held head. A signed entry carries its sender's "
            "signature, which a verifier can check against the sender's key. A submitted entry was sent to a "
            "transparency log; the receipt itself lives outside the chain.")
    if chained == n and signed == n and sub == n:
        decision = "Every entry is hash-linked, signed and submitted. Verification is a matter of running the checks."
    elif chained == n:
        decision = "Every entry is hash-linked. Signing and transparency-log submission would add sender attribution and an outside record of the log."
    else:
        decision = "Part of the log is outside the chain: enable the chain at workspace creation so every entry is linked."
    return Brief("assurance", "Whether the record holds up", head, text, decision, True, e,
                 {"n": n, "chained": chained, "signed": signed, "scitt_submitted": sub})


def drift(f: Frames, *, false_alarm_runs: int = 1000) -> Brief:
    """Whether the correction rate has moved since the baseline, from the CUSUM."""
    c = _stats.cusum(f, false_alarm_runs=false_alarm_runs)
    if c.empty:
        return Brief("drift", "Whether the correction rate has moved", "No decided tasks yet.",
                     "Nothing to track.", "Wait for decisions.", False, c)
    alarms = c[c["alarm"]]
    last = c.iloc[-1]
    if alarms.empty:
        head = (f"No alarm over {len(c)} decided tasks; the correction rate has stayed near "
                f"{_pct(last['target'])} (now {_pct(last['rate_so_far'])} cumulative).")
        decision = "Nothing has moved. Keep the chart running."
    else:
        first = alarms.iloc[0]
        head = (f"Alarm at task {int(first['i'])} of {len(c)} ({first['settled_at']:%d %b %H:%M}): the correction rate "
                f"moved above its baseline of {_pct(first['target'])}.")
        decision = "Look at what changed in the prompt, the model or the task mix just before the alarm."
    text = (f"The chart is tuned to notice a rise from {_pct(last['target'])} to {_pct(last['detect'])}, and to "
            f"raise a false alarm about once in every {false_alarm_runs} tasks when nothing has changed.")
    return Brief("drift", "Whether the correction rate has moved", head, text, decision, len(c) >= 50, c,
                 {"alarms": int(len(alarms))})


# ============================================================================
#   Graph findings
# ============================================================================

def concentration(f: Frames) -> Brief:
    """Whether one reviewer decides most of one agent's work."""
    c = _graph.concentration(f)
    cen = _graph.centrality(f)
    if c.empty:
        return Brief("concentration", "Who carries the reviewing", "No decisions yet.",
                     "No reviewer has decided on any agent's work.", "Nothing to balance.", False, c)
    top = c.iloc[0]
    head = (f"{top['top_reviewer']} takes {_pct(top['top_share'])} of the decisions on {top['assignee']}'s work "
            f"({int(top['n_decisions'])} decisions across {int(top['n_reviewers'])} reviewers).")
    text = ""
    if not cen.empty:
        hub = cen.iloc[0]
        text = (f"{hub['participant']} sits between the most work on the collaboration graph "
                f"(betweenness {hub['betweenness']:.2f}, {int(hub['in_weight'])} items in, {int(hub['out_weight'])} out).")
    if top["top_share"] > 0.6 and top["sufficient"]:
        decision = f"One reviewer decides most of {top['assignee']}'s work: spread it, or accept that their standard is the standard."
    else:
        decision = "Reviewing is spread across people."
    return Brief("concentration", "Who carries the reviewing", head, text, decision, bool(top["sufficient"]), c)


def coverage(f: Frames) -> Brief:
    """Whether every outcome that went out had a person on its path."""
    cov = _graph.coverage(f)
    a = cov.attrs
    if a["shipped"] == 0:
        return Brief("coverage", "Whether a person was on the path", "No work has gone out yet.",
                     "No task reached an outcome that ships.", "Nothing to check.", False, cov)
    head = (f"{_pct(a['share'])} of the {a['shipped']} outcomes that went out had a person on the path: "
            f"a human decision on the task or a predecessor, or a person doing the work.")
    if a["uncovered"]:
        text = f"{len(a['uncovered'])} went out with no person on the path: {', '.join(a['uncovered'][:5])}" + \
               (" and more." if len(a["uncovered"]) > 5 else ".")
        decision = "Those tasks shipped on the agent's say-so alone. Require review on that kind, or accept it knowingly."
    else:
        text = "Every outcome that went out passed a person."
        decision = "Oversight coverage is complete for this period."
    return Brief("coverage", "Whether a person was on the path", head, text, decision, True, cov,
                 {"shipped": a["shipped"], "covered": a["covered"], "share": a["share"]})


def duties(f: Frames) -> Brief:
    """Where one actor held two roles that should be separate."""
    d = _graph.duties(f)
    if d.empty:
        return Brief("duties", "Whether duties are separate", "No actor held two roles that should be separate.",
                     "Nobody reviewed their own work, decided what they delegated, or resolved their own handoff, "
                     "and no agent recorded a decision.", "Nothing to change.", True, d)
    counts = d["check"].value_counts()
    head = ", ".join(f"{int(n)} {k.replace('_', ' ')}" for k, n in counts.items()) + "."
    actors = d["actor"].value_counts()
    text = f"Most involve {actors.index[0]} ({int(actors.iloc[0])} findings)."
    if "agent_decided" in counts.index:
        decision = "An agent or service is recording decisions: those reviews are unwatched by a person. Route them to a human."
    else:
        decision = "Separate the roles: a reviewer should be someone other than the delegator and the assignee."
    return Brief("duties", "Whether duties are separate", head, text, decision, True, d)


# ============================================================================
#   All of them
# ============================================================================

def everything(f: Frames, *, threshold: float = 0.10) -> list[Brief]:
    """Every brief, in the order of the decision table."""
    return [
        overrides(f), paths(f), calibration(f), promotion(f, threshold), latency(f),
        agreement(f), whispers(f), handoffs(f), assurance(f), drift(f),
        concentration(f), coverage(f), duties(f),
    ]
