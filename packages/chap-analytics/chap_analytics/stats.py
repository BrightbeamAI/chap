"""
The statistics behind each decision in the roadmap's table.

Every function takes a :class:`~chap_analytics.frames.Frames` and returns a
tidy DataFrame: one row per group, with the estimate, its interval, the sample
size behind it, and a ``sufficient`` flag that is False where the sample is
below the minimum the statistic needs; the frame's ``attrs["minimum"]`` says
what that minimum was. A function that lacks the data to say anything
returns its columns and no rows rather than a guess.

Intervals on rates are Wilson intervals. Where a count is small, the promotion
functions show a posterior rather than a point estimate. Time to decision is a
survival function, so open reviews count as censored rather than being
dropped. Everything here uses numpy and pandas alone.

Grain and denominators:

* A *decided* task is one whose last review pass ended in a reviewer's
  decision: ``outcome`` is approved, overridden, rejected or abstained. Rates
  are shares of decided tasks. Tasks that were escalated, cancelled, superseded,
  completed without a decision or still open are outside the denominator and
  reported by :func:`outcomes`.
* ``by`` names a column of the ``tasks`` table (``kind``, ``assignee``,
  ``mode``, ``criticality``, ``risk_tier``), or ``"reviewer"`` to group by the
  reviewer who settled the task. Tags label corrections rather than tasks, so
  they have their own function, :func:`tags`.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from .frames import Frames

__all__ = [
    "wilson", "outcomes", "rates", "rates_over_time", "refine_reverse", "tags",
    "patch_paths", "path_rationales", "calibration", "calibration_summary",
    "latency", "survival", "latency_by", "open_queue", "promotion",
    "sequential", "agreement", "vote_agreement", "pairwise_agreement", "abstentions",
    "whispers", "handoffs", "assurance", "cusum", "DECIDED",
]

#: Outcomes that count as a reviewer's decision on the task's last review pass.
DECIDED = ("approved", "overridden", "rejected", "abstained")

_Z = 1.959963984540054  # two-sided 95%

_RATE_OUTCOMES = (("approve", "approved"), ("override", "overridden"),
                  ("reject", "rejected"), ("abstain", "abstained"))


# ============================================================================
#   Intervals and special functions
# ============================================================================

def wilson(k: int | np.ndarray, n: int | np.ndarray, z: float = _Z) -> tuple[Any, Any]:
    """
    Wilson score interval for ``k`` successes in ``n`` trials.

    Returns ``(low, high)``. For ``n == 0`` both bounds are NaN. Vectorised over
    numpy arrays and pandas Series.
    """
    k = np.asarray(k, dtype=float)
    n = np.asarray(n, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        p = np.where(n > 0, k / np.where(n > 0, n, 1), np.nan)
        denom = 1 + z * z / n
        centre = (p + z * z / (2 * n)) / denom
        half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
        low = np.where(n > 0, np.clip(centre - half, 0, 1), np.nan)
        high = np.where(n > 0, np.clip(centre + half, 0, 1), np.nan)
    if low.ndim == 0:
        return float(low), float(high)
    return low, high


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (Lentz's method)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / (c if abs(c) > tiny else tiny)
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / (c if abs(c) > tiny else tiny)
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-14:
            break
    return h


def beta_cdf(x: float, a: float, b: float) -> float:
    """Regularised incomplete beta function I_x(a, b), the CDF of Beta(a, b)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log(1 - x))
    if x < (a + 1) / (a + b + 2):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1 - x) / b


def beta_quantile(q: float, a: float, b: float) -> float:
    """Inverse of :func:`beta_cdf` by bisection, to 1e-9."""
    lo, hi = 0.0, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if beta_cdf(mid, a, b) < q:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# ============================================================================
#   Grouping helpers
# ============================================================================

def _decided(f: Frames) -> pd.DataFrame:
    t = f.tasks
    return t[t["outcome"].isin(DECIDED)].copy()


def _settling_decisions(f: Frames) -> pd.DataFrame:
    """The decision that settled each decided task's last review pass."""
    d = f.decisions
    if d.empty:
        return d.copy()
    final = d[d["is_final"].fillna(False)]
    # One settling decision per task: the last final decision by sequence.
    final = final.sort_values(["task_id", "seq"]).groupby("task_id", as_index=False).tail(1)
    return final


def _with_group(f: Frames, tasks: pd.DataFrame, by: str | None) -> tuple[pd.DataFrame, list[str]]:
    """Attach the grouping column(s) to a task-grain frame."""
    if by is None:
        return tasks.assign(_all="all"), ["_all"]
    if by in tasks.columns:
        return tasks, [by]
    if by == "reviewer":
        settle = _settling_decisions(f)[["task_id", "reviewer"]]
        return tasks.merge(settle, on="task_id", how="left"), ["reviewer"]
    if by == "tag":
        raise KeyError("A tag labels a correction rather than a task, so a rate per tag "
                       "reads as one by construction. Use tags() for how often each tag "
                       "occurs and refine_reverse(by='tag') for what the tag's corrections did.")
    raise KeyError(f"Unknown grouping {by!r}. Use a tasks column or 'reviewer'.")


_PERIOD_ALIASES = {"MS": "M", "QS": "Q", "YS": "Y", "AS": "Y", "W-SUN": "W"}


def _period_start(ts: pd.Series, freq: str) -> pd.Series:
    """Start of the calendar period holding each timestamp, kept in UTC."""
    freq = _PERIOD_ALIASES.get(freq, freq)
    naive = ts.dt.tz_convert("UTC").dt.tz_localize(None)
    return naive.dt.to_period(freq).dt.start_time.dt.tz_localize("UTC")


