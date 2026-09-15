"""
The deterministic clock advances once per dispatch, not once per internal
now() call. Two references that call now() a different number of times per
envelope must still stamp identical timestamps, which is what makes a
cross-language diff readable.
"""
from __future__ import annotations

import datetime as _dt

from chap_coordinator import Coordinator, CoordinatorOptions


def _ms(iso: str) -> int:
    return int(_dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S.%fZ")
               .replace(tzinfo=_dt.timezone.utc).timestamp() * 1000)


def _run():
    c = Coordinator(CoordinatorOptions(
        default_profiles=["core/1.0", "review/1.0"],
        deterministic_ids=True, deterministic_clock=True, enable_chain=True))

    def s(m, actor="human:a", **p):
        return c.dispatch({"jsonrpc": "2.0", "id": m, "method": m,
                           "params": {"workspace": "w", "from": actor, **p}})

    s("workspace.create", profiles=["core/1.0", "review/1.0"])
    s("participant.join", type="human")
    s("participant.join", actor="agent:b", type="agent")
    tid = s("task.create", kind="k", input={}, assignee="agent:b")["result"]["task_id"]
    s("task.update", actor="agent:b", task_id=tid, state="in_progress")
    return c, tid


def test_arrived_advances_one_step_per_dispatch():
    c, _ = _run()
    stamps = [_ms(e.arrived) for e in c.get_workspace("w").audit]
    diffs = [b - a for a, b in zip(stamps, stamps[1:])]
    assert diffs == [1000] * len(diffs), stamps


def test_now_is_frozen_within_a_dispatch():
    c, tid = _run()
    task = c.get_workspace("w").tasks[tid]
    entry = c.get_workspace("w").audit[-1]
    assert task.updated_at == entry.arrived


def test_now_outside_dispatch_still_advances():
    c = Coordinator(CoordinatorOptions(deterministic_clock=True))
    a, b = c.now_iso(), c.now_iso()
    assert a != b
