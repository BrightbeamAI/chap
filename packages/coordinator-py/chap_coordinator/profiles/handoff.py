"""
chap_coordinator.profiles.handoff

The handoff/1.0 profile (profiles/handoff.md).

Methods:
  - handoff.propose : propose moving one or more tasks to a recipient
  - handoff.accept  : accept; atomically reassigns the accepted tasks
  - handoff.decline : decline; original assignee stays; suggest alternative

`to` is a single participant URI or a group:... URI. For groups, the
first member to accept wins; subsequent accepts return
HANDOFF_ALREADY_RESOLVED.

Error codes:
  -32050 one or more task ids not currently assigned to the proposer
  -32051 already accepted/declined
  -32052 recipient not a workspace member
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..jsonrpc import E, rpc_error
from ..types import Handoff, HandoffTask, TaskHistoryEntry

if TYPE_CHECKING:
    from ..coordinator import Coordinator


def register_handoff(coord: "Coordinator") -> None:

    def handoff_propose(p: dict) -> dict:
        ws = coord.workspaces.get(p.get("workspace", ""))
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        for f in ("from", "to", "tasks"):
            if f not in p:
                return {"error": rpc_error(E.PARAMS, f"Missing field: {f}")}

        proposer = p["from"]
        recipient = p["to"]

        # Recipient must be a workspace member or a group:... URI
        if isinstance(recipient, str) and not recipient.startswith("group:"):
            if recipient not in ws.members:
                return {"error": rpc_error(E.HANDOFF_RECIPIENT_NOT_MEMBER,
                                           f"Recipient {recipient} is not a member")}

        # tasks is a list of task descriptor objects, each carrying a task_id
        tasks_in = p["tasks"]
        if not isinstance(tasks_in, list) or not tasks_in:
            return {"error": rpc_error(E.PARAMS, "tasks must be a non-empty list")}

        proposed: list[HandoffTask] = []
        for t in tasks_in:
            tid = t.get("task_id") if isinstance(t, dict) else None
            if not tid or tid not in ws.tasks:
                return {"error": rpc_error(E.PARAMS,
                                           f"Unknown task in handoff: {tid}")}
            task = ws.tasks[tid]
            if task.assignee != proposer:
                return {"error": rpc_error(
                    E.HANDOFF_TASKS_NOT_ASSIGNED_TO_PROPOSER,
                    f"Task {tid} is not currently assigned to {proposer}")}
            proposed.append(HandoffTask(
                task_id=tid,
                title=t.get("title"),
                status_summary=t.get("status_summary"),
                next_action=t.get("next_action"),
                blockers=list(t.get("blockers") or []),
            ))

        hid = p.get("handoff_id") or coord.ids.handoff_id()
        ho = Handoff(
            id=hid,
            proposer=proposer,
            recipient=recipient,
            proposed_at=coord.now_iso(),
            tasks=proposed,
            summary=p.get("summary"),
            context_links=list(p.get("context_links") or []),
        )
        ws.handoffs[hid] = ho
        return {"result": {"handoff_id": hid, "state": "proposed",
                           "task_ids": [t.task_id for t in proposed]}}

    def handoff_accept(p: dict) -> dict:
        ws = coord.workspaces.get(p.get("workspace", ""))
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        for f in ("from", "handoff_id"):
            if f not in p:
                return {"error": rpc_error(E.PARAMS, f"Missing field: {f}")}
        ho = ws.handoffs.get(p["handoff_id"])
        if not ho:
            return {"error": rpc_error(E.PARAMS, "Unknown handoff")}
        if ho.state != "proposed":
            return {"error": rpc_error(E.HANDOFF_ALREADY_RESOLVED,
                                       f"Handoff already {ho.state}")}

        acceptor = p["from"]
        # The acceptor must be the named recipient, OR (for group recipients)
        # a workspace member. For exact-URI recipients, only that URI may accept.
        if ho.recipient.startswith("group:"):
            if acceptor not in ws.members:
                return {"error": rpc_error(E.HANDOFF_RECIPIENT_NOT_MEMBER,
                                           "Acceptor is not a workspace member")}
        else:
            if acceptor != ho.recipient:
                return {"error": rpc_error(E.HANDOFF_RECIPIENT_NOT_MEMBER,
                                           "Acceptor is not the named recipient")}
            if acceptor not in ws.members:
                return {"error": rpc_error(E.HANDOFF_RECIPIENT_NOT_MEMBER,
                                           "Acceptor is not a workspace member")}

        # Accepted tasks (default: all in the handoff)
        accepted_ids = p.get("accepted_task_ids") or [t.task_id for t in ho.tasks]
        valid_ids = {t.task_id for t in ho.tasks}
        for tid in accepted_ids:
            if tid not in valid_ids:
                return {"error": rpc_error(E.PARAMS,
                                           f"Task {tid} not in this handoff")}

        # Atomically reassign
        now = coord.now_iso()
        for tid in accepted_ids:
            task = ws.tasks[tid]
            task.assignee = acceptor
            task.updated_at = now
            task.history.append(TaskHistoryEntry(
                ts=now, from_=acceptor, state=task.state,
                note=f"handoff accepted from {ho.proposer}",
            ))

        ho.state = "accepted"
        ho.accepted_by = acceptor
        ho.accepted_task_ids = list(accepted_ids)
        ho.accept_comment = p.get("comment")
        ho.resolved_at = now
        return {"result": {"accepted": True,
                           "task_ids": list(accepted_ids),
                           "assignee": acceptor}}

    def handoff_decline(p: dict) -> dict:
        ws = coord.workspaces.get(p.get("workspace", ""))
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        for f in ("from", "handoff_id"):
            if f not in p:
                return {"error": rpc_error(E.PARAMS, f"Missing field: {f}")}
        ho = ws.handoffs.get(p["handoff_id"])
        if not ho:
            return {"error": rpc_error(E.PARAMS, "Unknown handoff")}
        if ho.state != "proposed":
            return {"error": rpc_error(E.HANDOFF_ALREADY_RESOLVED,
                                       f"Handoff already {ho.state}")}
        # Decline. For group recipients, a single decline does NOT resolve
        # the handoff (others may still accept). For exact-URI recipients,
        # the named recipient declining does resolve it.
        decliner = p["from"]
        ho.decline_reason = p.get("reason")
        ho.decline_suggested_target = p.get("suggested_target")
        if not ho.recipient.startswith("group:") and decliner == ho.recipient:
            ho.state = "declined"
            ho.resolved_at = coord.now_iso()
        return {"result": {"recorded": True, "state": ho.state}}

    coord._handlers["handoff.propose"] = handoff_propose
    coord._handlers["handoff.accept"] = handoff_accept
    coord._handlers["handoff.decline"] = handoff_decline