def _finish(out: pd.DataFrame, groups: list[str]) -> pd.DataFrame:
    if groups == ["_all"]:
        out = out.drop(columns=["_all"])
    return out.reset_index(drop=True)


# ============================================================================
#   Rates
# ============================================================================

def _with_minimum(df: pd.DataFrame, minimum: int) -> pd.DataFrame:
    """Record the minimum ``sufficient`` was judged against, in ``attrs``."""
    df.attrs["minimum"] = int(minimum)
    return df


def outcomes(f: Frames, by: str | None = None) -> pd.DataFrame:
    """
    Every task by outcome, so the denominators used elsewhere are visible.

    One row per group and outcome, with the count and its share of the group.
    """
    t, groups = _with_group(f, f.tasks.copy(), by)
    t["outcome"] = t["outcome"].fillna("unknown")
    out = t.groupby(groups + ["outcome"], dropna=False).size().rename("n").reset_index()
    totals = out.groupby(groups)["n"].transform("sum")
    out["share"] = out["n"] / totals
    return _finish(out, groups)


def rates(f: Frames, by: str | None = None, *, minimum: int = 10) -> pd.DataFrame:
    """
    Approval, override, rejection and abstention rates among decided tasks.

    Each rate carries a Wilson interval (``*_low``, ``*_high``). ``sufficient``
    is False below ``minimum`` decided tasks in the group; the numbers are
    still shown, and the interval says how little they pin down.
    """
    t, groups = _with_group(f, _decided(f), by)
    if t.empty:
        cols = [g for g in groups if g != "_all"] + ["n"]
        for name, outcome in _RATE_OUTCOMES:
            cols += [outcome, f"{name}_rate", f"{name}_low", f"{name}_high"]
        return _with_minimum(pd.DataFrame(columns=cols + ["sufficient"]), minimum)
    g = t.groupby(groups, dropna=False)
    out = g.size().rename("n").to_frame()
    for name, outcome in _RATE_OUTCOMES:
        count = g["outcome"].apply(lambda s, o=outcome: int((s == o).sum()))
        out[outcome] = count
        out[f"{name}_rate"] = count / out["n"]
        low, high = wilson(count.to_numpy(), out["n"].to_numpy())
        out[f"{name}_low"], out[f"{name}_high"] = low, high
    out["sufficient"] = out["n"] >= minimum
    return _with_minimum(_finish(out.reset_index(), groups), minimum)


def rates_over_time(f: Frames, freq: str = "W", by: str | None = None,
                    *, minimum: int = 10, when: str = "settled_at") -> pd.DataFrame:
    """
    :func:`rates` per calendar period, bucketed on ``settled_at`` by default.

    ``freq`` is a pandas offset alias: ``D``, ``W``, ``MS``.
    """
    t, groups = _with_group(f, _decided(f), by)
    if t.empty:
        return _with_minimum(pd.DataFrame(columns=["period"] + [g for g in groups if g != "_all"] + ["n"]), minimum)
    t = t.dropna(subset=[when])
    t["period"] = _period_start(t[when], freq)
    g = t.groupby(["period"] + groups, dropna=False)
    out = g.size().rename("n").to_frame()
    for name, outcome in _RATE_OUTCOMES:
        count = g["outcome"].apply(lambda s, o=outcome: int((s == o).sum()))
        out[f"{name}_rate"] = count / out["n"]
        out[f"{name}_low"], out[f"{name}_high"] = wilson(count.to_numpy(), out["n"].to_numpy())
    out["sufficient"] = out["n"] >= minimum
    return _with_minimum(_finish(out.reset_index(), groups), minimum)


def refine_reverse(f: Frames, by: str | None = None, *, minimum: int = 10) -> pd.DataFrame:
    """
    Among overrides, the share that refined the agent's decision rather than
    reversing it, from ``intent_preserved``.

    ``unstated`` counts overrides where the client left the flag unset; the
    share is computed over the stated ones and the interval reflects that.
    """
    o = f.overrides.copy()
    if by is None:
        o["_all"] = "all"
        groups = ["_all"]
    elif by == "reviewer":
        groups = ["reviewer"]
    elif by == "tag":
        o["tags"] = o["tags"].apply(lambda v: v if isinstance(v, list) and v else [None])
        o = o.explode("tags").rename(columns={"tags": "tag"})
        groups = ["tag"]
    elif by in ("kind", "task_kind"):
        groups = ["task_kind"]
    elif by in o.columns:
        groups = [by]
    else:
        # A tasks column: join it in.
        o = o.merge(f.tasks[["task_id", by]], on="task_id", how="left")
        groups = [by]
    if o.empty:
        return _with_minimum(pd.DataFrame(columns=[g for g in groups if g != "_all"] +
                            ["n_overrides", "refining", "reversing", "unstated",
                             "refine_share", "low", "high", "sufficient"]), minimum)
    g = o.groupby(groups, dropna=False)
    out = g.size().rename("n_overrides").to_frame()
    out["refining"] = g["intent_preserved"].apply(lambda s: int((s == True).sum()))  # noqa: E712
    out["reversing"] = g["intent_preserved"].apply(lambda s: int((s == False).sum()))  # noqa: E712
    out["unstated"] = out["n_overrides"] - out["refining"] - out["reversing"]
    stated = out["refining"] + out["reversing"]
    out["refine_share"] = np.where(stated > 0, out["refining"] / stated.where(stated > 0, 1), np.nan)
    out["low"], out["high"] = wilson(out["refining"].to_numpy(), stated.to_numpy())
    out["sufficient"] = stated >= minimum
    return _with_minimum(_finish(out.reset_index(), groups), minimum)


