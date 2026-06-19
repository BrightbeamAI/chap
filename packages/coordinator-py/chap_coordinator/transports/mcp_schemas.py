"""
chap_coordinator.transports.mcp_schemas
========================================

JSON Schema definitions for each CHAP method, used as the
``inputSchema`` for the corresponding MCP tool. Each schema describes
the params object that the CHAP envelope would carry; the tool
handler wraps the params in a JSON-RPC 2.0 envelope before dispatch.

These schemas are tuned for MCP UX: the descriptions are what an
LLM client sees when deciding whether to call the tool, so they're
written for that audience as well as for validation.

Mirrors ``packages/coordinator-mcp/src/schemas.ts`` exactly; the two
implementations must stay in lockstep.

Aligned with CHAP 0.2 (see ``profiles/*.md``) and MCP 2025-11-25.
"""
from __future__ import annotations

from typing import Any

# Common reusable fragments.
_PARTICIPANT_URI: dict[str, Any] = {
    "type": "string",
    "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
}

_WORKSPACE_ID: dict[str, Any] = {
    "type": "string",
    "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
}

_TASK_ID: dict[str, Any] = {
    "type": "string",
    "description": "Task identifier returned by chap.task.create.",
}

_ROUTING_HINTS: dict[str, Any] = {
    "type": "object",
    "description": "Optional signals for the routing/1.0 profile.",
    "properties": {
        "criticality": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
        "deadline":    {"type": "string", "description": "ISO 8601 timestamp."},
        "risk_tier":   {"type": "string"},
        "max_cost_usd": {"type": "number"},
    },
    "additionalProperties": True,
}


