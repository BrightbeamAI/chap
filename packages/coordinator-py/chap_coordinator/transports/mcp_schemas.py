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
                "description": "Profile identifiers to enable, e.g. ['core/1.0', 'review/1.0']. Recorded on the workspace. Two profiles change behaviour by being present: modes/1.0, under which a trial-mode task requires review, and audit-scitt/1.0, which turns on the hash-linked chain.",
            },
            "mode": {
                "type": "string",
                "enum": [
                    "shadow",
                    "trial",
                    "production",
                ],
                "default": "trial",
                "description": "Default mode for tasks created here. Under modes/1.0 a trial-mode task has review_required set to true whatever the caller passes. 'shadow' and 'production' are recorded on the task and carry no further behaviour in this implementation.",
            },
            "mode_ceiling": {
                "type": "string",
                "enum": [
                    "shadow",
                    "trial",
                    "production",
                ],
                "default": "production",
                "description": "Highest mode a task in this workspace may request. A task.create above the ceiling is refused with -32040. The ceiling can be changed afterwards with chap.control.set_mode_ceiling.",
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
                "description": "The complete profile set to enable, replacing the current one. Adding audit-scitt/1.0 to a workspace that already has entries leaves those entries unchained and outside chain verification.",
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
                "description": "The kind of participant. Only members of type 'human' are eligible for the review that chap.task.complete opens on a task marked review_required, and a completion with no eligible human is refused with -32011.",
            },
            "role": {
                "type": "string",
                "description": "Operator-defined role, e.g. 'reviewer' or 'drafter'. One value is read by the coordinator: 'admin' permits revoking another member's key.",
            },
            "display_name": {
                "type": "string",
                "description": "Human-readable name for interfaces. Not used in authorisation.",
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
                "description": "The delegator. Must be a workspace member.",
            },
            "kind": {
                "type": "string",
                "description": "Task kind, e.g. 'draft_response' or 'review'. Free text; the coordinator records it without interpreting it.",
            },
            "assignee": {
                "type": "string",
                "description": "Who the task is assigned to. Must be a workspace member, and must not be paused: assigning to a paused member is refused with -32063.",
            },
            "input": {
                "type": "object",
                "description": "Task-specific input payload.",
                "additionalProperties": True,
            },
            "routing_hints": {
                "type": "object",
                "description": "Signals recorded on the task and read by the routing/1.0 methods: task.route, review.depth and escalate.auto. Recording a hint has no effect on its own; it is consulted only when one of those methods is called.",
                "properties": {
                    "criticality": {
                        "type": "string",
                        "enum": [
                            "low",
                            "medium",
                            "high",
                            "critical",
                        ],
                        "description": "How costly a wrong answer would be. The default review-depth policy reads it: 'critical' gives a full review, and 'high' with confidence below 0.7 does the same. The default assignee policy does not read it.",
                    },
                    "deadline": {
                        "type": "string",
                        "description": "When the work is needed by, as an ISO 8601 timestamp. Recorded on the task; the coordinator does not act on it.",
                    },
                    "risk_tier": {
                        "type": "string",
                        "description": "Operator-defined risk band, e.g. 'regulated' or 'internal'. No built-in policy reads it; it is available to an operator-supplied routing policy.",
                    },
                    "max_cost_usd": {
                        "type": "string",
                        "description": "Budget ceiling for this task in US dollars. Written as a decimal string, e.g. \"12.50\". CHAP canonicalisation accepts integers only, so a JSON number with a fractional part is refused with -32602. No built-in policy reads it.",
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
                "description": "Mode for this task, defaulting to the workspace mode. A mode above the workspace ceiling is refused with -32040.",
            },
            "review_required": {
                "type": "boolean",
                "description": "When true, chap.task.complete opens a review instead of completing: the output becomes the artefact under review, and the task reaches 'completed' only on a reviewer decision. chap.task.update cannot complete such a task. Under modes/1.0 a trial-mode task has this set to true whatever is passed here.",
            },
            "deadline": {
                "type": "string",
                "description": "When the task is due, as an ISO 8601 timestamp. Recorded on the task; the coordinator does not act on it.",
            },
            "idempotency_key": {
                "type": "string",
                "description": "Caller-chosen key for safe retries. A second create carrying a key already seen in this workspace returns the original task and records nothing further. The workspace retains the 10,000 most recent keys.",
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
                "description": "The state to move the task to. Only the transitions in SPECIFICATION.md 8.1 are legal from the task's current state; others are refused with -32602. A task marked review_required cannot be moved to 'completed' here: submit the output with chap.task.complete, which opens the review.",
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
                "description": "The task's output artefact. Pass a JSON object or array. A JSON-encoded string is parsed back to the structured value before dispatch, so patches in chap.decide.override apply against a real object.",
            },
            "confidence": {
                "type": "string",
                "description": "Self-reported confidence in the output, between 0 and 1. Written as a decimal string, e.g. \"0.86\". CHAP canonicalisation accepts integers only, so a JSON number with a fractional part is refused with -32602.",
            },
            "routing_hints": {
                "type": "object",
                "description": "Signals recorded on the task and read by the routing/1.0 methods: task.route, review.depth and escalate.auto. Recording a hint has no effect on its own; it is consulted only when one of those methods is called.",
                "properties": {
                    "criticality": {
                        "type": "string",
                        "enum": [
                            "low",
                            "medium",
                            "high",
                            "critical",
                        ],
                        "description": "How costly a wrong answer would be. The default review-depth policy reads it: 'critical' gives a full review, and 'high' with confidence below 0.7 does the same. The default assignee policy does not read it.",
                    },
                    "deadline": {
                        "type": "string",
                        "description": "When the work is needed by, as an ISO 8601 timestamp. Recorded on the task; the coordinator does not act on it.",
                    },
                    "risk_tier": {
                        "type": "string",
                        "description": "Operator-defined risk band, e.g. 'regulated' or 'internal'. No built-in policy reads it; it is available to an operator-supplied routing policy.",
                    },
                    "max_cost_usd": {
                        "type": "string",
                        "description": "Budget ceiling for this task in US dollars. Written as a decimal string, e.g. \"12.50\". CHAP canonicalisation accepts integers only, so a JSON number with a fractional part is refused with -32602. No built-in policy reads it.",
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
                        "description": "Start sequence number, inclusive.",
                    },
                    "to_seq": {
                        "type": "integer",
                        "description": "End sequence number, exclusive.",
                    },
                },
            },
            "filter": {
                "type": "object",
                "description": "Narrows the entries returned. Conditions combine with AND, and are applied within the sequence window rather than before it.",
                "properties": {
                    "method": {
                        "type": "string",
                        "description": "Return only entries for this CHAP method, e.g. 'decide.override'. Matched in full, without the 'chap.' tool-name prefix.",
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
                "description": "One or more reviewers, as a single URI string or an array of URI strings. Only a workspace member can go on to decide, so a review addressed elsewhere cannot be closed by its recipient.",
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
                "description": "How many of the addressed reviewers must approve before the task completes. quorum:N is accepted for any N of 1 or more. The rule is fixed once the review is open: a later request naming a different one is refused with -32014. Omitting it on a later request leaves the rule alone, which is how reviewers are added to an open review.",
            },
            "artefact": {
                "description": "The draft being submitted for review. Pass a JSON object or array. A JSON-encoded string is parsed back to the structured value before dispatch, so patches in chap.decide.override apply against a real object. Re-requesting with the same artefact widens the reviewer set; a different artefact on an open review is refused with -32014.",
            },
            "deadline": {
                "type": "string",
                "description": "When the review is needed by, as an ISO 8601 timestamp. Recorded on the review; the coordinator does not act on it.",
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
                "description": "Optional. SHA-256 over the JCS canonicalisation of the artefact under review, in the form `sha256:<hex>`. When present it binds the decision to the exact content reviewed, and a mismatch is refused with -32074.",
            },
            "comment": {
                "type": "string",
                "description": "The reviewer's note on this decision. Recorded in the audit entry for the decision.",
            },
            "tags": {
                "type": "array",
                "items": {
                    "type": "string",
                },
                "description": "Workspace-defined labels for this decision, e.g. ['tone', 'unsupported-claim']. Recorded in the audit entry. chap.audit.read does not filter on tags, so grouping by tag is done by the reader.",
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
                "description": "Optional. SHA-256 over the JCS canonicalisation of the artefact under review, in the form `sha256:<hex>`. When present it binds the decision to the exact content reviewed, and a mismatch is refused with -32074.",
            },
            "comment": {
                "type": "string",
                "description": "The reviewer's note on this decision. Recorded in the audit entry for the decision.",
            },
            "tags": {
                "type": "array",
                "items": {
                    "type": "string",
                },
                "description": "Workspace-defined labels for this decision, e.g. ['tone', 'unsupported-claim']. Recorded in the audit entry. chap.audit.read does not filter on tags, so grouping by tag is done by the reader.",
            },
            "request_revision": {
                "type": "boolean",
                "description": "When true the task returns to 'in_progress' rather than 'declined', so the assignee can revise and resubmit.",
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
                "description": "Optional. SHA-256 over the JCS canonicalisation of the artefact under review, in the form `sha256:<hex>`. When present it binds the decision to the exact content reviewed, and a mismatch is refused with -32074.",
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
                "description": "RFC 6902 JSON Patch operations applied to the artefact under review. The patched artefact becomes the task output; a patch that does not apply is refused with -32012.",
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
                "description": "Why the correction was made. Required on every override and recorded in the audit entry, since the diff shows what changed but not why.",
            },
            "tags": {
                "type": "array",
                "items": {
                    "type": "string",
                },
                "description": "Workspace-defined labels for this decision, e.g. ['tone', 'unsupported-claim']. Recorded in the audit entry. chap.audit.read does not filter on tags, so grouping by tag is done by the reader.",
            },
            "policy_refs": {
                "type": "array",
                "items": {
                    "type": "string",
                },
                "description": "Identifiers of the policies or guidelines this correction applies, e.g. ['policy:no-delivery-promises']. Recorded in the audit entry.",
            },
            "logical_id": {
                "type": "string",
                "description": "Caller-chosen identifier for the item being decided, stable across revisions and overrides of the same underlying content. Recorded in the audit entry.",
            },
            "intent_preserved": {
                "type": "boolean",
                "description": "True when the edit refines the decision the draft was making, false when it substitutes a different one. Recorded in the audit entry.",
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
                "description": "Why this reviewer is standing aside. Recorded in the audit entry.",
            },
            "category": {
                "type": "string",
                "enum": [
                    "conflict_of_interest",
                    "insufficient_context",
                    "out_of_scope",
                    "other",
                ],
                "description": "The kind of abstention. Recorded in the audit entry.",
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
                "description": "The task being escalated. It moves to 'escalated' and is linked to the successor. A completed, cancelled or superseded task cannot be escalated.",
            },
            "new_task": {
                "type": "object",
                "description": "The successor task to open for the escalation target.",
                "properties": {
                    "kind": {
                        "type": "string",
                        "description": "Task kind for the successor. Defaults to the original's kind.",
                    },
                    "assignee": {
                        "type": "string",
                        "description": "Who the escalation goes to. Must be a workspace member.",
                    },
                    "input": {
                        "type": "object",
                        "additionalProperties": True,
                        "description": "Input payload for the successor. It defaults to an empty object rather than the original's input, so anything the new assignee needs has to be restated here.",
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
                "description": "The question being put. A whisper is answered on its own, without the recipient opening the task.",
            },
            "options": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "Stable identifier that an answer refers to.",
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
                "description": "Multiple-choice options. When present, an answer must name one of these ids in answer_option; any other id is refused with -32022.",
            },
            "deadline_ms": {
                "type": "integer",
                "description": "How long the whisper stays open, in milliseconds from now. Once it passes, the whisper lapses and default_if_lapsed is applied.",
            },
            "default_if_lapsed": {
                "description": "The value applied if the deadline passes with no answer. Required, so that a lapsed whisper still has a defined outcome.",
            },
            "urgency": {
                "type": "string",
                "enum": [
                    "low",
                    "medium",
                    "high",
                ],
                "default": "low",
                "description": "How urgent the question is. Recorded and passed on to the client; it does not change the deadline or the lapse behaviour.",
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
                "description": "Identifier returned by chap.whisper.ask. A whisper that has already been answered is refused with -32020, and one past its deadline with -32021.",
            },
            "answer_option": {
                "type": "string",
                "description": "The id of the chosen option. Required when the whisper carried options.",
            },
            "answer": {
                "type": "string",
                "description": "Free-text answer, for a whisper with no options.",
            },
            "comment": {
                "type": "string",
                "description": "Anything else the answerer wants recorded alongside the answer.",
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
                "description": "The participants entitled to vote. A vote from anyone else is refused with -32030.",
            },
            "task_id": {
                "type": "string",
                "description": "Task identifier returned by chap.task.create.",
            },
            "rule": {
                "type": "string",
                "description": "How the outcome is decided when the deliberation closes: any_one_approves, all_approve, quorum:N, weighted_vote:T or weighted_vote_with_veto:T. An unrecognised rule is refused at open with -32033.",
            },
            "question": {
                "type": "string",
                "description": "What the group is deciding. State it so that a yea or a nay is unambiguous.",
            },
            "weights": {
                "type": "object",
                "additionalProperties": {
                    "type": "integer",
                },
                "description": "Voter to weight map, read by the weighted rules. A voter with no entry counts as 1. Weights must be integers: a JSON number with a fractional part is refused with -32602.",
            },
            "veto": {
                "type": "object",
                "additionalProperties": {
                    "type": "boolean",
                },
                "description": "Voter to can-veto map. A veto is honoured only under weighted_vote_with_veto, and only from a voter listed true here.",
            },
            "deadline": {
                "type": "string",
                "description": "When voting is intended to close, as an ISO 8601 timestamp. Recorded on the deliberation; closing is done by chap.deliberate.close and the coordinator does not close on the deadline.",
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
                "description": "Identifier returned by chap.deliberate.open. A closed deliberation is refused with -32032.",
            },
            "comment": {
                "type": "string",
                "description": "The contribution to record. Comments are kept with the deliberation and in the audit log, so the reasoning survives the vote.",
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
                "description": "Identifier returned by chap.deliberate.open. A closed deliberation is refused with -32032, and a second vote from the same voter with -32031.",
            },
            "vote": {
                "type": "string",
                "enum": [
                    "yea",
                    "nay",
                    "abstain",
                ],
                "description": "This voter's position. An abstention is recorded but counts as neither a yea nor a nay, so under all_approve or quorum:N it withholds the approval those rules need.",
            },
            "weight": {
                "type": "integer",
                "description": "Recorded with the vote. The tally uses the weights map given at chap.deliberate.open, not this value.",
            },
            "comment": {
                "type": "string",
                "description": "Why the vote went this way. Recorded with it.",
            },
            "veto_invoked": {
                "type": "boolean",
                "description": "Blocks the outcome regardless of the tally. Honoured only under weighted_vote_with_veto, and only when the voter is listed true in the veto map given at open.",
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
                "description": "Identifier returned by chap.deliberate.open. Closing computes the outcome from the votes cast; closing an already closed deliberation returns the outcome unchanged.",
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
                "description": "Recipient, as a participant URI or a group URI such as 'group:support-team'. A recipient who is not a workspace member is refused with -32052.",
            },
            "tasks": {
                "type": "array",
                "description": "The work being handed over, one entry per task. Every task must currently be assigned to the proposer; otherwise the proposal is refused with -32050.",
                "items": {
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "Task identifier returned by chap.task.create.",
                        },
                        "title": {
                            "type": "string",
                            "description": "Short label for the task.",
                        },
                        "status_summary": {
                            "type": "string",
                            "description": "Where the work has reached, and what has already been tried.",
                        },
                        "next_action": {
                            "type": "string",
                            "description": "What the recipient should do first.",
                        },
                        "blockers": {
                            "type": "array",
                            "items": {
                                "type": "string",
                            },
                            "description": "What is preventing progress, if anything.",
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
                "description": "Identifier returned by chap.handoff.propose. A handoff already accepted or declined is refused with -32051.",
            },
            "accepted_task_ids": {
                "type": "array",
                "items": {
                    "type": "string",
                    "description": "Task identifier returned by chap.task.create.",
                },
                "description": "Which of the proposed tasks are being accepted. If omitted, all of them are.",
            },
            "comment": {
                "type": "string",
                "description": "Anything the recipient wants recorded when taking the work on.",
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
                "description": "Identifier returned by chap.handoff.propose. A handoff already accepted or declined is refused with -32051.",
            },
            "reason": {
                "type": "string",
                "description": "Why the handover is refused. Recorded so the proposer can route it elsewhere.",
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
                "description": "What the pause applies to. 'task' moves one task to 'paused'; a task that is completed, declined, cancelled or superseded is refused with -32061. 'participant' stops new tasks being assigned to that member and leaves their existing work running. 'workspace' refuses every method except workspace.create, workspace.describe, control.resume, audit.read, participant.join and participant.leave.",
            },
            "task_id": {
                "type": "string",
                "description": "Task identifier returned by chap.task.create.",
            },
            "participant_uri": {
                "type": "string",
                "description": "Whose work to pause, when scope is 'participant'. Must be a workspace member.",
            },
            "in_flight_policy": {
                "type": "string",
                "enum": [
                    "allow_to_complete",
                    "interrupt",
                ],
                "description": "Recorded with the request, and echoed back when scope is 'participant'. The coordinator does not act on it: under either value, work already under way is left alone.",
            },
            "reason": {
                "type": "string",
                "description": "Why the pause was applied. Recorded in the audit entry.",
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
                "description": "What the resume applies to. 'task' returns a paused task to 'in_progress'; a task that is not paused is refused with -32061. 'participant' allows that member to be assigned tasks again. 'workspace' returns the workspace to active.",
            },
            "task_id": {
                "type": "string",
                "description": "Task identifier returned by chap.task.create.",
            },
            "participant_uri": {
                "type": "string",
                "description": "Whose work to resume, when scope is 'participant'. Must be a workspace member.",
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
                "description": "The task to cancel. A task that is completed, declined, cancelled or superseded is refused with -32061.",
            },
            "reason": {
                "type": "string",
                "description": "Why the task was cancelled. Recorded in the audit entry.",
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
                "description": "Name for this snapshot, recorded on the artefact. chap.control.rollback identifies a snapshot by its artefact id, not by label.",
            },
            "include": {
                "type": "array",
                "items": {
                    "type": "string",
                },
                "description": "Which aspects of the workspace to capture. Recognised values are 'members', 'open_tasks', 'mode_ceiling', 'policy' and 'audit'. Defaults to members, open_tasks and mode_ceiling.",
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
                "description": "Artefact id returned by chap.control.snapshot. An id with no matching snapshot is refused with -32062.",
            },
            "what_to_restore": {
                "type": "array",
                "items": {
                    "type": "string",
                },
                "description": "Which captured aspects to apply. Only 'mode_ceiling' and 'members' are restored; the others are held in the snapshot and not reapplied. Restoring members resets role and scopes on members still present, and does not re-add members who have left. Defaults to the snapshot's include list.",
            },
            "reason": {
                "type": "string",
                "description": "Why the rollback was performed. The rollback is itself an audit entry; earlier entries are not rewritten.",
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
                "description": "The task being replaced. It moves to 'superseded' and is linked to the successor rather than deleted.",
            },
            "successor_task": {
                "type": "object",
                "description": "The replacement task.",
                "properties": {
                    "kind": {
                        "type": "string",
                        "description": "Task kind for the successor. Required.",
                    },
                    "assignee": {
                        "type": "string",
                        "description": "Who takes the replacement on. Defaults to the superseded task's assignee, and must be a workspace member.",
                    },
                    "input": {
                        "type": "object",
                        "additionalProperties": True,
                        "description": "Input payload for the successor. Defaults to an empty object.",
                    },
                },
                "required": [
                    "kind",
                ],
            },
            "reason": {
                "type": "string",
                "description": "Why the original is being replaced. Recorded in the audit entry.",
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
                "description": "The highest mode tasks in this workspace may request from now on. Existing tasks keep the mode they were created with. Where the coordinator is configured to enforce step-up authentication this method is one of the privileged ones and a call without step-up is refused with -32402.",
            },
            "reason": {
                "type": "string",
                "description": "Why the ceiling is being changed. Recorded in the audit entry.",
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
                "description": "Candidate assignees. An empty list is refused with -32513. Candidates that are not workspace members are dropped, and if none remain the call is refused with -32510. The default policy selects the first remaining candidate; an operator-supplied routing policy may select on any basis.",
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
                "description": "Per-artefact signals such as confidence, model_id and cost_consumed_usd, merged over the task's routing_hints for this call. The default policy reads criticality and confidence. If the merged set is empty the call is refused with -32514. Fractional values are written as decimal strings, e.g. \"confidence\": \"0.86\".",
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
                "description": "Who to escalate to when the policy decides to escalate and names no target of its own. It must be a workspace member or a group URI; if the policy escalates with no usable target the call is refused with -32516.",
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
                "description": "Key id being retired. It is given a valid_until timestamp and stays in the member's key history, so signatures made before the rotation still verify. A key id that is unknown is refused with -32071, and one already revoked with -32072.",
            },
            "new_jwk": {
                "type": "object",
                "additionalProperties": True,
                "description": "The replacement public key as a JWK. It must carry a 'kid'. Whether the request itself has to be signed with the old key is decided at dispatch, and only where the coordinator is configured to require signatures.",
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
                "description": "Whose key is being revoked. Revoking another member's key requires the caller to hold the role 'admin'; otherwise the call is refused with -32011.",
            },
            "kid": {
                "type": "string",
                "description": "Key id to revoke. It is marked revoked with a timestamp and a reason, and signatures presented with it are refused from then on. A key id that is unknown is refused with -32071.",
            },
            "reason": {
                "type": "string",
                "description": "Why the key was revoked, e.g. 'laptop lost'. Recorded on the key and in the audit entry.",
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
                        "description": "Start sequence number, inclusive.",
                    },
                    "to_seq": {
                        "type": "integer",
                        "description": "End sequence number, exclusive.",
                    },
                },
            },
            "issuer": {
                "type": "string",
                "description": "Issuer identifier placed on each SCITT signed statement, naming who vouches for the chain. Defaults to 'service:coordinator'. Where no submitter is configured the statements are returned unsigned for the deployment to submit out of band.",
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
                "description": "The SCITT receipt to check, as returned by the transparency service. Verification is delegated to a hook supplied by the deployment, and fails closed with -32082 where no hook is configured.",
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
