"""
A week at a support desk, as the analytics layer sees it.

An agent drafts replies to customer tickets and three people review them. Over the
week they approve most, correct some and send a few back. One declares a
conflict of interest, work changes hands at a shift change, the agent asks two
questions and gets one answer, a policy exception goes to a vote, and one
ticket is escalated to legal. Every one of those actions is a CHAP envelope.
This script generates the week with ``chap_analytics.sample``, reads the chain
back both ways, and prints the analyses the tables make routine.

Each section is a count or a median, with a line on what it is for. The
sample is small and the script says so. A week of ordinary review work produced
all of it as a side effect.

    pip install 'chap-analytics[coordinator]'
    python support_desk.py
    python support_desk.py --export week.json   # the audit.read result, for from_json()

The workspace is generated on a simulated clock, so the output reflects the
coordinator that is installed and is the same for the same seed.
"""
from __future__ import annotations

import argparse
import json
import sys

import pandas as pd

from chap_analytics import Frames, frames, from_coordinator, redact_artefacts
from chap_analytics.load import Chain
from chap_analytics.sample import WORKSPACE, support_desk_coordinator


def section(title: str, informs: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}\n{informs}\n")


def show(df: pd.DataFrame) -> None:
    print(df.to_string(index=False) if len(df) else "  (empty)")


def report(f: Frames) -> None:  # noqa: C901 - one block per section
    tasks, decisions, overrides = f.tasks, f.decisions, f.overrides
    drafts = tasks[tasks["kind"] == "draft_reply"]

    print("A week at the support desk")
    print("==========================")
    print(f.summary())
    kinds = tasks["kind"].value_counts()
    print(f"\n{len(tasks)} tasks: " + ", ".join(f"{n} {k}" for k, n in kinds.items())
          + f". {int(tasks['settled'].sum())} settled, {int((~tasks['settled']).sum())} still open; "
          f"{decisions['reviewer'].nunique()} people decided.")
    print("Counts and medians only. A week is too little for a confidence interval.")

    section("1. How the week ended",
            "The overridden share is the supervision signal. The rest is throughput.")
    show(drafts["outcome"].value_counts().rename_axis("outcome").reset_index(name="tasks"))

    section("2. What reviewers keep correcting",
            "A field that dominates is a prompt to revise.")
    show(f.patch_ops.groupby("top_path").size().rename("corrections").reset_index())

    section("3. Why they corrected it",
            "The top tag names the next prompt change. A policy reference names the policy the prompt should cite.")
    tags = overrides.explode("tags")["tags"].value_counts().rename_axis("tag").reset_index(name="overrides")
    show(tags)
    refs = overrides.explode("policy_refs")["policy_refs"].dropna()
    if len(refs):
        print(f"\nPolicies invoked: {', '.join(sorted(refs.unique()))}")

    section("4. Refining the draft, or reversing it",
            "A refinement keeps the draft's decision and changes how it is expressed or supported. "
            "A reversal substitutes a different decision.")
    split = overrides["intent_preserved"].map({True: "refined", False: "reversed"}).fillna("unsaid")
    show(split.value_counts().rename_axis("edit").reset_index(name="overrides"))

    section("5. Does the agent's confidence mean anything?",
            "Overridden drafts should be the less confident ones. Otherwise the thresholds are decoration.")
    judged = drafts[drafts["outcome"].isin(["approved", "overridden"]) & drafts["confidence"].notna()]
    by = (judged.groupby("outcome")["confidence"].agg(["count", "median", "min", "max"]).round(2)
          .rename(columns={"count": "n"}).reset_index())
    show(by)
    print("\n  Reliability diagrams and a Brier score are stage 3 of the roadmap. With a "
          "week of data the two medians are what can be said.")

    section("6. The reviewers",
            "A reviewer who overrides far more than the others is either stricter or getting the harder work. "
            "Separating the two is stage 3 of the roadmap.")
    league = f.participants[f.participants["kind"] == "human"][
        ["participant", "n_decisions", "n_overrides", "n_abstentions"]].copy()
    lat = decisions.groupby("reviewer")["latency_s"].median().div(60).round(0).rename("median_minutes")
    league = league.merge(lat, left_on="participant", right_index=True, how="left")
    show(league)

    section("7. Time to a decision",
            "Elapsed time rather than effort. Open work is censored.")
    final = decisions[decisions["is_final"]]["latency_s"].div(60)
    print(f"  {len(final)} reviews settled; median {final.median():.0f} min, "
          f"90th percentile {final.quantile(0.9):.0f} min.")
    print(f"  {int((~tasks['settled']).sum())} tasks still open at the end of the week, "
          f"excluded from every figure above.")
    passes = tasks[tasks["n_reviews"] > 1]
    print(f"  {len(passes)} task(s) were sent back and reviewed again; each pass is measured "
          f"from its own opening.")

    section("8. Questions and handoffs",
            "A lapse means the agent's default stood unreviewed. A declined handoff means the proposer misread who should take the work.")
    w = f.whispers
    print(f"  Whispers asked: {len(w)}, answered {int(w['answered'].sum())}, lapsed {int(w['lapsed'].sum())}"
          + (f"; median response {w['response_s'].median() / 60:.0f} min." if w["answered"].any() else "."))
    for _, row in f.handoffs.iterrows():
        print(f"  Handoff {row['proposer']} -> {row['recipient']}: {row['resolution']}, "
              f"{row['n_accepted']}/{row['n_tasks']} tasks taken, after {row['response_s'] / 60:.0f} min.")
    for _, row in f.deliberations.iterrows():
        print(f"  Deliberation \"{row['question']}\": {row['n_yea']} for, {row['n_nay']} against, "
              f"turnout {row['turnout']:.0%}, outcome {row['outcome'] or 'unavailable from this source'}.")
    escalated = tasks[tasks["outcome"] == "escalated"]
    successors = tasks[tasks["supersedes"].notna()]["assignee"].astype(str)
    print(f"  Escalated: {len(escalated)} (to {', '.join(successors)}).")


