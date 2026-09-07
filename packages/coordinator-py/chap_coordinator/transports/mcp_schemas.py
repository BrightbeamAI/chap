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

Aligned with CHAP 0.2 (see ``profiles/*.md``) and MCP 2026-07-28.
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
    "description": "A workspace.",
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


# --- BEGIN GENERATED SCHEMAS (scripts/sync-mcp-schemas.mjs) ---
# Generated from packages/coordinator-mcp/src/schemas.ts. Do not edit by
# hand: edit the TypeScript table and run scripts/sync-mcp-schemas.mjs.
SCHEMAS: dict[str, dict[str, Any]] = {
    "chap.workspace.create": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace id to create. If omitted, one is generated.",
            },
            "profiles": {
                "type": "array",
                "items": {
                    "type": "string",
                },
                "description": "Profile identifiers to enable, e.g. ['core/1.0', 'review/1.0'].",
            },
            "mode": {
                "type": "string",
                "enum": [
                    "shadow",
                    "trial",
                    "production",
                ],
                "default": "trial",
                "description": "Starting mode for tasks here, under modes/1.0: shadow runs without delivering output, trial delivers under mandatory review, production delivers per policy. Inert unless that profile is loaded.",
            },
            "mode_ceiling": {
                "type": "string",
                "enum": [
                    "shadow",
                    "trial",
                    "production",
                ],
                "default": "production",
                "description": "Highest mode any task in this workspace may use. Raising it later needs elevated privilege, so it is a safety bound rather than a default.",
            },
        },
        "additionalProperties": False,
    },

    "chap.workspace.describe": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
        },
        "required": [
            "workspace",
        ],
    },

    "chap.workspace.set_profiles": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "profiles": {
                "type": "array",
                "items": {
                    "type": "string",
                },
                "description": "The complete profile set to enable, replacing the current one. Adding audit-scitt/1.0 to a workspace with existing entries leaves those entries unchained and outside chain verification.",
            },
        },
        "required": [
            "workspace",
            "profiles",
        ],
    },

    "chap.participant.join": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
            "type": {
                "type": "string",
                "enum": [
                    "human",
                    "agent",
                    "service",
                    "group",
                    "workspace",
                ],
                "description": "What kind of participant this is. It is load-bearing: only 'human' members are eligible for the review a required task opens on completion.",
            },
            "role": {
                "type": "string",
                "description": "Operator-defined role, e.g. 'reviewer' or 'drafter'.",
            },
            "display_name": {
                "type": "string",
                "description": "Human-readable name shown in interfaces. Does not affect authorisation.",
            },
        },
        "required": [
            "workspace",
            "from",
            "type",
        ],
    },

    "chap.participant.leave": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
        },
        "required": [
            "workspace",
            "from",
        ],
    },

    "chap.task.create": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Delegator URI.",
            },
            "kind": {
                "type": "string",
                "description": "Task kind, e.g. 'draft_response' or 'review'.",
            },
            "assignee": {
                "type": "string",
                "description": "Who the task is assigned to. Must be a workspace member.",
            },
            "input": {
                "type": "object",
                "description": "Task-specific input payload.",
                "additionalProperties": True,
            },
            "routing_hints": {
                "type": "object",
                "description": "Optional signals the routing/1.0 profile uses to pick an assignee and a review depth. Ignored when that profile is not loaded.",
                "properties": {
                    "criticality": {
                        "type": "string",
                        "enum": [
                            "low",
                            "medium",
                            "high",
                            "critical",
                        ],
                        "description": "How much a wrong answer costs. Higher criticality pushes towards a more capable assignee and a fuller review.",
                    },
                    "deadline": {
                        "type": "string",
                        "description": "When the work is needed by, as an ISO 8601 timestamp.",
                    },
                    "risk_tier": {
                        "type": "string",
                        "description": "Operator-defined risk band, e.g. 'regulated' or 'internal'. Interpreted by your routing policy.",
                    },
                    "max_cost_usd": {
                        "type": "number",
                        "description": "Budget ceiling for this task in US dollars, for policies that price candidates.",
                    },
                },
                "additionalProperties": True,
            },
            "mode": {
                "type": "string",
                "enum": [
                    "shadow",
                    "trial",
                    "production",
                ],
                "description": "Mode for this task under modes/1.0, defaulting to the workspace mode. Refused if it exceeds the workspace's mode_ceiling.",
            },
            "review_required": {
                "type": "boolean",
                "description": "When true, chap.task.complete opens a review instead of completing: the output becomes the artefact under review and only a reviewer decision finishes the task.",
            },
            "deadline": {
                "type": "string",
                "description": "When the task is due, as an ISO 8601 timestamp.",
            },
            "idempotency_key": {
                "type": "string",
                "description": "Caller-chosen key for safe retries. A repeat carrying a key already seen in this workspace returns the original task rather than creating a second one.",
            },
        },
        "required": [
            "workspace",
            "from",
            "kind",
            "assignee",
            "input",
        ],
    },

    "chap.task.update": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
            "task_id": {
                "type": "string",
                "description": "Task identifier returned by chap.task.create.",
            },
            "state": {
                "type": "string",
                "enum": [
                    "in_progress",
                    "review_requested",
                    "declined",
                    "paused",
                    "cancelled",
                    "completed",
                ],
                "description": "The state to move the task to. Only the transitions in SPECIFICATION.md 8.1 are legal from the task's current state; anything else is refused with -32602.",
            },
            "progress_note": {
                "type": "string",
                "description": "Short note on what changed, kept in the task's history.",
            },
        },
        "required": [
            "workspace",
            "from",
            "task_id",
            "state",
        ],
    },

    "chap.task.complete": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
            "task_id": {
                "type": "string",
                "description": "Task identifier returned by chap.task.create.",
            },
            "output": {
                "description": "The task's output artefact. Pass as a JSON object or array, not a JSON-encoded string.",
            },
            "confidence": {
                "type": "number",
                "description": "Self-reported confidence (0-1), where supported.",
            },
            "routing_hints": {
                "type": "object",
                "description": "Optional signals the routing/1.0 profile uses to pick an assignee and a review depth. Ignored when that profile is not loaded.",
                "properties": {
                    "criticality": {
                        "type": "string",
                        "enum": [
                            "low",
                            "medium",
                            "high",
                            "critical",
                        ],
                        "description": "How much a wrong answer costs. Higher criticality pushes towards a more capable assignee and a fuller review.",
                    },
                    "deadline": {
                        "type": "string",
                        "description": "When the work is needed by, as an ISO 8601 timestamp.",
                    },
                    "risk_tier": {
                        "type": "string",
                        "description": "Operator-defined risk band, e.g. 'regulated' or 'internal'. Interpreted by your routing policy.",
                    },
                    "max_cost_usd": {
                        "type": "number",
                        "description": "Budget ceiling for this task in US dollars, for policies that price candidates.",
                    },
                },
                "additionalProperties": True,
            },
        },
        "required": [
            "workspace",
            "from",
            "task_id",
        ],
    },

    "chap.audit.read": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "range": {
                "type": "object",
                "description": "Sequence window to return. Omit for the whole log.",
                "properties": {
                    "from_seq": {
                        "type": "integer",
                        "description": "Start sequence number (inclusive).",
                    },
                    "to_seq": {
                        "type": "integer",
                        "description": "End sequence number (exclusive).",
                    },
                },
            },
            "filter": {
                "type": "object",
                "description": "Narrow the entries returned. Filters combine with AND.",
                "properties": {
                    "method": {
                        "type": "string",
                        "description": "Return only entries for this CHAP method, e.g. 'decide.override'.",
                    },
                    "from": {
                        "type": "string",
                        "description": "Return only entries whose actor is this participant URI.",
                    },
                    "task_id": {
                        "type": "string",
                        "description": "Return only entries about this task.",
                    },
                },
            },
        },
        "required": [
            "workspace",
        ],
    },

    "chap.review.request": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
            "task_id": {
                "type": "string",
                "description": "Task identifier returned by chap.task.create.",
            },
            "to": {
                "oneOf": [
                    {
                        "type": "string",
                        "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
                    },
                    {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
                        },
                    },
                ],
                "description": "One or more reviewers. A single URI string, or a JSON array of URI strings (not a JSON-encoded string).",
            },
            "rule": {
                "type": "string",
                "enum": [
                    "any_one_approves",
                    "all_approve",
                    "quorum:2",
                    "quorum:3",
                ],
                "default": "any_one_approves",
                "description": "How many of the addressed reviewers must approve before the task completes. Fixed for the life of the review: a later request that changes it is refused with -32014.",
            },
            "artefact": {
                "description": "The draft being submitted for review. Pass as a JSON object or array, not a JSON-encoded string.",
            },
            "deadline": {
                "type": "string",
                "description": "When the review is needed by, as an ISO 8601 timestamp.",
            },
        },
        "required": [
            "workspace",
            "from",
            "task_id",
            "to",
            "artefact",
        ],
    },

    "chap.decide.approve": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
            "task_id": {
                "type": "string",
                "description": "Task identifier returned by chap.task.create.",
            },
            "approved_artefact_digest": {
                "type": "string",
                "pattern": "^sha256:[0-9a-f]{64}$",
                "description": "Optional. SHA-256 over the JCS canonicalisation of the artefact under review, as `sha256:<hex>`. Binds the decision to the exact content reviewed; refused with -32074 on mismatch.",
            },
            "comment": {
                "type": "string",
                "description": "The reviewer's note on this decision, recorded in the audit log alongside it.",
            },
            "tags": {
                "type": "array",
                "items": {
                    "type": "string",
                },
                "description": "Workspace-defined labels for this decision, e.g. ['tone', 'unsupported-claim']. Querying the audit log by tag is how recurring correction patterns are found.",
            },
        },
        "required": [
            "workspace",
            "from",
            "task_id",
        ],
    },

    "chap.decide.reject": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
            "task_id": {
                "type": "string",
                "description": "Task identifier returned by chap.task.create.",
            },
            "approved_artefact_digest": {
                "type": "string",
                "pattern": "^sha256:[0-9a-f]{64}$",
                "description": "Optional. SHA-256 over the JCS canonicalisation of the artefact under review, as `sha256:<hex>`. Binds the decision to the exact content reviewed; refused with -32074 on mismatch.",
            },
            "comment": {
                "type": "string",
                "description": "The reviewer's note on this decision, recorded in the audit log alongside it.",
            },
            "tags": {
                "type": "array",
                "items": {
                    "type": "string",
                },
                "description": "Workspace-defined labels for this decision, e.g. ['tone', 'unsupported-claim']. Querying the audit log by tag is how recurring correction patterns are found.",
            },
            "request_revision": {
                "type": "boolean",
                "description": "If true, task returns to in_progress instead of declined.",
            },
        },
        "required": [
            "workspace",
            "from",
            "task_id",
        ],
    },

    "chap.decide.override": {
        "type": "object",
        "properties": {
            "approved_artefact_digest": {
                "type": "string",
                "pattern": "^sha256:[0-9a-f]{64}$",
                "description": "Optional. SHA-256 over the JCS canonicalisation of the artefact under review, as `sha256:<hex>`. Binds the decision to the exact content reviewed; refused with -32074 on mismatch.",
            },
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
            "task_id": {
                "type": "string",
                "description": "Task identifier returned by chap.task.create.",
            },
            "diff": {
                "type": "array",
                "description": "RFC 6902 JSON Patch operations applied to the artefact under review.",
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {
                            "type": "string",
                            "enum": [
                                "add",
                                "replace",
                                "remove",
                                "copy",
                                "move",
                                "test",
                            ],
                            "description": "The patch operation to apply.",
                        },
                        "path": {
                            "type": "string",
                            "description": "JSON Pointer into the artefact, e.g. '/body' or '/items/0/severity'.",
                        },
                        "value": {
                            "description": "The new value, for add, replace and test.",
                        },
                        "from": {
                            "type": "string",
                            "description": "Source JSON Pointer, for copy and move.",
                        },
                    },
                    "required": [
                        "op",
                        "path",
                    ],
                },
            },
            "rationale": {
                "type": "string",
                "description": "Why the override was applied. Required for the structured-override audit trail.",
            },
            "tags": {
                "type": "array",
                "items": {
                    "type": "string",
                },
                "description": "Workspace-defined labels for this decision, e.g. ['tone', 'unsupported-claim']. Querying the audit log by tag is how recurring correction patterns are found.",
            },
            "policy_refs": {
                "type": "array",
                "items": {
                    "type": "string",
                },
                "description": "Identifiers of the policies or guidelines this correction applies, e.g. ['policy:no-delivery-promises']. Lets an auditor trace a decision back to the rule behind it.",
            },
            "logical_id": {
                "type": "string",
                "description": "Durable handle for the thing being decided, shared across revisions and overrides of the same underlying artefact.",
            },
            "intent_preserved": {
                "type": "boolean",
                "description": "True when the edit refines the same decision the draft was making, false when it substitutes a different one. Separates wording fixes from reversals in the override report.",
            },
        },
        "required": [
            "workspace",
            "from",
            "task_id",
            "diff",
            "rationale",
        ],
    },

    "chap.abstain.declare": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
            "task_id": {
                "type": "string",
                "description": "Task identifier returned by chap.task.create.",
            },
            "reason": {
                "type": "string",
                "description": "Why this reviewer is standing aside, recorded in the audit log.",
            },
            "category": {
                "type": "string",
                "enum": [
                    "conflict_of_interest",
                    "insufficient_context",
                    "out_of_scope",
                    "other",
                ],
                "description": "The kind of abstention, so recurring gaps in reviewer coverage can be counted rather than read.",
            },
        },
        "required": [
            "workspace",
            "from",
            "task_id",
            "reason",
        ],
    },

    "chap.escalate.raise": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
            "original_task_id": {
                "type": "string",
                "description": "Task identifier returned by chap.task.create.",
            },
            "new_task": {
                "type": "object",
                "description": "The successor task to open for the escalation target. The original is marked escalated and linked to it.",
                "properties": {
                    "kind": {
                        "type": "string",
                        "description": "Task kind for the successor, defaulting to the original's kind.",
                    },
                    "assignee": {
                        "type": "string",
                        "description": "Who the escalation goes to. Must be a workspace member.",
                    },
                    "input": {
                        "type": "object",
                        "additionalProperties": True,
                        "description": "Input payload for the successor, typically the original input plus the context that prompted the escalation.",
                    },
                },
                "required": [
                    "assignee",
                ],
            },
        },
        "required": [
            "workspace",
            "from",
            "original_task_id",
            "new_task",
        ],
    },

    "chap.whisper.ask": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
            "task_id": {
                "type": "string",
                "description": "Task identifier returned by chap.task.create.",
            },
            "to": {
                "type": "array",
                "items": {
                    "type": "string",
                    "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
                },
                "description": "Who is being asked.",
            },
            "question": {
                "type": "string",
                "description": "The single question to put to them, short enough to answer without opening the task.",
            },
            "options": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "Stable identifier the answer refers to.",
                        },
                        "label": {
                            "type": "string",
                            "description": "What the option says to the person answering.",
                        },
                    },
                    "required": [
                        "id",
                    ],
                },
                "description": "Optional multiple-choice options. If present, answer_option must be one of these ids.",
            },
            "deadline_ms": {
                "type": "integer",
                "description": "Time-to-live in milliseconds from now.",
            },
            "default_if_lapsed": {
                "description": "Value applied if the whisper lapses without an answer.",
            },
            "urgency": {
                "type": "string",
                "enum": [
                    "low",
                    "medium",
                    "high",
                ],
                "default": "low",
                "description": "How hard to push for an answer before the deadline. Advisory: it does not change the lapse behaviour.",
            },
        },
        "required": [
            "workspace",
            "from",
            "task_id",
            "to",
            "question",
            "deadline_ms",
            "default_if_lapsed",
        ],
    },

    "chap.whisper.answer": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
            "whisper_id": {
                "type": "string",
                "description": "Identifier returned by chap.whisper.ask.",
            },
            "answer_option": {
                "type": "string",
                "description": "Option id (required when the whisper has options).",
            },
            "answer": {
                "type": "string",
                "description": "Free-text answer (when no options).",
            },
            "comment": {
                "type": "string",
                "description": "Anything the answerer wants on the record beyond the answer itself.",
            },
        },
        "required": [
            "workspace",
            "from",
            "whisper_id",
        ],
    },

    "chap.deliberate.open": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
            "to": {
                "type": "array",
                "items": {
                    "type": "string",
                    "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
                },
                "description": "Deliberation participants.",
            },
            "task_id": {
                "type": "string",
                "description": "Task identifier returned by chap.task.create.",
            },
            "rule": {
                "type": "string",
                "description": "Voting rule. Examples: any_one_approves, all_approve, quorum:N, weighted_vote:T, weighted_vote_with_veto:T.",
            },
            "question": {
                "type": "string",
                "description": "What the group is deciding, stated so a yea or nay is unambiguous.",
            },
            "weights": {
                "type": "object",
                "additionalProperties": {
                    "type": "number",
                },
                "description": "Voter -> weight map (for weighted rules).",
            },
            "veto": {
                "type": "object",
                "additionalProperties": {
                    "type": "boolean",
                },
                "description": "Voter -> can-veto map.",
            },
            "deadline": {
                "type": "string",
                "description": "When voting closes, as an ISO 8601 timestamp.",
            },
        },
        "required": [
            "workspace",
            "from",
            "to",
            "rule",
        ],
    },

    "chap.deliberate.comment": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
            "deliberation_id": {
                "type": "string",
                "description": "Identifier returned by chap.deliberate.open.",
            },
            "comment": {
                "type": "string",
                "description": "The contribution to record. Comments are part of the audit trail, so the reasoning survives the vote.",
            },
        },
        "required": [
            "workspace",
            "from",
            "deliberation_id",
            "comment",
        ],
    },

    "chap.deliberate.vote": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
            "deliberation_id": {
                "type": "string",
                "description": "Identifier returned by chap.deliberate.open.",
            },
            "vote": {
                "type": "string",
                "enum": [
                    "yea",
                    "nay",
                    "abstain",
                ],
                "description": "This voter's position. Abstain is recorded and does not count towards the rule.",
            },
            "weight": {
                "type": "number",
                "description": "Weight to apply, for weighted rules. Defaults to the weight set when the deliberation was opened.",
            },
            "comment": {
                "type": "string",
                "description": "Why the vote went this way, recorded with it.",
            },
            "veto_invoked": {
                "type": "boolean",
                "description": "Set by a voter with veto rights to block the outcome regardless of the tally. Only honoured under a veto rule.",
            },
        },
        "required": [
            "workspace",
            "from",
            "deliberation_id",
            "vote",
        ],
    },

    "chap.deliberate.close": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
            "deliberation_id": {
                "type": "string",
                "description": "Identifier returned by chap.deliberate.open.",
            },
        },
        "required": [
            "workspace",
            "from",
            "deliberation_id",
        ],
    },

    "chap.handoff.propose": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
            "to": {
                "type": "string",
                "description": "Recipient (URI or 'group:...').",
            },
            "tasks": {
                "type": "array",
                "description": "The work being handed over, one entry per task, each carrying the context the recipient needs to pick it up cold.",
                "items": {
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "Task identifier returned by chap.task.create.",
                        },
                        "title": {
                            "type": "string",
                            "description": "Short label for the task, so the recipient can scan the list.",
                        },
                        "status_summary": {
                            "type": "string",
                            "description": "Where the work has got to and anything already tried.",
                        },
                        "next_action": {
                            "type": "string",
                            "description": "The one thing the recipient should do first.",
                        },
                        "blockers": {
                            "type": "array",
                            "items": {
                                "type": "string",
                            },
                            "description": "What is stopping progress, if anything.",
                        },
                    },
                    "required": [
                        "task_id",
                    ],
                },
            },
            "summary": {
                "type": "string",
                "description": "Covering note for the handover as a whole, above the per-task detail.",
            },
            "context_links": {
                "type": "array",
                "items": {
                    "type": "string",
                },
                "description": "URLs to threads, tickets or documents the recipient will need.",
            },
        },
        "required": [
            "workspace",
            "from",
            "to",
            "tasks",
        ],
    },

    "chap.handoff.accept": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
            "handoff_id": {
                "type": "string",
                "description": "Identifier returned by chap.handoff.propose.",
            },
            "accepted_task_ids": {
                "type": "array",
                "items": {
                    "type": "string",
                    "description": "Task identifier returned by chap.task.create.",
                },
                "description": "If omitted, all proposed tasks are accepted.",
            },
            "comment": {
                "type": "string",
                "description": "Anything the recipient wants on the record when taking the work on.",
            },
        },
        "required": [
            "workspace",
            "from",
            "handoff_id",
        ],
    },

    "chap.handoff.decline": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
            "handoff_id": {
                "type": "string",
                "description": "Identifier returned by chap.handoff.propose.",
            },
            "reason": {
                "type": "string",
                "description": "Why the handover is being refused, recorded so the proposer can route it elsewhere.",
            },
            "suggested_target": {
                "type": "string",
                "description": "Who should take it instead, if the decliner knows.",
            },
        },
        "required": [
            "workspace",
            "from",
            "handoff_id",
        ],
    },

    "chap.control.pause": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
            "scope": {
                "type": "string",
                "enum": [
                    "task",
                    "participant",
                    "workspace",
                ],
                "default": "task",
                "description": "What the control applies to: one task, everything a participant is doing, or the whole workspace.",
            },
            "task_id": {
                "type": "string",
                "description": "Task identifier returned by chap.task.create.",
            },
            "participant_uri": {
                "type": "string",
                "description": "Whose work to pause, when scope is 'participant'.",
            },
            "in_flight_policy": {
                "type": "string",
                "enum": [
                    "allow_to_complete",
                    "interrupt",
                ],
                "description": "What happens to work already under way: let it finish, or stop it where it stands.",
            },
            "reason": {
                "type": "string",
                "description": "Why the pause was applied, recorded in the audit log.",
            },
        },
        "required": [
            "workspace",
            "from",
        ],
    },

    "chap.control.resume": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
            "scope": {
                "type": "string",
                "enum": [
                    "task",
                    "participant",
                    "workspace",
                ],
                "default": "task",
                "description": "What the control applies to: one task, everything a participant is doing, or the whole workspace.",
            },
            "task_id": {
                "type": "string",
                "description": "Task identifier returned by chap.task.create.",
            },
            "participant_uri": {
                "type": "string",
                "description": "Whose work to resume, when scope is 'participant'.",
            },
        },
        "required": [
            "workspace",
            "from",
        ],
    },

    "chap.control.cancel": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
            "task_id": {
                "type": "string",
                "description": "Task identifier returned by chap.task.create.",
            },
            "reason": {
                "type": "string",
                "description": "Why the task was cancelled, recorded in the audit log.",
            },
        },
        "required": [
            "workspace",
            "from",
            "task_id",
        ],
    },

    "chap.control.snapshot": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
            "label": {
                "type": "string",
                "description": "Name for this snapshot, so a later rollback can be described in words rather than an id.",
            },
            "include": {
                "type": "array",
                "items": {
                    "type": "string",
                },
                "description": "Aspects to snapshot, e.g. ['members', 'open_tasks', 'mode_ceiling'].",
            },
        },
        "required": [
            "workspace",
            "from",
        ],
    },

    "chap.control.rollback": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
            "to_snapshot_artefact_id": {
                "type": "string",
                "description": "Artefact id returned by chap.control.snapshot, naming the state to restore.",
            },
            "what_to_restore": {
                "type": "array",
                "items": {
                    "type": "string",
                },
                "description": "Which aspects of the snapshot to apply, e.g. ['members', 'mode_ceiling']. Omit to restore everything it captured.",
            },
            "reason": {
                "type": "string",
                "description": "Why the rollback was performed. The rollback is itself an audit entry; the state it restores is not rewritten.",
            },
        },
        "required": [
            "workspace",
            "from",
            "to_snapshot_artefact_id",
        ],
    },

    "chap.control.supersede": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
            "task_id": {
                "type": "string",
                "description": "Task identifier returned by chap.task.create.",
            },
            "successor_task": {
                "type": "object",
                "description": "The replacement task. The superseded one stays in the chain, linked to this successor, rather than being deleted.",
                "properties": {
                    "kind": {
                        "type": "string",
                        "description": "Task kind for the successor.",
                    },
                    "assignee": {
                        "type": "string",
                        "description": "Who takes the replacement task on. Defaults to the superseded task's assignee.",
                    },
                    "input": {
                        "type": "object",
                        "additionalProperties": True,
                        "description": "Input payload for the successor.",
                    },
                },
                "required": [
                    "kind",
                ],
            },
            "reason": {
                "type": "string",
                "description": "Why the original is being replaced, recorded in the audit log.",
            },
        },
        "required": [
            "workspace",
            "from",
            "task_id",
            "successor_task",
        ],
    },

    "chap.control.set_mode_ceiling": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
            "new_ceiling": {
                "type": "string",
                "enum": [
                    "shadow",
                    "trial",
                    "production",
                ],
                "description": "The highest mode tasks in this workspace may use from now on. Raising it is a privileged operation and may require step-up authentication.",
            },
            "reason": {
                "type": "string",
                "description": "Why the ceiling is being changed, recorded in the audit log.",
            },
        },
        "required": [
            "workspace",
            "from",
            "new_ceiling",
        ],
    },

    "chap.task.route": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
            "task_id": {
                "type": "string",
                "description": "Task identifier returned by chap.task.create.",
            },
            "candidates": {
                "type": "array",
                "items": {
                    "type": "string",
                    "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
                },
                "description": "Candidate assignees.",
            },
        },
        "required": [
            "workspace",
            "from",
            "task_id",
            "candidates",
        ],
    },

    "chap.review.depth": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
            "task_id": {
                "type": "string",
                "description": "Task identifier returned by chap.task.create.",
            },
            "artefact_routing_hints": {
                "type": "object",
                "description": "Per-artefact signals like confidence, model_id, cost_consumed_usd.",
                "additionalProperties": True,
            },
        },
        "required": [
            "workspace",
            "from",
            "task_id",
        ],
    },

    "chap.escalate.auto": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
            "task_id": {
                "type": "string",
                "description": "Task identifier returned by chap.task.create.",
            },
            "default_escalation_target": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
        },
        "required": [
            "workspace",
            "from",
            "task_id",
        ],
    },

    "chap.participant.rotate_key": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
            "old_kid": {
                "type": "string",
                "description": "Key id being retired. It stays in the key history so past signatures still verify.",
            },
            "new_jwk": {
                "type": "object",
                "additionalProperties": True,
                "description": "The replacement public key as a JWK. The request must be signed with the old key, which is what proves the rotation is genuine.",
            },
        },
        "required": [
            "workspace",
            "from",
            "old_kid",
            "new_jwk",
        ],
    },

    "chap.participant.revoke_key": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
            "target_uri": {
                "type": "string",
                "description": "Whose key is being revoked.",
            },
            "kid": {
                "type": "string",
                "description": "Key id to revoke. Signatures made with it are refused from now on; entries it already signed stay valid.",
            },
            "reason": {
                "type": "string",
                "description": "Why the key was revoked, e.g. 'laptop lost'. Recorded in the audit log.",
            },
        },
        "required": [
            "workspace",
            "from",
            "target_uri",
            "kid",
        ],
    },

    "chap.audit.submit_to_scitt": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
            "range": {
                "type": "object",
                "description": "Sequence window to anchor. Omit to submit the whole chain.",
                "properties": {
                    "from_seq": {
                        "type": "integer",
                        "description": "Start sequence number (inclusive).",
                    },
                    "to_seq": {
                        "type": "integer",
                        "description": "End sequence number (exclusive).",
                    },
                },
            },
            "issuer": {
                "type": "string",
                "description": "Issuer identifier to put on the SCITT signed statement, identifying who is vouching for the chain.",
            },
        },
        "required": [
            "workspace",
        ],
    },

    "chap.audit.verify_receipt": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
            "receipt": {
                "type": "object",
                "additionalProperties": True,
                "description": "The SCITT receipt to check, as returned by the transparency service. Verification fails closed when no verifier is configured.",
            },
        },
        "required": [
            "workspace",
            "receipt",
        ],
    },

    "chap.audit.verify_chain": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Workspace identifier, e.g. 'wsp_techcorp_support'.",
            },
            "from": {
                "type": "string",
                "description": "Participant URI, e.g. 'human:alice@example.org' or 'agent:bot@local'.",
            },
        },
        "required": [
            "workspace",
        ],
    },
}
# --- END GENERATED SCHEMAS ---


TOOL_NAMES: list[str] = list(SCHEMAS.keys())


def schema_for(tool_name: str) -> dict[str, Any] | None:
    """Return the JSON Schema for a tool, or ``None`` if unknown."""
    return SCHEMAS.get(tool_name)


def method_for_tool(tool_name: str) -> str | None:
    """Map an MCP tool name back to its CHAP method name."""
    if not tool_name.startswith("chap."):
        return None
    return tool_name[len("chap."):]


# ============================================================
#   Stringified-JSON coercion
# ============================================================
#
# LLM MCP clients (Claude Desktop, Cursor, and others) frequently
# serialise structured tool arguments as JSON-encoded *strings* rather
# than as native JSON objects/arrays. For example, an ``artefact`` that
# should arrive as {"draft": "..."} arrives as the string
# '{"draft": "..."}', and a ``to`` that should be ["human:me@local"]
# arrives as '["human:me@local"]'.
#
# The CHAP protocol core is deliberately strict: it stores artefacts
# and applies JSON Patches against whatever it receives. A stringified
# object therefore (a) pollutes the audit log with the wrong type and
# (b) makes object-path patches in decide.override impossible, because
# there is no object to traverse.
#
# We fix this at the adapter boundary, leaving the protocol core
# untouched. Mirrors ``coerceToolArgs`` in the TypeScript adapter; the
# two must stay in lockstep.


def _admits_type(schema: dict[str, Any] | None, t: str) -> bool:
    """True if the schema admits ``t`` ('object' or 'array') as a value type."""
    if not schema:
        return False
    if schema.get("type") == t:
        return True
    if "oneOf" in schema:
        return any(_admits_type(s, t) for s in schema["oneOf"])
    # A field declared with neither `type` nor `oneOf` nor `enum`
    # (e.g. `output`, `artefact`) is an opaque payload: it admits any
    # JSON value, so we allow coercion to both object and array.
    if "type" not in schema and "oneOf" not in schema and "enum" not in schema:
        return True
    return False


def _coerce_value(value: Any, schema: dict[str, Any] | None) -> Any:
    """Parse a stringified-JSON value when the schema admits its type."""
    if not isinstance(value, str):
        return value
    trimmed = value.strip()
    if not trimmed:
        return value
    looks_object = trimmed.startswith("{")
    looks_array = trimmed.startswith("[")
    if not looks_object and not looks_array:
        return value
    if not ((looks_object and _admits_type(schema, "object"))
            or (looks_array and _admits_type(schema, "array"))):
        return value
    try:
        import json as _json
        parsed = _json.loads(value)
    except (ValueError, TypeError):
        # Not valid JSON: leave it for the coordinator to validate/reject.
        return value
    if isinstance(parsed, list) and _admits_type(schema, "array"):
        return parsed
    if isinstance(parsed, dict) and _admits_type(schema, "object"):
        return parsed
    return value


def coerce_tool_args(
    tool_name: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Normalise a tool call's arguments, coercing stringified-JSON
    values whose parameter schema admits a structured type.

    Pure: returns a new dict and does not mutate its input. Unknown
    keys (not in the schema) pass through untouched. Coordinator
    dispatch then sees correctly-typed params, so the audit log records
    the right shapes and decide.override patches apply against real
    objects.
    """
    schema = schema_for(tool_name)
    props = (schema or {}).get("properties")
    if not props:
        return args
    return {key: _coerce_value(val, props.get(key)) for key, val in args.items()}
