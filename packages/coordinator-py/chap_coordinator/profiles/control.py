"""
chap_coordinator.profiles.control

The control/1.0 profile (profiles/control.md).

Methods (all per spec):
  - control.pause            (scope: task|participant|workspace)
  - control.resume
  - control.cancel
  - control.snapshot         (returns art_... id)
  - control.rollback         (takes to_snapshot_artefact_id + what_to_restore)
  - control.supersede        (creates successor_task in one shot)
  - control.set_mode_ceiling

All control.* methods are privileged. Step-up enforcement is handled
at the coordinator dispatch layer; we surface CONTROL_STEP_UP_REQUIRED
where appropriate.

Error codes:
  -32060 step-up required
  -32061 not authorised for control operations
  -32062 snapshot artefact not found
  -32063 workspace paused; operation blocked
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..jsonrpc import E, rpc_error
from ..types import SnapshotArtefact, Task, TaskHistoryEntry

if TYPE_CHECKING:
    from ..coordinator import Coordinator


_VALID_MODES = ("shadow", "trial", "production")
_VALID_SCOPES = ("task", "participant", "workspace")


def register_control(coord: "Coordinator") -> None:

    def control_pause(p: dict) -> dict:
        ws = coord.workspaces.get(p.get("workspace", ""))
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        scope = p.get("scope", "task")
        if scope not in _VALID_SCOPES:
            return {"error": rpc_error(E.PARAMS, f"scope must be one of {_VALID_SCOPES}")}

        if scope == "task":
            task = ws.tasks.get(p.get("task_id", ""))
            if not task:
                return {"error": rpc_error(E.PARAMS, "Unknown task")}
            if task.state in ("completed", "declined", "cancelled"):
                return {"error": rpc_error(E.CONTROL_NOT_AUTHORISED,
                                           f"Cannot pause {task.state} task")}
            task.paused = True
            prior = task.state
            task.state = "paused"
            task.updated_at = coord.now_iso()
            task.history.append(TaskHistoryEntry(
                ts=task.updated_at, from_=p.get("from", ""),
                state="paused", note=f"was {prior}",
            ))
            return {"result": {"scope": "task", "task_id": task.id,
                               "state": "paused"}}

        if scope == "participant":
            uri = p.get("participant_uri") or p.get("uri")
            if not uri or uri not in ws.members:
                return {"error": rpc_error(E.PARAMS,
                                           f"Unknown participant: {uri}")}
            ws.members[uri].paused = True
            return {"result": {"scope": "participant", "participant_uri": uri,
                               "paused": True,
                               "in_flight_policy": p.get("in_flight_policy", "allow_to_complete")}}

        # workspace
        ws.state = "paused"
        return {"result": {"scope": "workspace", "workspace": ws.id,
                           "state": "paused"}}

    def control_resume(p: dict) -> dict:
        ws = coord.workspaces.get(p.get("workspace", ""))
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        scope = p.get("scope", "task")
        if scope not in _VALID_SCOPES:
            return {"error": rpc_error(E.PARAMS, f"scope must be one of {_VALID_SCOPES}")}

        if scope == "task":
            task = ws.tasks.get(p.get("task_id", ""))
            if not task:
                return {"error": rpc_error(E.PARAMS, "Unknown task")}
            if task.state != "paused":
                return {"error": rpc_error(E.CONTROL_NOT_AUTHORISED,
                                           f"Cannot resume {task.state} task")}
            task.paused = False
            task.state = "in_progress"
            task.updated_at = coord.now_iso()
            task.history.append(TaskHistoryEntry(
                ts=task.updated_at, from_=p.get("from", ""), state="in_progress",
            ))
            return {"result": {"scope": "task", "task_id": task.id,
                               "state": "in_progress"}}

        if scope == "participant":
            uri = p.get("participant_uri") or p.get("uri")
            if not uri or uri not in ws.members:
                return {"error": rpc_error(E.PARAMS,
                                           f"Unknown participant: {uri}")}
            ws.members[uri].paused = False
            return {"result": {"scope": "participant",
                               "participant_uri": uri, "paused": False}}

        # workspace
        ws.state = "active"
        return {"result": {"scope": "workspace", "workspace": ws.id,
                           "state": "active"}}

    def control_cancel(p: dict) -> dict:
        ws = coord.workspaces.get(p.get("workspace", ""))
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        task = ws.tasks.get(p.get("task_id", ""))
        if not task:
            return {"error": rpc_error(E.PARAMS, "Unknown task")}
        if task.state in ("completed", "declined", "cancelled"):
            return {"error": rpc_error(E.CONTROL_NOT_AUTHORISED,
                                       f"Cannot cancel {task.state} task")}
        task.state = "cancelled"
        task.updated_at = coord.now_iso()
        task.history.append(TaskHistoryEntry(
            ts=task.updated_at, from_=p.get("from", ""),
            state="cancelled", note=p.get("reason"),
        ))
        return {"result": {"state": "cancelled"}}

    def control_snapshot(p: dict) -> dict:
        ws = coord.workspaces.get(p.get("workspace", ""))
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        include = list(p.get("include") or ["members", "open_tasks",
                                            "mode_ceiling"])
        # Capture the requested slice of state
        state: dict = {}
        if "members" in include:
            state["members"] = [m.to_dict() for m in ws.members.values()]
        if "open_tasks" in include:
            state["open_tasks"] = [
                t.to_dict() for t in ws.tasks.values()
                if t.state not in ("completed", "declined", "cancelled")
            ]
        if "mode_ceiling" in include:
            state["mode_ceiling"] = ws.mode_ceiling
        if "policy" in include:
            state["routing_policy_uri"] = ws.routing_policy_uri
        if "all" in include or "audit" in include:
            state["audit_seq"] = len(ws.audit)

        snap_id = coord.ids.artefact_id()  # snapshots are artefacts per spec
        snap = SnapshotArtefact(
            id=snap_id,
            ts=coord.now_iso(),
            by=p.get("from", ""),
            workspace=ws.id,
            audit_seq=len(ws.audit),
            label=p.get("label"),
            include=include,
            state=state,
        )
        ws.snapshots[snap_id] = snap
        return {"result": {"snapshot_artefact_id": snap_id,
                           "audit_seq": snap.audit_seq,
                           "artefact": snap.to_dict()}}

    def control_rollback(p: dict) -> dict:
        ws = coord.workspaces.get(p.get("workspace", ""))
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        snap_id = (p.get("to_snapshot_artefact_id")
                   or p.get("snapshot_artefact_id")
                   or p.get("snapshot_id"))
        if not snap_id:
            return {"error": rpc_error(E.PARAMS,
                                       "to_snapshot_artefact_id is required")}
        snap = ws.snapshots.get(snap_id)
        if not snap:
            return {"error": rpc_error(E.CONTROL_SNAPSHOT_NOT_FOUND,
                                       f"Unknown snapshot: {snap_id}")}
        what = list(p.get("what_to_restore") or snap.include)

        # Apply the rollback. Per spec, this APPENDS, it does not truncate.
        # We restore the named slices of state.
        restored: list[str] = []
        if "mode_ceiling" in what and "mode_ceiling" in snap.state:
            ws.mode_ceiling = snap.state["mode_ceiling"]
            restored.append("mode_ceiling")
        if "members" in what and "members" in snap.state:
            # Restore each member's role/scopes from the snapshot;
            # we don't recreate departed members (audit is append-only)
            snap_uris = {m["uri"]: m for m in snap.state["members"]}
            for uri, snap_m in snap_uris.items():
                if uri in ws.members:
                    ws.members[uri].role = snap_m.get("role",
                                                     ws.members[uri].role)
                    ws.members[uri].scopes = snap_m.get("scopes")
            restored.append("members")

        return {"result": {
            "rolled_back_to": snap_id,
            "audit_seq": snap.audit_seq,
            "restored": restored,
            "reason": p.get("reason"),
        }}

    def control_supersede(p: dict) -> dict:
        ws = coord.workspaces.get(p.get("workspace", ""))
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        old_id = p.get("task_id")
        old = ws.tasks.get(old_id or "")
        if not old:
            return {"error": rpc_error(E.PARAMS, "Unknown task")}

        # successor_task is an object describing the new task; the Coordinator
        # creates it as part of the supersede operation (per spec).
        new_spec = p.get("successor_task") or {}
        if not new_spec or "kind" not in new_spec:
            return {"error": rpc_error(E.PARAMS,
                                       "successor_task must include kind")}
        assignee = new_spec.get("assignee") or old.assignee
        if assignee not in ws.members:
            return {"error": rpc_error(E.PARAMS,
                                       "successor assignee not in workspace")}

        now = coord.now_iso()
        new_id = coord.ids.task_id()
        new_task = Task(
            id=new_id,
            kind=new_spec["kind"],
            state="created",
            assignee=assignee,
            delegator=p.get("from", ""),
            input=new_spec.get("input") or {},
            created_at=now,
            updated_at=now,
            mode=new_spec.get("mode") or old.mode,
            supersedes=old.id,
            history=[TaskHistoryEntry(
                ts=now, from_=p.get("from", ""), state="created",
                note=f"supersedes {old.id}: {p.get('reason') or ''}",
            )],
        )
        ws.tasks[new_id] = new_task

        old.state = "superseded"
        old.superseded_by = new_id
        old.updated_at = now
        old.history.append(TaskHistoryEntry(
            ts=now, from_=p.get("from", ""), state="superseded",
            note=f"-> {new_id}: {p.get('reason') or ''}",
        ))
        return {"result": {
            "superseded_task_id": old.id,
            "new_task_id": new_id,
            "reason": p.get("reason"),
        }}

    def control_set_mode_ceiling(p: dict) -> dict:
        ws = coord.workspaces.get(p.get("workspace", ""))
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        new_ceiling = p.get("new_ceiling") or p.get("mode_ceiling")
        if new_ceiling not in _VALID_MODES:
            return {"error": rpc_error(E.PARAMS,
                                       f"new_ceiling must be one of {_VALID_MODES}")}
        ws.mode_ceiling = new_ceiling
        return {"result": {"mode_ceiling": ws.mode_ceiling,
                           "reason": p.get("reason")}}

    coord._handlers["control.pause"] = control_pause
    coord._handlers["control.resume"] = control_resume
    coord._handlers["control.cancel"] = control_cancel
    coord._handlers["control.snapshot"] = control_snapshot
    coord._handlers["control.rollback"] = control_rollback
    coord._handlers["control.supersede"] = control_supersede
    coord._handlers["control.set_mode_ceiling"] = control_set_mode_ceiling