def tags(f: Frames, by: str | None = None) -> pd.DataFrame:
    """
    How often each tag is used on overrides, with the share of the group's
    overrides carrying it and, where stated, the share of those that refined
    rather than reversed.
    """
    o = f.overrides.copy()
    cols = ([by] if by else []) + ["tag", "n", "overrides", "share", "refining", "reversing", "refine_share"]
    if o.empty:
        return pd.DataFrame(columns=cols)
    if by and by not in o.columns:
        o = o.merge(f.tasks[["task_id", by]], on="task_id", how="left")
    groups = [by] if by else []
    o["tags"] = o["tags"].apply(lambda v: v if isinstance(v, list) and v else ["(untagged)"])
    ex = o.explode("tags").rename(columns={"tags": "tag"})
    g = ex.groupby(groups + ["tag"], dropna=False)
    out = pd.DataFrame({
        "n": g.size(),
        "refining": g["intent_preserved"].apply(lambda s: int((s == True).sum())),  # noqa: E712
        "reversing": g["intent_preserved"].apply(lambda s: int((s == False).sum())),  # noqa: E712
    }).reset_index()
    totals = o.groupby(groups, dropna=False).size().rename("overrides").reset_index() if groups \
        else pd.DataFrame({"overrides": [len(o)]})
    out = out.merge(totals, on=groups, how="left") if groups else out.assign(overrides=len(o))
    out["share"] = out["n"] / out["overrides"]
    stated = out["refining"] + out["reversing"]
    out["refine_share"] = np.where(stated > 0, out["refining"] / stated.where(stated > 0, 1), np.nan)
    return out.sort_values(groups + ["n"], ascending=[True] * len(groups) + [False]).reset_index(drop=True)[cols]


# ============================================================================
#   Where the corrections land
# ============================================================================

def patch_paths(f: Frames, by: str | None = "task_kind", *, top: int | None = None,
                depth: str = "top_path") -> pd.DataFrame:
    """
    How often each part of the artefact is corrected, per group.

    Counts overrides touching each path (an override touching a path twice
    counts once). ``share`` is the share of the group's overrides that touched
    the path, so it reads as "N in ten corrections of this kind edit the reply".
    ``depth`` is ``top_path`` for the first segment or ``path`` for the full
    JSON Pointer.
    """
    ops = f.patch_ops
    if ops.empty:
        return pd.DataFrame(columns=([by] if by else []) + [depth, "n", "share"])
    if by == "task_kind":
        ops = ops.merge(f.overrides[["task_id", "seq", "task_kind"]], on=["task_id", "seq"], how="left")
    elif by and by not in ops.columns:
        ops = ops.merge(f.tasks[["task_id", by]], on="task_id", how="left")
    groups = [by] if by else []
    touched = ops.drop_duplicates(subset=groups + ["task_id", "seq", depth])
    out = touched.groupby(groups + [depth], dropna=False).size().rename("n").reset_index()
    per_group = touched.drop_duplicates(subset=groups + ["task_id", "seq"]).groupby(groups, dropna=False).size() if groups \
        else pd.Series({(): len(touched.drop_duplicates(subset=["task_id", "seq"]))})
    if groups:
        out = out.merge(per_group.rename("overrides").reset_index(), on=groups, how="left")
    else:
        out["overrides"] = len(touched.drop_duplicates(subset=["task_id", "seq"]))
    out["share"] = out["n"] / out["overrides"]
    out = out.sort_values(groups + ["n"], ascending=[True] * len(groups) + [False])
    if top:
        out = out.groupby(groups, group_keys=False).head(top) if groups else out.head(top)
    return out.reset_index(drop=True)


def path_rationales(f: Frames, path: str | None = None, kind: str | None = None) -> pd.DataFrame:
    """
    The overrides behind a cell of the path heatmap: who corrected what, and
    the rationale they gave. ``path`` matches ``top_path``.
    """
    o = f.overrides
    if path is not None:
        o = o[o["top_path"] == path]
    if kind is not None:
        o = o[o["task_kind"] == kind]
    cols = ["task_id", "task_kind", "reviewer", "ts", "top_path", "paths", "intent_preserved",
            "tags", "policy_refs", "rationale"]
    return o[cols].sort_values("ts").reset_index(drop=True)


# ============================================================================
#   Calibration
# ============================================================================

def _calibration_rows(f: Frames) -> pd.DataFrame:
    t = _decided(f)
    t = t.dropna(subset=["confidence"])
    t = t[t["outcome"].isin(("approved", "overridden", "rejected"))]
    t = t.assign(accepted=(t["outcome"] == "approved").astype(float))
    return t[["task_id", "confidence", "accepted", "assignee", "kind", "mode"]]