def compare_reads(state: Frames, envelopes: Frames) -> None:
    section("9. The same tables from audit.read alone",
            "An MCP client holds the envelopes. Everything above is available to it, "
            "and the rows whose id is inferred say so.")
    same = (state.tasks["outcome"].value_counts().to_dict()
            == envelopes.tasks["outcome"].value_counts().to_dict())
    print(f"  Outcome counts identical across the two reads: {same}")
    print(f"  Override count: {len(state.overrides)} with state, {len(envelopes.overrides)} from envelopes")
    for name in ("tasks", "whispers", "deliberations", "handoffs"):
        df = envelopes[name]
        print(f"  {name:14} {int(df['id_certain'].sum())}/{len(df)} rows identified beyond doubt from envelopes")
    print("  With state the pairing is settled by what the two records agree on. "
          "From envelopes it is settled where the order of events allows, and flagged elsewhere.")


def show_redaction(coord, intact: Frames) -> None:
    section("10. With the content redacted",
            "Whoever runs the analysis can be kept from the customer's message. The shape survives.")
    red = frames(from_coordinator(coord, workspace=WORKSPACE, redact=redact_artefacts))
    print(f"  Overrides: {len(red.overrides)} redacted, {len(intact.overrides)} intact")
    print(f"  Patch paths kept: {sorted(red.patch_ops['top_path'].unique())}")
    print(f"  Tags kept: {sorted(red.overrides.explode('tags')['tags'].unique())}")
    print(f"  based_on on the first override: {red.overrides.iloc[0]['based_on']!r} "
          f"(was a {type(intact.overrides.iloc[0]['based_on']).__name__})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--export", metavar="PATH",
                        help="also write the audit.read result to a JSON file, to load later with from_json()")
    args = parser.parse_args(argv)

    coord = support_desk_coordinator(args.seed)
    entries = coord.dispatch({"jsonrpc": "2.0", "id": "audit.read", "method": "audit.read",
                              "params": {"workspace": WORKSPACE, "from": "human:maya"}})["result"]["entries"]
    with_state = frames(from_coordinator(coord, workspace=WORKSPACE))
    from_envelopes = frames(Chain(workspace=WORKSPACE, events=entries, state=None, source="audit.read"))

    pd.set_option("display.width", 120)
    report(with_state)
    compare_reads(with_state, from_envelopes)
    show_redaction(coord, with_state)

    if args.export:
        with open(args.export, "w", encoding="utf-8") as fh:
            json.dump({"workspace": WORKSPACE, "entries": entries}, fh, indent=1)
        print(f"\nWrote {len(entries)} audit entries to {args.export}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
