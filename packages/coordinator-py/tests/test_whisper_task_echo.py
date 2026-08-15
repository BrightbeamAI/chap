"""
whisper.answer echoes the whisper's task_id onto the recorded envelope, so a
task-filtered audit.read returns the answer alongside the ask. The value is
taken from the stored whisper, never from the caller, so an answer cannot be
filed against a different task.
"""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions

PROFILES = ["core/1.0", "whisper/1.0", "audit-scitt/1.0"]


def _setup(with_coord=False):
    c = Coordinator(CoordinatorOptions(default_profiles=PROFILES))

    def s(m, **p):
        return c.dispatch({"jsonrpc": "2.0", "id": m, "method": m, "params": p})

    s("workspace.create", workspace="w", profiles=PROFILES)
    s("participant.join", workspace="w", **{"from": "agent:bot"}, type="agent")
    s("participant.join", workspace="w", **{"from": "human:me"}, type="human")
    return (s, c) if with_coord else s


def _task(s, kind="x"):
    return s("task.create", workspace="w", **{"from": "agent:bot"}, kind=kind,
             input={}, assignee="agent:bot")["result"]["task_id"]


def _ask(s, task_id):
    return s("whisper.ask", workspace="w", **{"from": "agent:bot"}, to="human:me",
             task_id=task_id, question="Proceed?", deadline_ms=60000,
             default_if_lapsed="yes",
             options=[{"id": "yes", "label": "Yes"}])["result"]["whisper_id"]


def _methods(s, task_id):
    entries = s("audit.read", workspace="w", **{"from": "human:me"},
                filter={"task_id": task_id})["result"]["entries"]
    return [e["envelope"]["method"] for e in entries]


def test_answer_is_returned_by_a_task_filtered_read():
    s = _setup()
    tid = _task(s)
    wid = _ask(s, tid)
    s("whisper.answer", workspace="w", **{"from": "human:me"},
      whisper_id=wid, answer_option="yes")
    assert _methods(s, tid) == ["whisper.ask", "whisper.answer"]


def test_answer_result_carries_task_id():
    s = _setup()
    tid = _task(s)
    wid = _ask(s, tid)
    r = s("whisper.answer", workspace="w", **{"from": "human:me"},
          whisper_id=wid, answer_option="yes")["result"]
    assert r["task_id"] == tid


def test_caller_supplied_task_id_cannot_misfile_the_answer():
    s = _setup()
    real = _task(s, "x")
    other = _task(s, "y")
    wid = _ask(s, real)
    s("whisper.answer", workspace="w", **{"from": "human:me"},
      whisper_id=wid, answer_option="yes", task_id=other)
    assert "whisper.answer" in _methods(s, real)
    assert "whisper.answer" not in _methods(s, other)


def test_lapse_notification_carries_task_id():
    """A lapsed whisper applies its default with no human input, so the
    notification must be visible on a task-filtered read."""
    import datetime as _dt

    s, c = _setup(with_coord=True)
    tid = _task(s)
    _ask(s, tid)
    later = (_dt.datetime.now(_dt.timezone.utc)
             + _dt.timedelta(milliseconds=120000)).strftime("%Y-%m-%dT%H:%M:%SZ")
    emitted = c.check_whisper_lapses("w", later)
    assert emitted, "the whisper should have lapsed"
    assert emitted[0]["params"]["task_id"] == tid
    assert "notify.message" in _methods(s, tid)