def calibration(f: Frames, bins: int = 10, *, minimum: int = 30) -> pd.DataFrame:
    """
    Reliability table: reported confidence against the share of tasks the
    reviewers accepted as they were.

    One row per confidence bin with the count, the mean reported confidence,
    the observed acceptance share and its Wilson interval. Abstentions are set
    aside since they are neither acceptance nor correction. The frame's
    ``attrs`` carry ``n``, ``ece`` (expected calibration error), ``brier`` and
    ``sufficient``; :func:`calibration_summary` returns the same as a dict.
    """
    rows = _calibration_rows(f)
    edges = np.linspace(0, 1, bins + 1)
    if rows.empty:
        out = pd.DataFrame(columns=["bin", "low_edge", "high_edge", "n", "mean_confidence",
                                    "accepted", "acceptance", "low", "high"])
        out.attrs.update({"n": 0, "ece": float("nan"), "brier": float("nan"), "sufficient": False})
        return _with_minimum(out, minimum)
    idx = np.clip(np.digitize(rows["confidence"].to_numpy(), edges[1:-1], right=True), 0, bins - 1)
    rows = rows.assign(bin=idx)
    g = rows.groupby("bin")
    out = pd.DataFrame({
        "bin": range(bins),
        "low_edge": edges[:-1], "high_edge": edges[1:],
    })
    agg = g.agg(n=("accepted", "size"), mean_confidence=("confidence", "mean"),
                accepted=("accepted", "sum"))
    out = out.merge(agg, left_on="bin", right_index=True, how="left")
    out["n"] = out["n"].fillna(0).astype(int)
    out["accepted"] = out["accepted"].fillna(0).astype(int)
    out["acceptance"] = np.where(out["n"] > 0, out["accepted"] / out["n"].where(out["n"] > 0, 1), np.nan)
    out["low"], out["high"] = wilson(out["accepted"].to_numpy(), out["n"].to_numpy())
    n = int(len(rows))
    filled = out[out["n"] > 0]
    ece = float((filled["n"] / n * (filled["acceptance"] - filled["mean_confidence"]).abs()).sum())
    brier = float(((rows["confidence"] - rows["accepted"]) ** 2).mean())
    out.attrs.update({"n": n, "ece": ece, "brier": brier, "sufficient": n >= minimum})
    return _with_minimum(out, minimum)


def calibration_summary(f: Frames, bins: int = 10, *, minimum: int = 30) -> dict[str, Any]:
    """``n``, ``ece``, ``brier`` and ``sufficient`` for the whole workspace."""
    return dict(calibration(f, bins, minimum=minimum).attrs)


# ============================================================================
#   Time to decision
# ============================================================================

def _chain_end(f: Frames) -> pd.Timestamp | None:
    ts = f.events["ts"].dropna()
    return ts.max() if not ts.empty else None


def latency(f: Frames) -> pd.DataFrame:
    """
    One row per review pass: when it opened, how long until its first
    decision, and whether it is still open (``event`` False, ``duration_s``
    measured to the end of the chain).

    Open passes are found from the last ``review.request`` or ``task.complete``
    envelope on a task that is still awaiting a decision.
    """
    d = f.decisions
    end = _chain_end(f)
    rows: list[dict] = []
    if not d.empty:
        first = (d.sort_values("ts").groupby(["task_id", "review_index"], as_index=False)
                 .first()[["task_id", "review_index", "requested_at", "ts", "latency_s", "reviewer", "task_kind", "assignee"]])
        for r in first.itertuples(index=False):
            rows.append({"task_id": r.task_id, "review_index": r.review_index,
                         "requested_at": r.requested_at, "decided_at": r.ts,
                         "duration_s": r.latency_s, "event": True,
                         "reviewer": r.reviewer, "task_kind": r.task_kind, "assignee": r.assignee})
    open_tasks = f.tasks[(f.tasks["outcome"] == "open") & f.tasks["was_reviewed"].fillna(False)]
    if not open_tasks.empty and end is not None:
        ev = f.events[f.events["method"].isin(("review.request", "task.complete"))]
        last_req = ev.sort_values("ts").groupby("task_id")["ts"].last()
        decided_passes = {(r.task_id, r.review_index) for r in first.itertuples(index=False)} if not d.empty else set()
        for t in open_tasks.itertuples(index=False):
            n_rev = int(t.n_reviews) if pd.notna(t.n_reviews) else 1
            idx = max(0, n_rev - 1)
            if (t.task_id, idx) in decided_passes:
                continue
            req = last_req.get(t.task_id)
            if req is None or pd.isna(req):
                continue
            rows.append({"task_id": t.task_id, "review_index": idx, "requested_at": req,
                         "decided_at": pd.NaT, "duration_s": (end - req).total_seconds(),
                         "event": False, "reviewer": None, "task_kind": t.kind,
                         "assignee": t.assignee})
    cols = ["task_id", "review_index", "requested_at", "decided_at", "duration_s", "event",
            "reviewer", "task_kind", "assignee"]
    out = pd.DataFrame(rows, columns=cols)
    return out.sort_values(["requested_at", "task_id"]).reset_index(drop=True)


def survival(f: Frames, by: str | None = None) -> pd.DataFrame:
    """
    Kaplan-Meier estimate of the time a review waits for its first decision.

    Open reviews are censored at the end of the chain rather than dropped, so
    a workspace with a long tail of waiting work shows it. ``survival`` at a
    time is the share of reviews still undecided after that long; ``low`` and
    ``high`` are Greenwood 95% bounds. Group with ``by`` on ``reviewer``,
    ``task_kind`` or ``assignee``.
    """
    lt = latency(f)
    groups = [by] if by else []
    cols = groups + ["time_s", "at_risk", "events", "censored", "survival", "low", "high"]
    if lt.empty:
        return pd.DataFrame(columns=cols)
    out_rows: list[dict] = []
    for key, part in (lt.groupby(by, dropna=False) if by else [(None, lt)]):
        part = part.dropna(subset=["duration_s"]).sort_values("duration_s")
        s, var_sum = 1.0, 0.0
        times = sorted(part["duration_s"].unique())
        for t in times:
            at = part[part["duration_s"] >= t]
            at_risk = len(at)
            here = part[part["duration_s"] == t]
            d = int(here["event"].sum())
            c = int((~here["event"]).sum())
            if d > 0 and at_risk > 0:
                s *= 1 - d / at_risk
                if at_risk > d:
                    var_sum += d / (at_risk * (at_risk - d))
            se = s * math.sqrt(var_sum) if s > 0 else 0.0
            row = {"time_s": float(t), "at_risk": at_risk, "events": d, "censored": c,
                   "survival": s, "low": max(0.0, s - _Z * se), "high": min(1.0, s + _Z * se)}
            if by:
                row[by] = key
            out_rows.append(row)
    return pd.DataFrame(out_rows, columns=cols)


