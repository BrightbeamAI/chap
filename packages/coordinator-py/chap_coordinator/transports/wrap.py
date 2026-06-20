"""
chap_coordinator.transports.wrap
=================================

Inward integration helpers: turn an external tool/agent event into a
CHAP envelope landing in a workspace's audit log. The library
counterpart to the patterns described in
``integrations/CHAP-with-MCP.md`` and ``integrations/CHAP-with-A2A.md``.

The outward transports (``mcp_server.py``, ``a2a_server.py``) let an
external client *drive* a CHAP workspace. These helpers go the other
direction: when work has already happened *outside* CHAP (an MCP tool
call, an A2A message exchange), wrap it so the audit log carries a
faithful, citable record.

Spec target: CHAP 0.2. MCP 2025-11-25. A2A 1.0.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from chap_coordinator.canonical import canonicalize
from chap_coordinator.coordinator import Coordinator


__all__ = [
    "wrap_mcp_tool_call",
    "wrap_a2a_message_exchange",
    "content_hash",
]


def content_hash(value: Any) -> str:
    """Return ``sha256:<64-hex>`` of the JCS canonicalisation of ``value``.

    The same digest is used by CHAP envelopes themselves, so audit
    trails that cite an MCP call by hash can be compared against
    independently-canonicalised copies of the call's input or output.
    """
    canonical = canonicalize(value)
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def wrap_mcp_tool_call(
    coord: Coordinator,
    workspace: str,
    *,
    caller: str,
    tool: str,
    args: dict[str, Any],
    result: Any,
    server: str | None = None,
    task_kind: str | None = None,
    routing_hints: dict[str, Any] | None = None,
    confidence: float | None = None,
    review_required: bool = False,
) -> dict[str, str]:
    """Wrap a completed MCP tool call as a CHAP task pair.

    Emits ``task.create`` + ``task.complete`` envelopes into
    ``workspace`` so the MCP call shows up as a first-class audit
    entry. The result artefact is the MCP tool's return value, plus a
    ``citations[]`` entry with the input/output hashes (per the pattern
    in ``integrations/CHAP-with-MCP.md`` §2).

    Parameters
    ----------
    coord
        The CHAP Coordinator instance.
    workspace
        The workspace id to append the wrapped call to.
    caller
        Participant URI making the call. Must already be a member of
        the workspace.
    tool
        MCP tool name, e.g. ``"github.create_issue"``.
    args
        Arguments passed to the MCP tool.
    result
        Return value from the MCP tool. Failures should be passed as
        ``{"error": ...}``; the wrapper does not interpret semantics.
    server
        Optional MCP server identifier (e.g. ``"github"``,
        ``"slack"``). Recorded in the citation.
    task_kind
        Override the default task kind ``"mcp_call:<tool>"``.
    routing_hints
        Optional hints attached to the CHAP task.
    confidence
        Optional confidence value attached to ``task.complete``.
    review_required
        If True, leaves the task in ``review_requested`` state via
        ``review.request`` to the caller's manager (configured per
        deployment). Default False: completes the task immediately.

    Returns
    -------
    dict
        ``{"task_id": ..., "input_hash": ..., "output_hash": ...}``
        for downstream callers that want to cite the wrapped event.
    """
    if not workspace:
        raise ValueError("workspace is required")
    if not caller:
        raise ValueError("caller is required")
    if not tool:
        raise ValueError("tool is required")

    input_hash = content_hash(args)
    output_hash = content_hash(result)
    kind = task_kind or f"mcp_call:{tool}"

    citations = [{
        "kind":         "mcp_tool_call",
        "server":       server or "unknown",
        "tool":         tool,
        "input_hash":   input_hash,
        "output_hash":  output_hash,
    }]

    # task.create
    create_params: dict[str, Any] = {
        "workspace": workspace,
        "from":      caller,
        "kind":      kind,
        "assignee":  caller,           # self-assigned wrapper
        "input":     args,
    }
    if routing_hints:
        create_params["routing_hints"] = dict(routing_hints)

    create_resp = coord.dispatch({
        "jsonrpc": "2.0",
        "id":      f"wrap-{tool}-create",
        "method":  "task.create",
        "params":  create_params,
    })
    if "error" in create_resp:
        raise RuntimeError(f"task.create failed: {create_resp['error']}")
    task_id = create_resp["result"]["task_id"]

    # task.update -> in_progress so the lifecycle is honest.
    coord.dispatch({
        "jsonrpc": "2.0",
        "id":      f"wrap-{tool}-update",
        "method":  "task.update",
        "params":  {"workspace": workspace, "from": caller,
                    "task_id": task_id, "state": "in_progress"},
    })

    complete_params: dict[str, Any] = {
        "workspace": workspace,
        "from":      caller,
        "task_id":   task_id,
        "output":    {"result": result, "citations": citations},
    }
    if confidence is not None:
        complete_params["confidence"] = confidence

    complete_resp = coord.dispatch({
        "jsonrpc": "2.0",
        "id":      f"wrap-{tool}-complete",
        "method":  "task.complete",
        "params":  complete_params,
    })
    if "error" in complete_resp:
        raise RuntimeError(f"task.complete failed: {complete_resp['error']}")

    return {
        "task_id":     task_id,
        "input_hash":  input_hash,
        "output_hash": output_hash,
    }


def wrap_a2a_message_exchange(
    coord: Coordinator,
    workspace: str,
    *,
    bridge_uri: str,
    remote_agent: str,
    sent: dict[str, Any],
    received: dict[str, Any],
    task_kind: str = "a2a_exchange",
    routing_hints: dict[str, Any] | None = None,
    confidence: float | None = None,
) -> dict[str, str]:
    """Wrap a completed A2A message exchange as a CHAP task pair.

    Implements the bridge-participant pattern from
    ``integrations/CHAP-with-A2A.md`` §3 as a library helper.

    The bridge participant (``bridge_uri``, e.g.
    ``service:a2a-bridge``) is the CHAP-visible proxy for an external
    A2A agent (``remote_agent``, e.g. ``a2a:partner-org/agent-name``).
    Each completed exchange yields a CHAP task that records what was
    sent, what came back, and the hashes of both for downstream
    auditability.

    Parameters
    ----------
    coord
        The CHAP Coordinator instance.
    workspace
        The workspace id.
    bridge_uri
        URI of the local bridge participant. Must already be joined
        to the workspace.
    remote_agent
        Identifier of the remote A2A agent. Recorded in the citation;
        not assumed to be a workspace member.
    sent
        The A2A message body sent outbound.
    received
        The A2A message body received in response.
    task_kind
        CHAP task kind. Default ``"a2a_exchange"``.
    routing_hints
        Optional hints attached to the CHAP task.
    confidence
        Optional confidence value attached to ``task.complete``.

    Returns
    -------
    dict
        ``{"task_id": ..., "sent_hash": ..., "received_hash": ...}``
    """
    if not workspace:
        raise ValueError("workspace is required")
    if not bridge_uri:
        raise ValueError("bridge_uri is required")
    if not remote_agent:
        raise ValueError("remote_agent is required")

    sent_hash = content_hash(sent)
    received_hash = content_hash(received)

    citation = {
        "kind":          "a2a_exchange",
        "remote_agent":  remote_agent,
        "sent_hash":     sent_hash,
        "received_hash": received_hash,
    }

    create_params: dict[str, Any] = {
        "workspace": workspace,
        "from":      bridge_uri,
        "kind":      task_kind,
        "assignee":  bridge_uri,
        "input":     {"remote_agent": remote_agent, "sent": sent},
    }
    if routing_hints:
        create_params["routing_hints"] = dict(routing_hints)

    create_resp = coord.dispatch({
        "jsonrpc": "2.0", "id": "wrap-a2a-create",
        "method": "task.create", "params": create_params,
    })
    if "error" in create_resp:
        raise RuntimeError(f"task.create failed: {create_resp['error']}")
    task_id = create_resp["result"]["task_id"]

    coord.dispatch({
        "jsonrpc": "2.0", "id": "wrap-a2a-update",
        "method": "task.update",
        "params": {"workspace": workspace, "from": bridge_uri,
                   "task_id": task_id, "state": "in_progress"},
    })

    complete_params: dict[str, Any] = {
        "workspace": workspace, "from": bridge_uri, "task_id": task_id,
        "output": {"received": received, "citations": [citation]},
    }
    if confidence is not None:
        complete_params["confidence"] = confidence

    complete_resp = coord.dispatch({
        "jsonrpc": "2.0", "id": "wrap-a2a-complete",
        "method": "task.complete", "params": complete_params,
    })
    if "error" in complete_resp:
        raise RuntimeError(f"task.complete failed: {complete_resp['error']}")

    return {
        "task_id":       task_id,
        "sent_hash":     sent_hash,
        "received_hash": received_hash,
    }
