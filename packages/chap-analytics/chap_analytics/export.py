"""
Exports: the chain in the shapes other tools expect.

* :func:`evaluation_cases`: one row per corrected task, with the agent's
  output, the human-corrected output and the rationale, in the shape an
  evaluation harness takes. ``to_jsonl`` writes it as JSON lines.
* :func:`prompt_revision_candidates`: the correction clusters most worth
  addressing, ranked by how often they occur and how often they reversed the
  agent's decision, with example rationales.
* :func:`routing_calibration`: confidence thresholds fitted to observed
  acceptance, with the reliability table that justifies them.

Artefact content is included only where the chain carries it and the caller
did not redact it. The task input is available where the chain was read with
state (a SQLite file or an in-process coordinator); from ``audit.read`` alone
it is null.
"""
from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd

from . import charts as _charts
from . import stats as _stats
from .frames import Frames

__all__ = ["evaluation_cases", "to_jsonl", "prompt_revision_candidates", "routing_calibration"]


def _inputs(f: Frames) -> dict[str, Any]:
    state = f.chain.state or {}
    tasks = state.get("tasks") or {}
    out = {}
    for tid, t in tasks.items():
        if isinstance(t, dict):
            out[tid] = t.get("input")
    return out


def evaluation_cases(f: Frames, *, include_approved: bool = False) -> pd.DataFrame:
    """
    One row per corrected task: ``input`` (where the chain carries state),
    ``agent_output`` (the artefact under review), ``corrected_output`` (the
    patch applied), ``rationale``, ``tags``, ``intent_preserved``, and who
    decided. With ``include_approved`` the approved tasks are added with the
    corrected output equal to the agent's, so a harness can score acceptance
    as well as correction.
    """
    inputs = _inputs(f)
    o = f.overrides
    cols = ["task_id", "kind", "assignee", "reviewer", "ts", "input", "agent_output", "corrected_output",
            "rationale", "tags", "policy_refs", "intent_preserved", "outcome"]
    rows = []
    for r in o.itertuples(index=False):
        rows.append({"task_id": r.task_id, "kind": r.task_kind, "assignee": r.assignee, "reviewer": r.reviewer,
                     "ts": r.ts, "input": inputs.get(r.task_id), "agent_output": r.based_on,
                     "corrected_output": r.result, "rationale": r.rationale,
                     "tags": list(r.tags) if isinstance(r.tags, list) else [],
                     "policy_refs": list(r.policy_refs) if isinstance(r.policy_refs, list) else [],
                     "intent_preserved": r.intent_preserved, "outcome": "overridden"})
    if include_approved and not f.decisions.empty:
        approved = f.tasks[f.tasks["outcome"] == "approved"]
        settle = _stats._settling_decisions(f).set_index("task_id")
        # An approval carries no artefact of its own: the agent's output was
        # accepted as drafted, so both output columns stay empty on these rows.
        for t in approved.itertuples(index=False):
            reviewer = settle["reviewer"].get(t.task_id) if t.task_id in settle.index else None
            rows.append({"task_id": t.task_id, "kind": t.kind, "assignee": t.assignee, "reviewer": reviewer,
                         "ts": t.settled_at, "input": inputs.get(t.task_id), "agent_output": None,
                         "corrected_output": None, "rationale": None, "tags": [], "policy_refs": [],
                         "intent_preserved": None, "outcome": "approved"})
    out = pd.DataFrame(rows, columns=cols)
    return out.sort_values("ts").reset_index(drop=True) if not out.empty else out


def to_jsonl(cases: pd.DataFrame, path: str) -> str:
    """Write :func:`evaluation_cases` as JSON lines and return the path."""
    with open(path, "w", encoding="utf-8") as fh:
        for row in cases.itertuples(index=False):
            fh.write(json.dumps({c: _charts._plain(v) for c, v in zip(cases.columns, row)}, ensure_ascii=False) + "\n")
    return path


def prompt_revision_candidates(f: Frames, *, top: int = 10, examples: int = 3) -> pd.DataFrame:
    """
    Correction clusters ranked for attention: one row per (task kind, part of
    the artefact, tag), with how many corrections it covers, the share of
    those that reversed the agent's decision, and example rationales.

    ``priority`` is the count weighted by the reversing share plus one, so a
    cluster that keeps reversing decisions ranks above one of the same size
    that only rewords.
    """
    o = f.overrides.copy()
    cols = ["task_kind", "top_path", "tag", "n", "reversing", "reversing_share", "priority", "examples"]
    if o.empty:
        return pd.DataFrame(columns=cols)
    o["tags"] = o["tags"].apply(lambda v: v if isinstance(v, list) and v else ["(untagged)"])
    ex = o.explode("tags").rename(columns={"tags": "tag"})
    rows = []
    for (kind, path, tag), part in ex.groupby(["task_kind", "top_path", "tag"], dropna=False):
        n = len(part)
        rev = int((part["intent_preserved"] == False).sum())  # noqa: E712
        stated = int(part["intent_preserved"].notna().sum())
        share = rev / stated if stated else float("nan")
        rats = [r for r in part["rationale"].dropna().astype(str).tolist() if r][:examples]
        rows.append({"task_kind": kind, "top_path": path, "tag": tag, "n": n, "reversing": rev,
                     "reversing_share": share, "priority": n * (1 + (share if stated else 0)), "examples": rats})
    out = pd.DataFrame(rows, columns=cols).sort_values("priority", ascending=False).reset_index(drop=True)
    return out.head(top)


def routing_calibration(f: Frames, *, target_acceptance: float = 0.90, bins: int = 10,
                        minimum: int = 20) -> dict[str, Any]:
    """
    A confidence threshold fitted to what reviewers accepted.

    Returns ``threshold``: the lowest reported confidence at and above which
    the observed acceptance reaches ``target_acceptance``; ``acceptance_above``
    and ``n_above``, what that threshold delivers; ``table``, the reliability
    table it was read from; and ``sufficient``. ``threshold`` is None where
    no level of reported confidence reaches the target, which is itself the
    finding: route everything to review, or fix the agent's confidence first.
    """
    cal = _stats.calibration(f, bins=bins, minimum=minimum)
    rows = _stats._calibration_rows(f)
    out: dict[str, Any] = {"target_acceptance": target_acceptance, "threshold": None, "acceptance_above": None,
                           "n_above": 0, "table": cal, "sufficient": bool(cal.attrs.get("sufficient", False))}
    if rows.empty:
        return out
    conf = rows["confidence"].to_numpy(dtype=float)
    acc = rows["accepted"].to_numpy(dtype=float)
    for level in np.round(np.arange(0.0, 1.0001, 0.05), 2):
        mask = conf >= level
        if mask.sum() == 0:
            break
        share = float(acc[mask].mean())
        if share >= target_acceptance and int(mask.sum()) >= 5:
            out.update({"threshold": float(level), "acceptance_above": share, "n_above": int(mask.sum())})
            break
    return out