def latency_by(f: Frames, by: str = "reviewer", *, minimum: int = 5) -> pd.DataFrame:
    """
    Time to first decision per group, on decided passes: count, median, 90th
    percentile and mean, in seconds, with the number of passes still open.
    """
    lt = latency(f)
    cols = [by, "n", "open", "median_s", "p90_s", "mean_s", "sufficient"]
    if lt.empty:
        return _with_minimum(pd.DataFrame(columns=cols), minimum)
    decided = lt[lt["event"]]
    g = decided.groupby(by, dropna=False)["duration_s"]
    out = pd.DataFrame({
        "n": g.size(),
        "median_s": g.median(),
        "p90_s": g.quantile(0.9),
        "mean_s": g.mean(),
    })
    opened = lt[~lt["event"]].groupby(by, dropna=False).size().rename("open")
    out = out.join(opened, how="outer")
    out["open"] = out["open"].fillna(0).astype(int)
    out["n"] = out["n"].fillna(0).astype(int)
    out["sufficient"] = out["n"] >= minimum
    return _with_minimum(out.reset_index().rename(columns={"index": by})[cols], minimum)


def open_queue(f: Frames) -> pd.DataFrame:
    """Reviews still waiting, oldest first, with their age at the end of the chain."""
    lt = latency(f)
    cols = ["task_id", "task_kind", "assignee", "requested_at", "age_s"]
    if lt.empty:
        return pd.DataFrame(columns=cols)
    q = lt[~lt["event"].astype(bool)].rename(columns={"duration_s": "age_s"})
    return q[cols].sort_values("age_s", ascending=False).reset_index(drop=True)


# ============================================================================
#   Promotion readiness
# ============================================================================

def promotion(f: Frames, threshold: float = 0.10, by: str | None = "assignee", *,
              mode: str | None = None, prior: tuple[float, float] = (1.0, 1.0),
              credible: float = 0.90, minimum: int = 20,
              counting: str = "reversing") -> pd.DataFrame:
    """
    Is an agent's rate of substantive correction below ``threshold``?

    ``counting`` picks what counts against the agent: ``reversing`` for
    overrides with ``intent_preserved`` False plus rejections, ``changed`` for
    every override and rejection. With a Beta prior the posterior over the true
    rate is Beta(prior[0] + k, prior[1] + n - k); the table gives its mean, a
    ``credible`` interval and ``p_below_threshold``, the probability the true
    rate is under the bar. That number is meaningful at any n; ``sufficient``
    marks groups with at least ``minimum`` decided tasks so the reader knows
    how much of it is prior. ``alpha`` and ``beta`` are the posterior's own
    parameters, so a chart or a follow-up calculation starts from the same
    distribution.
    """
    t = _decided(f)
    if mode is not None:
        t = t[t["mode"] == mode]
    if counting == "reversing":
        rev = set(f.overrides[f.overrides["intent_preserved"] == False]["task_id"])  # noqa: E712
        t["against_flag"] = t["task_id"].isin(rev) | (t["outcome"] == "rejected")
    elif counting == "changed":
        t["against_flag"] = t["outcome"].isin(("overridden", "rejected"))
    else:
        raise ValueError("counting must be 'reversing' or 'changed'")
    t, groups = _with_group(f, t, by)
    cols = [g for g in groups if g != "_all"] + ["n", "against", "rate", "posterior_mean",
                                                "low", "high", "p_below_threshold", "threshold",
                                                "alpha", "beta", "sufficient"]
    if t.empty:
        return _with_minimum(pd.DataFrame(columns=cols), minimum)
    a0, b0 = prior
    rows = []
    for key, part in t.groupby(groups, dropna=False):
        n = len(part)
        k = int(part["against_flag"].sum())
        a, b = a0 + k, b0 + n - k
        lo_q, hi_q = (1 - credible) / 2, 1 - (1 - credible) / 2
        row = dict(zip(groups, key if isinstance(key, tuple) else (key,)))
        row.update({"n": n, "against": k, "rate": k / n if n else float("nan"),
                    "posterior_mean": a / (a + b),
                    "low": beta_quantile(lo_q, a, b), "high": beta_quantile(hi_q, a, b),
                    "p_below_threshold": beta_cdf(threshold, a, b),
                    "threshold": threshold, "alpha": float(a), "beta": float(b),
                    "sufficient": n >= minimum})
        rows.append(row)
    return _with_minimum(_finish(pd.DataFrame(rows), groups)[cols], minimum)


