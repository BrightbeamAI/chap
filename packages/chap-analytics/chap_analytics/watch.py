"""
Watching a live workspace.

A :class:`Watcher` reads ``audit.read`` incrementally from a sequence cursor,
re-projects the chain, runs the drift chart, and rewrites the report on each
poll where a ``report_path`` was given; :meth:`Watcher.briefs` gives the
briefs for the chain as it stands. When the CUSUM crosses its decision
interval the watcher calls ``on_alarm`` with the alarm: the metric, where it
moved, and the changed tasks among the twenty decided before it. Each alarm
is reported once.

    from chap_analytics import watch

    w = watch.Watcher("http://localhost:8080/chap", "wsp_support",
                      report_path="support.html",
                      on_alarm=lambda a: print(a.headline))
    w.run(interval_s=300)

The source can be an HTTP endpoint, an in-process ``Coordinator``, or any
callable that takes a starting sequence number and returns the entries from
there on, which is what tests use.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from . import briefs as _briefs
from . import report as _report
from . import stats as _stats
from .frames import Frames, frames
from .load import Chain, Redactor, _chain

__all__ = ["Watcher", "Alarm", "Update", "http_source", "coordinator_source"]

Source = Callable[[int], list[dict]]

#: How many decided tasks before an alarm are searched for the changed ones it reports.
_WINDOW = 20


@dataclass
class Alarm:
    """A drift alarm: which metric moved, when, and the changed tasks among the twenty decided before it."""
    metric: str
    at_task: int
    settled_at: Any
    task_id: str
    target: float
    detect: float
    rate_so_far: float
    window: list[str] = field(default_factory=list)

    @property
    def headline(self) -> str:
        """One sentence: the metric, where it moved, and the rate so far."""
        when = self.settled_at.strftime("%d %b %H:%M") if isinstance(self.settled_at, pd.Timestamp) else str(self.settled_at)
        return (f"{self.metric} moved above its baseline of {self.target:.0%} at task {self.at_task} ({when}); "
                f"{self.rate_so_far:.0%} of decided tasks so far were changed.")


@dataclass
class Update:
    """What one poll found."""
    new_entries: int
    total_entries: int
    frames: Frames
    alarms: list[Alarm]
    report_path: str | None = None


def http_source(url: str, workspace: str, *, actor: str = "service:analytics", timeout: int = 30) -> Source:
    """``audit.read`` over HTTP, from a starting sequence number."""
    scheme = urllib.parse.urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"http_source only accepts http and https URLs; got {scheme or 'no'} scheme in {url!r}.")

    def fetch(from_seq: int) -> list[dict]:
        body = json.dumps({
            "jsonrpc": "2.0", "id": "analytics-watch", "method": "audit.read",
            "params": {"workspace": workspace, "from": actor, "range": {"from_seq": from_seq}},
        }).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.load(resp)
        if "error" in payload:
            raise RuntimeError(f"audit.read refused: {payload['error']}")
        return list(payload.get("result", {}).get("entries", []))

    return fetch


def coordinator_source(coord: Any, workspace: str, *, actor: str = "service:analytics") -> Source:
    """``audit.read`` against an in-process coordinator, from a starting sequence number."""
    def fetch(from_seq: int) -> list[dict]:
        res = coord.dispatch({"jsonrpc": "2.0", "id": "analytics-watch", "method": "audit.read",
                              "params": {"workspace": workspace, "from": actor, "range": {"from_seq": from_seq}}})
        if "error" in res:
            raise RuntimeError(f"audit.read refused: {res['error']}")
        return list(res.get("result", {}).get("entries", []))
    return fetch


class Watcher:
    """
    Polls a source, keeps the entries seen so far, and re-projects on each
    poll. ``on_update`` receives every :class:`Update`; ``on_alarm`` receives
    each new :class:`Alarm` once.
    """

    def __init__(self, source: str | Source | Any, workspace: str, *,
                 actor: str = "service:analytics", redact: Redactor | None = None,
                 report_path: str | None = None, report_kwargs: dict | None = None,
                 on_update: Callable[[Update], None] | None = None,
                 on_alarm: Callable[[Alarm], None] | None = None,
                 cusum: dict | None = None):
        if isinstance(source, str):
            self.source: Source = http_source(source, workspace, actor=actor)
        elif callable(source) and not hasattr(source, "dispatch"):
            self.source = source
        else:
            self.source = coordinator_source(source, workspace, actor=actor)
        self.workspace = workspace
        self.redact = redact
        self.report_path = report_path
        self.report_kwargs = report_kwargs or {}
        self.on_update = on_update
        self.on_alarm = on_alarm
        self.cusum_kwargs = {"false_alarm_runs": 1000, **(cusum or {})}
        self.entries: list[dict] = []
        self.cursor = 0
        self.alarmed: set[str] = set()
        self.frames: Frames | None = None

    def chain(self) -> Chain:
        """The entries seen so far as a :class:`Chain`, with the redactor applied."""
        return _chain(self.workspace, list(self.entries), None, "audit.read (watch)", self.redact)

    def poll(self) -> Update:
        """Fetch what is new, re-project, check for drift, regenerate outputs."""
        new = self.source(self.cursor)
        # Guard against a source that returns the whole log regardless of range.
        new = [e for e in new if not isinstance(e.get("seq"), int) or e["seq"] >= self.cursor]
        self.entries.extend(new)
        if self.entries:
            seqs = [e.get("seq") for e in self.entries if isinstance(e.get("seq"), int)]
            self.cursor = (max(seqs) + 1) if seqs else len(self.entries)
        f = frames(self.chain())
        self.frames = f
        alarms = self._alarms(f)
        path = None
        if self.report_path:
            path = _report.write(f, self.report_path, **self.report_kwargs)
        update = Update(new_entries=len(new), total_entries=len(self.entries), frames=f, alarms=alarms, report_path=path)
        if self.on_update:
            self.on_update(update)
        for a in alarms:
            if self.on_alarm:
                self.on_alarm(a)
        return update

    def briefs(self) -> list[_briefs.Brief]:
        """The briefs for the chain as it stands."""
        if self.frames is None:
            self.poll()
        return _briefs.everything(self.frames)

    def _alarms(self, f: Frames) -> list[Alarm]:
        c = _stats.cusum(f, **self.cusum_kwargs)
        out = []
        if c.empty:
            return out
        alarmed = c[c["alarm"]]
        for r in alarmed.itertuples(index=False):
            if r.task_id in self.alarmed:
                continue
            self.alarmed.add(r.task_id)
            window = list(c[(c["i"] <= r.i) & (c["i"] > max(0, r.i - _WINDOW)) & c["changed"]]["task_id"])
            out.append(Alarm(metric="correction rate", at_task=int(r.i), settled_at=r.settled_at, task_id=r.task_id,
                             target=float(r.target), detect=float(r.detect), rate_so_far=float(r.rate_so_far),
                             window=window))
        return out

    def run(self, *, interval_s: float = 60.0, iterations: int | None = None,
            sleep: Callable[[float], None] = time.sleep) -> None:
        """Poll every ``interval_s`` seconds, ``iterations`` times or until interrupted."""
        n = 0
        while iterations is None or n < iterations:
            self.poll()
            n += 1
            if iterations is not None and n >= iterations:
                break
            sleep(interval_s)
