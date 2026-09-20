"""
Reviewer severity against agent quality.

A reviewer who changes half of what they see may be strict, or may be seeing
weak work. With several reviewers deciding on several agents, the two can be
separated: fit one number per agent (quality) and one per reviewer (severity)
so that the chance a decision accepts the work as drafted is

    P(accept) = sigmoid(quality[agent] - severity[reviewer] + baseline)

which is a Rasch-style logistic model. The fit is by penalised maximum
likelihood in numpy; the penalty keeps a reviewer or agent with few decisions
near zero rather than at infinity. Below the minimum amount of data the
function declines to fit and says why, because the numbers would be the
prior and nothing else.
"""
from __future__ import annotations


import numpy as np
import pandas as pd

from .frames import Frames

__all__ = ["reviewer_severity"]


def reviewer_severity(f: Frames, *, minimum_decisions: int = 30, minimum_reviewers: int = 2,
                      minimum_per_unit: int = 5, l2: float = 1.0, iterations: int = 2000,
                      learning_rate: float = 0.05) -> pd.DataFrame:
    """
    One row per agent and per reviewer with the fitted parameter, how many
    decisions it rests on, and an approximate standard error from the
    diagonal of the penalised observed information, which leaves out the
    correlation between a reviewer's and an agent's estimates. The frame's
    ``attrs`` carry ``fitted`` (bool), ``reason`` when it is False,
    ``baseline``, ``log_likelihood`` and ``n``.

    Positive ``quality`` is work accepted more than the baseline; positive
    ``severity`` is a reviewer who accepts less than the baseline. Both are
    on the log-odds scale and are identified relative to their group mean.
    """
    d = f.decisions
    cols = ["unit", "role", "estimate", "se", "n", "accept_share"]
    out = pd.DataFrame(columns=cols)
    d = d[d["kind"].isin(("approve", "override", "reject"))].dropna(subset=["reviewer", "assignee"])
    if d.empty or len(d) < minimum_decisions:
        out.attrs.update({"fitted": False, "reason": f"{len(d)} decisions; at least {minimum_decisions} are needed", "n": int(len(d))})
        return out
    d = d.sort_values("seq").groupby(["task_id", "review_index", "reviewer"]).tail(1)
    reviewers = sorted(d["reviewer"].unique())
    agents = sorted(d["assignee"].unique())
    if len(reviewers) < minimum_reviewers:
        out.attrs.update({"fitted": False, "reason": f"{len(reviewers)} reviewer(s); at least {minimum_reviewers} are needed to separate severity from quality", "n": int(len(d))})
        return out
    counts_r = d["reviewer"].value_counts()
    counts_a = d["assignee"].value_counts()
    thin = [u for u in reviewers if counts_r[u] < minimum_per_unit] + [u for u in agents if counts_a[u] < minimum_per_unit]
    y = (d["kind"] == "approve").to_numpy(dtype=float)
    ri = d["reviewer"].map({r: i for i, r in enumerate(reviewers)}).to_numpy()
    ai = d["assignee"].map({a: i for i, a in enumerate(agents)}).to_numpy()
    nr, na, n = len(reviewers), len(agents), len(d)

    sev = np.zeros(nr)
    qual = np.zeros(na)
    base = float(np.log(max(y.mean(), 1e-6) / max(1 - y.mean(), 1e-6)))
    # Gradient ascent on the penalised log-likelihood. Each parameter's step
    # is its gradient divided by the number of decisions it appears in, so a
    # busy reviewer and a quiet one move at comparable speed; the logistic
    # curvature is at most a quarter, so a step four times the mean gradient
    # stays stable and converges in fewer iterations.
    curvature_bound = 4.0
    counts_a = np.maximum(1, np.bincount(ai, minlength=na))
    counts_r = np.maximum(1, np.bincount(ri, minlength=nr))
    for _ in range(iterations):
        eta = qual[ai] - sev[ri] + base
        p = 1 / (1 + np.exp(-eta))
        resid = y - p
        g_qual = np.bincount(ai, weights=resid, minlength=na) - l2 * qual
        g_sev = -np.bincount(ri, weights=resid, minlength=nr) - l2 * sev
        g_base = resid.sum()
        qual += learning_rate * curvature_bound * g_qual / counts_a
        sev += learning_rate * curvature_bound * g_sev / counts_r
        base += learning_rate * g_base / n
        # Identify each group relative to its mean.
        qual -= qual.mean()
        sev -= sev.mean()
    eta = qual[ai] - sev[ri] + base
    p = 1 / (1 + np.exp(-eta))
    w = p * (1 - p)
    info_a = np.bincount(ai, weights=w, minlength=na) + l2
    info_r = np.bincount(ri, weights=w, minlength=nr) + l2
    ll = float((y * np.log(np.clip(p, 1e-12, 1)) + (1 - y) * np.log(np.clip(1 - p, 1e-12, 1))).sum())

    rows = []
    for i, a in enumerate(agents):
        mask = ai == i
        rows.append({"unit": a, "role": "agent", "estimate": float(qual[i]), "se": float(1 / np.sqrt(info_a[i])),
                     "n": int(mask.sum()), "accept_share": float(y[mask].mean())})
    for i, r in enumerate(reviewers):
        mask = ri == i
        rows.append({"unit": r, "role": "reviewer", "estimate": float(sev[i]), "se": float(1 / np.sqrt(info_r[i])),
                     "n": int(mask.sum()), "accept_share": float(y[mask].mean())})
    out = pd.DataFrame(rows, columns=cols)
    out["thin"] = out["unit"].isin(thin)
    out.attrs.update({"fitted": True, "reason": None, "baseline": float(base), "log_likelihood": ll, "n": int(n),
                      "note": "estimates are log-odds relative to the group mean; 'thin' units rest on fewer than "
                              f"{minimum_per_unit} decisions and sit near zero by penalty rather than by evidence"})
    return out