def sequential(f: Frames, p0: float, p1: float, *, alpha: float = 0.05, beta: float = 0.20,
               by: str | None = "assignee", counting: str = "reversing") -> pd.DataFrame:
    """
    Wald's sequential probability ratio test on the correction rate, task by
    task in time order.

    ``p0`` is the rate an agent may run at and ``p1`` the rate that should
    stop a promotion. ``alpha`` is the chance of stopping a good agent, ``beta``
    the chance of promoting a bad one. Each row carries the cumulative
    log-likelihood ratio and a ``verdict``: ``promote`` once the ratio crosses
    the lower bound, ``hold`` once it crosses the upper, ``continue`` between.
    The verdict in the last row is the current state of the test.
    """
    t = _decided(f).sort_values("settled_at")
    if counting == "reversing":
        rev = set(f.overrides[f.overrides["intent_preserved"] == False]["task_id"])  # noqa: E712
        t["against_flag"] = t["task_id"].isin(rev) | (t["outcome"] == "rejected")
    else:
        t["against_flag"] = t["outcome"].isin(("overridden", "rejected"))
    t, groups = _with_group(f, t, by)
    upper = math.log((1 - beta) / alpha)
    lower = math.log(beta / (1 - alpha))
    inc_bad = math.log(p1 / p0)
    inc_good = math.log((1 - p1) / (1 - p0))
    rows = []
    for key, part in t.groupby(groups, dropna=False):
        llr, verdict = 0.0, "continue"
        for i, r in enumerate(part.itertuples(index=False), start=1):
            if verdict == "continue":
                llr += inc_bad if r.against_flag else inc_good
                if llr >= upper:
                    verdict = "hold"
                elif llr <= lower:
                    verdict = "promote"
            row = dict(zip(groups, key if isinstance(key, tuple) else (key,)))
            row.update({"task_id": r.task_id, "settled_at": r.settled_at, "i": i,
                        "against": bool(r.against_flag), "llr": llr, "upper": upper,
                        "lower": lower, "verdict": verdict})
            rows.append(row)
    cols = [g for g in groups if g != "_all"] + ["task_id", "settled_at", "i", "against",
                                                "llr", "upper", "lower", "verdict"]
    if not rows:
        return pd.DataFrame(columns=cols)
    return _finish(pd.DataFrame(rows), groups)[cols]


# ============================================================================
#   Reviewer agreement
# ============================================================================

def _multi_decided_passes(f: Frames) -> pd.DataFrame:
    d = f.decisions
    if d.empty:
        return d
    d = d[d["kind"].isin(("approve", "override", "reject"))].copy()
    d["category"] = np.where(d["kind"] == "approve", "accept", "change")
    counts = d.groupby(["task_id", "review_index"])["reviewer"].nunique()
    multi = counts[counts >= 2].index
    return d.set_index(["task_id", "review_index"]).loc[multi].reset_index()


def agreement(f: Frames, *, minimum: int = 10) -> pd.DataFrame:
    """
    Fleiss' kappa over review passes where two or more reviewers decided the
    same artefact, on accept against change (override or reject).

    Read it with the protocol in mind. Under ``quorum:N`` and ``all_approve``
    a pass stays open for a second decision only while the first reviewers
    approve; an override or a rejection settles it. So the passes compared
    here are the ones a first reviewer accepted, and kappa measures how often
    the next reviewer agreed with an acceptance. For independent judgements
    on one question, use :func:`vote_agreement` on deliberations.

    Fleiss' statistic assumes the same number of raters on every subject, so
    the passes are grouped by how many reviewers decided them and one row is
    returned per group size. ``kappa`` below zero is worse than chance;
    ``sufficient`` needs ``minimum`` passes. Abstentions are outside the
    comparison.
    """
    d = _multi_decided_passes(f)
    cols = ["raters", "n_passes", "n_reviewers", "agreement_observed", "agreement_expected",
            "kappa", "sufficient"]
    if d.empty:
        return _with_minimum(pd.DataFrame(columns=cols), minimum)
    rows = []
    per_pass = d.groupby(["task_id", "review_index"])
    sizes = per_pass["reviewer"].nunique()
    for m in sorted(sizes.unique()):
        keys = sizes[sizes == m].index
        sub = d.set_index(["task_id", "review_index"]).loc[keys].reset_index()
        # Where a reviewer decided twice in a pass, keep the last decision.
        sub = sub.sort_values("seq").groupby(["task_id", "review_index", "reviewer"]).tail(1)
        table = sub.pivot_table(index=["task_id", "review_index"], columns="category",
                                values="reviewer", aggfunc="count", fill_value=0)
        for cat in ("accept", "change"):
            if cat not in table.columns:
                table[cat] = 0
        counts = table[["accept", "change"]].to_numpy(dtype=float)
        n_sub = len(counts)
        p_i = (counts * (counts - 1)).sum(axis=1) / (m * (m - 1))
        p_bar = float(p_i.mean())
        p_j = counts.sum(axis=0) / (n_sub * m)
        p_e = float((p_j ** 2).sum())
        kappa = (p_bar - p_e) / (1 - p_e) if p_e < 1 else float("nan")
        rows.append({"raters": int(m), "n_passes": n_sub,
                     "n_reviewers": int(sub["reviewer"].nunique()),
                     "agreement_observed": p_bar, "agreement_expected": p_e,
                     "kappa": kappa, "sufficient": n_sub >= minimum})
    return _with_minimum(pd.DataFrame(rows, columns=cols), minimum)


def vote_agreement(f: Frames, *, minimum: int = 5) -> pd.DataFrame:
    """
    Fleiss' kappa over deliberations, on yea against nay, grouped by the
    number of voters. Votes are cast independently, so this is the cleaner
    measure of how far a group agrees. Abstentions are set aside.
    """
    v = f.votes
    cols = ["voters", "n_deliberations", "agreement_observed", "agreement_expected", "kappa", "sufficient"]
    if v.empty:
        return _with_minimum(pd.DataFrame(columns=cols), minimum)
    v = v[v["vote"].isin(("yea", "nay"))]
    v = v.sort_values("seq").groupby(["deliberation_id", "voter"]).tail(1)
    sizes = v.groupby("deliberation_id")["voter"].nunique()
    rows = []
    for m in sorted(sizes[sizes >= 2].unique()):
        ids = sizes[sizes == m].index
        sub = v[v["deliberation_id"].isin(ids)]
        table = sub.pivot_table(index="deliberation_id", columns="vote", values="voter",
                                aggfunc="count", fill_value=0)
        for cat in ("yea", "nay"):
            if cat not in table.columns:
                table[cat] = 0
        counts = table[["yea", "nay"]].to_numpy(dtype=float)
        n_sub = len(counts)
        p_i = (counts * (counts - 1)).sum(axis=1) / (m * (m - 1))
        p_bar = float(p_i.mean())
        p_j = counts.sum(axis=0) / (n_sub * m)
        p_e = float((p_j ** 2).sum())
        kappa = (p_bar - p_e) / (1 - p_e) if p_e < 1 else float("nan")
        rows.append({"voters": int(m), "n_deliberations": n_sub, "agreement_observed": p_bar,
                     "agreement_expected": p_e, "kappa": kappa, "sufficient": n_sub >= minimum})
    return _with_minimum(pd.DataFrame(rows, columns=cols), minimum)


