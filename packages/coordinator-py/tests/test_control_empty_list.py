"""
control.snapshot and control.rollback must honour an explicitly empty list as
"nothing", not fall back to the default, matching the TypeScript reference and
ordinary JSON field-presence semantics. `p.get(x) or default` treated an
explicit [] as omitted.
"""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions

PROFILES = ["core/1.0", "review/1.0", "control/1.0"]


def _ready():
    c = Coordinator(CoordinatorOptions(default_profiles=PROFILES))

    def s(m, actor="human:a", **p):
        return c.dispatch({"jsonrpc": "2.0", "id": m, "method": m,
                           "params": {"workspace": "w", "from": actor, **p}})

    s("workspace.create", profiles=PROFILES)
    s("participant.join", type="human")
    return c, s


def _snap(c, sid):
    return c.get_workspace("w").snapshots[sid]


def test_snapshot_explicit_empty_include_captures_nothing():
    c, s = _ready()
    sid = s("control.snapshot", include=[])["result"]["snapshot_artefact_id"]
    snap = _snap(c, sid)
    assert snap.include == []
    assert snap.state == {}


def test_snapshot_omitted_include_uses_defaults():
    c, s = _ready()
    sid = s("control.snapshot")["result"]["snapshot_artefact_id"]
    snap = _snap(c, sid)
    assert set(snap.include) == {"members", "open_tasks", "mode_ceiling"}


def test_rollback_explicit_empty_what_to_restore_restores_nothing():
    c, s = _ready()
    sid = s("control.snapshot", include=["members", "mode_ceiling"])["result"]["snapshot_artefact_id"]
    r = s("control.rollback", to_snapshot_artefact_id=sid, what_to_restore=[])
    assert r["result"]["restored"] == []


def test_rollback_omitted_what_to_restore_restores_snapshot_include():
    c, s = _ready()
    sid = s("control.snapshot", include=["members", "mode_ceiling"])["result"]["snapshot_artefact_id"]
    r = s("control.rollback", to_snapshot_artefact_id=sid)
    assert set(r["result"]["restored"]) == {"members", "mode_ceiling"}