SCHEMAS: dict[str, dict[str, Any]] = {
    # ============================================================
    # Core
    # ============================================================
    "chap.workspace.create": {
        "type": "object",
        "properties": {
            "workspace": {**_WORKSPACE_ID, "description": "Workspace id to create. If omitted, one is generated."},
            "profiles": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Profile identifiers to enable, e.g. ['core/1.0', 'review/1.0'].",
            },
            "mode":         {"type": "string", "enum": ["shadow", "trial", "production"], "default": "trial"},
            "mode_ceiling": {"type": "string", "enum": ["shadow", "trial", "production"], "default": "production"},
        },
        "additionalProperties": False,
    },
    "chap.workspace.describe": {
        "type": "object",
        "properties": {"workspace": _WORKSPACE_ID},
        "required": ["workspace"],
    },
    "chap.workspace.set_profiles": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID,
            "profiles":  {"type": "array", "items": {"type": "string"}},
        },
        "required": ["workspace", "profiles"],
    },
    "chap.participant.join": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID,
            "from":      _PARTICIPANT_URI,
            "type":      {"type": "string", "enum": ["human", "agent", "service", "group", "workspace"]},
            "role":      {"type": "string", "description": "Operator-defined role, e.g. 'reviewer' or 'drafter'."},
            "display_name": {"type": "string"},
        },
        "required": ["workspace", "from", "type"],
    },
    "chap.participant.leave": {
        "type": "object",
        "properties": {"workspace": _WORKSPACE_ID, "from": _PARTICIPANT_URI},
        "required": ["workspace", "from"],
    },
    "chap.task.create": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID,
            "from":      {**_PARTICIPANT_URI, "description": "Delegator URI."},
            "kind":      {"type": "string", "description": "Task kind, e.g. 'draft_response' or 'review'."},
            "assignee":  {**_PARTICIPANT_URI, "description": "Who the task is assigned to. Must be a workspace member."},
            "input":     {"type": "object", "description": "Task-specific input payload.", "additionalProperties": True},
            "routing_hints": _ROUTING_HINTS,
            "mode":      {"type": "string", "enum": ["shadow", "trial", "production"]},
        },
        "required": ["workspace", "from", "kind", "assignee", "input"],
    },
    "chap.task.update": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID,
            "from":      _PARTICIPANT_URI,
            "task_id":   _TASK_ID,
            "state":     {"type": "string", "enum": ["in_progress", "review_requested", "declined", "paused", "cancelled", "completed"]},
            "progress_note": {"type": "string"},
        },
        "required": ["workspace", "from", "task_id", "state"],
    },
    "chap.task.complete": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID,
            "from":      _PARTICIPANT_URI,
            "task_id":   _TASK_ID,
            "output":    {"description": "The task's output artefact."},
            "confidence": {"type": "number", "description": "Self-reported confidence (0-1)."},
            "routing_hints": _ROUTING_HINTS,
        },
        "required": ["workspace", "from", "task_id"],
    },
    "chap.audit.read": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID,
            "range": {
                "type": "object",
                "properties": {
                    "from_seq": {"type": "integer"},
                    "to_seq":   {"type": "integer"},
                },
            },
            "filter": {
                "type": "object",
                "properties": {
                    "method":  {"type": "string"},
                    "from":    {"type": "string"},
                    "task_id": {"type": "string"},
                },
            },
        },
        "required": ["workspace"],
    },
    # ============================================================
    # review/1.0
    # ============================================================
    "chap.review.request": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID,
            "from":      _PARTICIPANT_URI,
            "task_id":   _TASK_ID,
            "to": {
                "oneOf": [_PARTICIPANT_URI, {"type": "array", "items": _PARTICIPANT_URI}],
                "description": "One or more reviewers.",
            },
            "rule": {
                "type": "string",
                "enum": ["any_one_approves", "all_approve", "quorum:2", "quorum:3"],
                "default": "any_one_approves",
            },
            "artefact": {"description": "The draft being submitted for review."},
            "deadline": {"type": "string"},
        },
        "required": ["workspace", "from", "task_id", "to", "artefact"],
    },
    "chap.decide.approve": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID, "from": _PARTICIPANT_URI, "task_id": _TASK_ID,
            "comment": {"type": "string"},
            "tags":    {"type": "array", "items": {"type": "string"}},
        },
        "required": ["workspace", "from", "task_id"],
    },
    "chap.decide.reject": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID, "from": _PARTICIPANT_URI, "task_id": _TASK_ID,
            "comment": {"type": "string"},
            "tags":    {"type": "array", "items": {"type": "string"}},
            "request_revision": {"type": "boolean"},
        },
        "required": ["workspace", "from", "task_id"],
    },
    "chap.decide.override": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID, "from": _PARTICIPANT_URI, "task_id": _TASK_ID,
            "diff": {
                "type": "array",
                "description": "RFC 6902 JSON Patch operations applied to the artefact under review.",
                "items": {
                    "type": "object",
                    "properties": {
                        "op":    {"type": "string", "enum": ["add", "replace", "remove", "copy", "move", "test"]},
                        "path":  {"type": "string"},
                        "value": {},
                        "from":  {"type": "string"},
                    },
                    "required": ["op", "path"],
                },
            },
            "rationale": {"type": "string", "description": "Why the override was applied."},
            "tags":      {"type": "array", "items": {"type": "string"}},
            "policy_refs": {"type": "array", "items": {"type": "string"}},
            "logical_id": {"type": "string"},
            "intent_preserved": {"type": "boolean"},
        },
        "required": ["workspace", "from", "task_id", "diff", "rationale"],
    },
    "chap.abstain.declare": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID, "from": _PARTICIPANT_URI, "task_id": _TASK_ID,
            "reason":   {"type": "string"},
            "category": {"type": "string", "enum": ["conflict_of_interest", "insufficient_context", "out_of_scope", "other"]},
        },
        "required": ["workspace", "from", "task_id", "reason"],
    },
    "chap.escalate.raise": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID, "from": _PARTICIPANT_URI,
            "original_task_id": _TASK_ID,
            "new_task": {
                "type": "object",
                "properties": {
                    "kind":     {"type": "string"},
                    "assignee": _PARTICIPANT_URI,
                    "input":    {"type": "object", "additionalProperties": True},
                },
                "required": ["assignee"],
            },
        },
        "required": ["workspace", "from", "original_task_id", "new_task"],
    },
    # ============================================================
    # whisper/1.0
    # ============================================================
    "chap.whisper.ask": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID, "from": _PARTICIPANT_URI, "task_id": _TASK_ID,
            "to": {"type": "array", "items": _PARTICIPANT_URI},
            "question": {"type": "string"},
            "options": {
                "type": "array",
                "items": {"type": "object", "properties": {"id": {"type": "string"}, "label": {"type": "string"}}, "required": ["id"]},
            },
            "deadline_ms": {"type": "integer"},
            "default_if_lapsed": {"description": "Value applied if the whisper lapses."},
            "urgency": {"type": "string", "enum": ["low", "medium", "high"], "default": "low"},
        },
        "required": ["workspace", "from", "task_id", "to", "question", "deadline_ms", "default_if_lapsed"],
    },
    "chap.whisper.answer": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID, "from": _PARTICIPANT_URI,
            "whisper_id": {"type": "string"},
            "answer_option": {"type": "string"},
            "answer":        {"type": "string"},
            "comment":       {"type": "string"},
        },
        "required": ["workspace", "from", "whisper_id"],
    },
    # ============================================================
    # deliberation/1.0
    # ============================================================
    "chap.deliberate.open": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID, "from": _PARTICIPANT_URI,
            "to": {"type": "array", "items": _PARTICIPANT_URI},
            "task_id": _TASK_ID,
            "rule": {"type": "string"},
            "question": {"type": "string"},
            "weights":  {"type": "object", "additionalProperties": {"type": "number"}},
            "veto":     {"type": "object", "additionalProperties": {"type": "boolean"}},
            "deadline": {"type": "string"},
        },
        "required": ["workspace", "from", "to", "rule"],
    },
    "chap.deliberate.comment": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID, "from": _PARTICIPANT_URI,
            "deliberation_id": {"type": "string"},
            "comment": {"type": "string"},
        },
        "required": ["workspace", "from", "deliberation_id", "comment"],
    },
    "chap.deliberate.vote": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID, "from": _PARTICIPANT_URI,
            "deliberation_id": {"type": "string"},
            "vote": {"type": "string", "enum": ["yea", "nay", "abstain"]},
            "weight": {"type": "number"},
            "comment": {"type": "string"},
            "veto_invoked": {"type": "boolean"},
        },
        "required": ["workspace", "from", "deliberation_id", "vote"],
    },
    "chap.deliberate.close": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID, "from": _PARTICIPANT_URI,
            "deliberation_id": {"type": "string"},
        },
        "required": ["workspace", "from", "deliberation_id"],
    },
    # ============================================================
    # handoff/1.0
    # ============================================================
    "chap.handoff.propose": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID, "from": _PARTICIPANT_URI,
            "to": {"type": "string", "description": "Recipient (URI or 'group:...')."},
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "task_id": _TASK_ID,
                        "title":   {"type": "string"},
                        "status_summary": {"type": "string"},
                        "next_action":    {"type": "string"},
                        "blockers":       {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["task_id"],
                },
            },
            "summary": {"type": "string"},
            "context_links": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["workspace", "from", "to", "tasks"],
    },
    "chap.handoff.accept": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID, "from": _PARTICIPANT_URI,
            "handoff_id": {"type": "string"},
            "accepted_task_ids": {"type": "array", "items": _TASK_ID},
            "comment": {"type": "string"},
        },
        "required": ["workspace", "from", "handoff_id"],
    },
    "chap.handoff.decline": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID, "from": _PARTICIPANT_URI,
            "handoff_id": {"type": "string"},
            "reason": {"type": "string"},
            "suggested_target": _PARTICIPANT_URI,
        },
        "required": ["workspace", "from", "handoff_id"],
    },
    # ============================================================
    # control/1.0
    # ============================================================
    "chap.control.pause": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID, "from": _PARTICIPANT_URI,
            "scope":           {"type": "string", "enum": ["task", "participant", "workspace"], "default": "task"},
            "task_id":         _TASK_ID,
            "participant_uri": _PARTICIPANT_URI,
            "in_flight_policy": {"type": "string", "enum": ["allow_to_complete", "interrupt"]},
            "reason": {"type": "string"},
        },
        "required": ["workspace", "from"],
    },
    "chap.control.resume": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID, "from": _PARTICIPANT_URI,
            "scope": {"type": "string", "enum": ["task", "participant", "workspace"], "default": "task"},
            "task_id": _TASK_ID,
            "participant_uri": _PARTICIPANT_URI,
        },
        "required": ["workspace", "from"],
    },
    "chap.control.cancel": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID, "from": _PARTICIPANT_URI,
            "task_id": _TASK_ID,
            "reason":  {"type": "string"},
        },
        "required": ["workspace", "from", "task_id"],
    },
    "chap.control.snapshot": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID, "from": _PARTICIPANT_URI,
            "label":   {"type": "string"},
            "include": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["workspace", "from"],
    },
    "chap.control.rollback": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID, "from": _PARTICIPANT_URI,
            "to_snapshot_artefact_id": {"type": "string"},
            "what_to_restore": {"type": "array", "items": {"type": "string"}},
            "reason": {"type": "string"},
        },
        "required": ["workspace", "from", "to_snapshot_artefact_id"],
    },
    "chap.control.supersede": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID, "from": _PARTICIPANT_URI,
            "task_id": _TASK_ID,
            "successor_task": {
                "type": "object",
                "properties": {
                    "kind":     {"type": "string"},
                    "assignee": _PARTICIPANT_URI,
                    "input":    {"type": "object", "additionalProperties": True},
                },
                "required": ["kind"],
            },
            "reason": {"type": "string"},
        },
        "required": ["workspace", "from", "task_id", "successor_task"],
    },
    "chap.control.set_mode_ceiling": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID, "from": _PARTICIPANT_URI,
            "new_ceiling": {"type": "string", "enum": ["shadow", "trial", "production"]},
            "reason":      {"type": "string"},
        },
        "required": ["workspace", "from", "new_ceiling"],
    },
    # ============================================================
    # routing/1.0
    # ============================================================
    "chap.task.route": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID, "from": _PARTICIPANT_URI,
            "task_id": _TASK_ID,
            "candidates": {"type": "array", "items": _PARTICIPANT_URI},
        },
        "required": ["workspace", "from", "task_id", "candidates"],
    },
    "chap.review.depth": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID, "from": _PARTICIPANT_URI,
            "task_id": _TASK_ID,
            "artefact_routing_hints": {"type": "object", "additionalProperties": True},
        },
        "required": ["workspace", "from", "task_id"],
    },
    "chap.escalate.auto": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID, "from": _PARTICIPANT_URI,
            "task_id": _TASK_ID,
            "default_escalation_target": _PARTICIPANT_URI,
        },
        "required": ["workspace", "from", "task_id"],
    },
    # ============================================================
    # security-signed/1.0
    # ============================================================
    "chap.participant.rotate_key": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID, "from": _PARTICIPANT_URI,
            "old_kid": {"type": "string"},
            "new_jwk": {"type": "object", "additionalProperties": True},
        },
        "required": ["workspace", "from", "old_kid", "new_jwk"],
    },
    "chap.participant.revoke_key": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID, "from": _PARTICIPANT_URI,
            "target_uri": _PARTICIPANT_URI,
            "kid": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["workspace", "from", "target_uri", "kid"],
    },
    # ============================================================
    # audit-scitt/1.0
    # ============================================================
    "chap.audit.submit_to_scitt": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID, "from": _PARTICIPANT_URI,
            "range": {
                "type": "object",
                "properties": {
                    "from_seq": {"type": "integer"},
                    "to_seq":   {"type": "integer"},
                },
            },
            "issuer": {"type": "string"},
        },
        "required": ["workspace"],
    },
    "chap.audit.verify_receipt": {
        "type": "object",
        "properties": {
            "workspace": _WORKSPACE_ID, "from": _PARTICIPANT_URI,
            "receipt":   {"type": "object", "additionalProperties": True},
        },
        "required": ["workspace", "receipt"],
    },
    "chap.audit.verify_chain": {
        "type": "object",
        "properties": {"workspace": _WORKSPACE_ID, "from": _PARTICIPANT_URI},
        "required": ["workspace"],
    },
}


TOOL_NAMES: list[str] = list(SCHEMAS.keys())


def schema_for(tool_name: str) -> dict[str, Any] | None:
    """Return the JSON Schema for a tool, or ``None`` if unknown."""
    return SCHEMAS.get(tool_name)


def method_for_tool(tool_name: str) -> str | None:
    """Map an MCP tool name back to its CHAP method name."""
    if not tool_name.startswith("chap."):
        return None
    return tool_name[len("chap."):]