def pairwise_agreement(f: Frames, *, minimum: int = 10) -> pd.DataFrame:
    """
    Cohen's kappa for each pair of reviewers over the passes both decided,
    accept against change. One row per pair.
    """
    d = _multi_decided_passes(f)
    cols = ["reviewer_a", "reviewer_b", "n_passes", "agreement_observed", "kappa", "sufficient"]
    if d.empty:
        return _with_minimum(pd.DataFrame(columns=cols), minimum)
    d = d.sort_values("seq").groupby(["task_id", "review_index", "reviewer"]).tail(1)
    wide = d.pivot_table(index=["task_id", "review_index"], columns="reviewer",
                         values="category", aggfunc="first")
    reviewers = sorted(wide.columns)
    rows = []
    for i, a in enumerate(reviewers):
        for b in reviewers[i + 1:]:
            both = wide[[a, b]].dropna()
            n = len(both)
            if n == 0:
                continue
            po = float((both[a] == both[b]).mean())
            pa = both[a].value_counts(normalize=True)
            pb = both[b].value_counts(normalize=True)
            pe = float(sum(pa.get(c, 0) * pb.get(c, 0) for c in ("accept", "change")))
            kappa = (po - pe) / (1 - pe) if pe < 1 else float("nan")
            rows.append({"reviewer_a": a, "reviewer_b": b, "n_passes": n,
                         "agreement_observed": po, "kappa": kappa, "sufficient": n >= minimum})
    return _with_minimum(pd.DataFrame(rows, columns=cols), minimum)


def abstentions(f: Frames, by: str = "task_kind") -> pd.DataFrame:
    """Abstentions by stated category and group, with each group's decided count for scale."""
    d = f.decisions
    cols = [by, "abstain_category", "n", "decided", "share"]
    if d.empty:
        return pd.DataFrame(columns=cols)
    ab = d[d["kind"] == "abstain"].copy()
    ab["abstain_category"] = ab["abstain_category"].fillna("unstated")
    out = ab.groupby([by, "abstain_category"], dropna=False).size().rename("n").reset_index()
    decided = d[d["is_final"].fillna(False)].groupby(by, dropna=False).size().rename("decided").reset_index()
    out = out.merge(decided, on=by, how="left")
    out["share"] = out["n"] / out["decided"]
    return out[cols].sort_values(["n"], ascending=False).reset_index(drop=True)


# ============================================================================
#   Whispers and handoffs
# ============================================================================

def whispers(f: Frames, by: str | None = "asker", *, minimum: int = 5) -> pd.DataFrame:
    """
    How often agents ask, how often the question lapses, and how fast an
    answer comes. Lapse rate carries a Wilson interval over the whispers that
    reached a resolution (answered or lapsed); pending ones are counted apart.
    """
    w = f.whispers.copy()
    groups = [by] if by else []
    cols = groups + ["n", "answered", "lapsed", "pending", "lapse_rate", "low", "high",
                     "median_response_s", "sufficient"]
    if w.empty:
        return _with_minimum(pd.DataFrame(columns=cols), minimum)
    if not groups:
        w["_all"] = "all"
        groups = ["_all"]
    g = w.groupby(groups, dropna=False)
    out = pd.DataFrame({
        "n": g.size(),
        "answered": g["answered"].apply(lambda s: int(s.fillna(False).sum())),
        "lapsed": g["lapsed"].apply(lambda s: int(s.fillna(False).sum())),
        "median_response_s": g["response_s"].median(),
    })
    out["pending"] = g["state"].apply(lambda s: int((s == "pending").sum()))
    resolved = out["n"] - out["pending"]
    out["lapse_rate"] = np.where(resolved > 0, out["lapsed"] / resolved.where(resolved > 0, 1), np.nan)
    out["low"], out["high"] = wilson(out["lapsed"].to_numpy(), resolved.to_numpy())
    out["sufficient"] = resolved >= minimum
    out = out.reset_index()
    if groups == ["_all"]:
        out = out.drop(columns=["_all"])
        cols = [c for c in cols if c != by]
    return _with_minimum(out[[c for c in cols if c in out.columns]], minimum)


def handoffs(f: Frames, by: str | None = "recipient", *, minimum: int = 5) -> pd.DataFrame:
    """
    Acceptance rate per recipient (or proposer), with a Wilson interval over
    resolved handoffs, and the median time to a resolution.
    """
    h = f.handoffs.copy()
    groups = [by] if by else []
    cols = groups + ["n", "accepted", "declined", "open", "accept_rate", "low", "high",
                     "median_response_s", "sufficient"]
    if h.empty:
        return _with_minimum(pd.DataFrame(columns=cols), minimum)
    if not groups:
        h["_all"] = "all"
        groups = ["_all"]
    g = h.groupby(groups, dropna=False)
    out = pd.DataFrame({
        "n": g.size(),
        "accepted": g["resolution"].apply(lambda s: int((s == "accepted").sum())),
        "declined": g["resolution"].apply(lambda s: int((s == "declined").sum())),
        "open": g["resolution"].apply(lambda s: int((s == "open").sum())),
        "median_response_s": g["response_s"].median(),
    })
    resolved = out["accepted"] + out["declined"]
    out["accept_rate"] = np.where(resolved > 0, out["accepted"] / resolved.where(resolved > 0, 1), np.nan)
    out["low"], out["high"] = wilson(out["accepted"].to_numpy(), resolved.to_numpy())
    out["sufficient"] = resolved >= minimum
    out = out.reset_index()
    if groups == ["_all"]:
        out = out.drop(columns=["_all"])
        cols = [c for c in cols if c != by]
    return _with_minimum(out[[c for c in cols if c in out.columns]], minimum)


# ============================================================================
#   Assurance
# ============================================================================

def assurance(f: Frames, freq: str = "D") -> pd.DataFrame:
    """
    Per period: how many entries the chain holds, and the share that are
    hash-linked, signed, and covered by a recorded SCITT submission.
    """
    e = f.events.dropna(subset=["ts"]).copy()
    cols = ["period", "n", "chained", "signed", "scitt_submitted",
            "chained_share", "signed_share", "scitt_share"]
    if e.empty:
        return pd.DataFrame(columns=cols)
    e["period"] = _period_start(e["ts"], freq)
    g = e.groupby("period")
    out = pd.DataFrame({
        "n": g.size(),
        "chained": g["chained"].apply(lambda s: int(s.fillna(False).sum())),
        "signed": g["signed"].apply(lambda s: int(s.fillna(False).sum())),
        "scitt_submitted": g["scitt_submitted"].apply(lambda s: int(s.fillna(False).sum())),
    })
    for name in ("chained", "signed"):
        out[f"{name}_share"] = out[name] / out["n"]
    out["scitt_share"] = out["scitt_submitted"] / out["n"]
    return out.reset_index()[cols]


# ============================================================================
#   Drift
# ============================================================================

def _cusum_threshold(p0: float, p1: float, arl0: int, runs: int, seed: int, horizon: int) -> float:
    """
    Decision interval h giving an in-control average run length near ``arl0``,
    found by simulation: the smallest h whose simulated mean run length under
    p0 reaches arl0.
    """
    rng = np.random.default_rng(seed)
    inc_bad, inc_good = math.log(p1 / p0), math.log((1 - p1) / (1 - p0))
    draws = rng.random((runs, horizon)) < p0
    incs = np.where(draws, inc_bad, inc_good)
    # Cumulative CUSUM paths with reset at zero, per run.
    paths = np.zeros_like(incs)
    s = np.zeros(runs)
    for j in range(horizon):
        s = np.maximum(0.0, s + incs[:, j])
        paths[:, j] = s
    maxima = np.maximum.accumulate(paths, axis=1)
    candidates = np.linspace(0.5, 12.0, 47)
    for h in candidates:
        crossed = maxima >= h
        first = np.where(crossed.any(axis=1), crossed.argmax(axis=1) + 1, horizon)
        if first.mean() >= arl0:
            return float(h)
    return float(candidates[-1])


def cusum(f: Frames, *, target: float | None = None, shift: float = 0.10,
          false_alarm_runs: int = 1000, baseline: int = 100, by: str | None = None,
          seed: int = 0) -> pd.DataFrame:
    """
    Bernoulli CUSUM on whether each decided task was corrected, in time order.

    ``target`` is the override rate the process is believed to run at; when
    unset it is the rate over the first ``baseline`` tasks. ``shift`` is the
    rise the chart is tuned to catch (target + shift). ``false_alarm_runs`` is
    the average number of tasks between false alarms when the rate has not
    moved; the decision interval is chosen by simulation to give that.

    One row per decided task with the cumulative statistic, the threshold and
    ``alarm`` where it was crossed. The statistic resets after an alarm.
    """
    t = _decided(f).sort_values("settled_at")
    t["changed_flag"] = t["outcome"].isin(("overridden", "rejected"))
    t, groups = _with_group(f, t, by)
    cols = [g for g in groups if g != "_all"] + ["task_id", "settled_at", "i", "changed",
                                                "rate_so_far", "statistic", "threshold",
                                                "alarm", "target", "detect"]
    rows = []
    for key, part in t.groupby(groups, dropna=False):
        changed = part["changed_flag"].to_numpy(dtype=bool)
        n = len(changed)
        if n == 0:
            continue
        p0 = target if target is not None else float(changed[:baseline].mean()) if n else 0.0
        p0 = min(max(p0, 0.02), 0.98)
        p1 = min(p0 + shift, 0.99)
        inc_bad, inc_good = math.log(p1 / p0), math.log((1 - p1) / (1 - p0))
        h = _cusum_threshold(p0, p1, false_alarm_runs, runs=400, seed=seed, horizon=max(200, false_alarm_runs * 3))
        s = 0.0
        running = 0
        for i, r in enumerate(part.itertuples(index=False), start=1):
            s = max(0.0, s + (inc_bad if r.changed_flag else inc_good))
            running += int(r.changed_flag)
            alarm = s >= h
            row = dict(zip(groups, key if isinstance(key, tuple) else (key,)))
            row.update({"task_id": r.task_id, "settled_at": r.settled_at, "i": i,
                        "changed": bool(r.changed_flag), "rate_so_far": running / i,
                        "statistic": s, "threshold": h, "alarm": alarm,
                        "target": p0, "detect": p1})
            rows.append(row)
            if alarm:
                s = 0.0
    if not rows:
        return pd.DataFrame(columns=cols)
    return _finish(pd.DataFrame(rows), groups)[cols]
