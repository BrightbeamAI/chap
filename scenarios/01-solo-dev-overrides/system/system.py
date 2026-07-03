#!/usr/bin/env python3
"""
Scenario 1, as a working system: a real Pydantic AI agent, gated by CHAP.

Where the sibling ../scenario.py stages the whole story to show the CHAP
mechanics, this is the same story wired into an actual agent framework, to
prove CHAP slots into a real system's human-in-the-loop path.

What is real here:
  - A real Pydantic AI `Agent` with an approval-gated tool. The framework
    genuinely runs the agent, pauses at the tool call for human approval,
    and resumes with the decision. This is Pydantic AI's own deferred-tool
    mechanism, unchanged.
  - CHAP genuinely mediates. Each approval, edit, or denial is translated
    by the `chap-pydantic-ai` bridge into a real `decide.approve` /
    `decide.override` / `decide.reject` on a hash-linked audit chain in the
    reference coordinator.

What is stubbed, so this runs offline with no API key:
  - The model. A deterministic `FunctionModel` plays the review bot and
    proposes realistic findings. Swapping in a live model is a one-line
    change (see LIVE MODE at the bottom): `Agent("anthropic:claude-...")`.
  - The developer's decisions. In a real deployment a person decides at the
    approval prompt; here a fixed script decides, so the run is reproducible.

Run it:
    pip install "pydantic-ai-slim>=1.0"        # the one dependency
    python3 system.py

Prints the same payoff as the core scenario, but every decision below was
produced by driving a real agent loop through CHAP, not written by hand.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

# Prefer installed packages; fall back to the in-repo ones for a fresh clone.
try:
    from chap_coordinator import Coordinator
    from chap_pydantic_ai import ChapApprovalBridge
except ModuleNotFoundError:
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root / "packages" / "coordinator-py"))
    sys.path.insert(0, str(root / "packages" / "chap-pydantic-ai"))
    from chap_coordinator import Coordinator
    from chap_pydantic_ai import ChapApprovalBridge

try:
    from pydantic_ai import (
        Agent, DeferredToolRequests, DeferredToolResults, ToolApproved, ToolDenied,
    )
    from pydantic_ai.models.function import FunctionModel, AgentInfo
    from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
except ModuleNotFoundError:
    sys.exit(
        "This system example needs Pydantic AI:\n"
        "    pip install \"pydantic-ai-slim>=1.0\"\n"
        "(The zero-dependency core version is ../scenario.py.)"
    )


WORKSPACE = "wsp_my_reviews"
ME = "human:me@local"          # the developer, the reviewer
BOT = "agent:cursor@local"     # the code-review agent


# ---------------------------------------------------------------------------
# The agent: a real Pydantic AI agent whose one tool is approval-gated.
# ---------------------------------------------------------------------------

def review_bot(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """A deterministic stand-in for the model.

    For a "review <PR>" prompt it proposes an approval-gated submit_review
    call with a realistic finding. Replace this whole function with a live
    model to get real reviews (see LIVE MODE).
    """
    prompt = ""
    for m in reversed(messages):
        for part in getattr(m, "parts", []):
            if getattr(part, "part_kind", None) == "user-prompt":
                prompt = part.content
                break
        if prompt:
            break
    pr = prompt.split()[-1] if prompt else "PR-000"
    return ModelResponse(parts=[ToolCallPart(
        tool_name="submit_review",
        args={
            "pr": pr,
            "severity": "warning",
            "comment": "Unused parameter in event handler.",
        },
    )])


agent = Agent(FunctionModel(review_bot), output_type=[str, DeferredToolRequests])


@agent.tool_plain(requires_approval=True)
def submit_review(pr: str, severity: str, comment: str) -> str:
    """Post a code-review finding. Gated: a human approves before it lands."""
    return f"review posted on {pr}: [{severity}] {comment}"


# ---------------------------------------------------------------------------
# The developer's decisions. In production a person decides at the prompt;
# here a script does, keyed by PR, so the run is reproducible.
#
# Each entry is how the developer responds to the agent's proposed review:
#   ("approve", note, tags)
#   ("reject",  note, tags)
#   ("edit",    {field: new_value}, rationale, tags)   -> decide.override
# ---------------------------------------------------------------------------

DECISIONS = {
    "PR-471": ("approve", "Legit missing null check; accepted.", []),
    "PR-472": ("edit", {"severity": "info"},
               "Bot flags unused parameter on every event handler. False "
               "positive: handlers conform to a framework signature.",
               ["false-positive", "framework-pattern-misread"]),
    "PR-473": ("reject", "Style nit the team does not enforce.", ["cosmetic-pref"]),
    "PR-474": ("edit", {"severity": "info"},
               "Same framework-signature false positive as PR-472.",
               ["false-positive", "framework-pattern-misread"]),
    "PR-475": ("approve", "Good catch on the race condition.", []),
    "PR-476": ("edit", {"severity": "info"},
               "Framework-signature false positive again.",
               ["false-positive", "framework-pattern-misread"]),
    "PR-477": ("reject", "Duplicate of an existing lint rule.", ["duplicate"]),
}


def main() -> None:
    coord = Coordinator()
    bridge = ChapApprovalBridge(
        coord, workspace=WORKSPACE, agent=BOT, reviewer=ME,
    )

    for pr, decision in DECISIONS.items():
        # 1. Real agent loop: run until it pauses at the approval-gated tool.
        pending = agent.run_sync(f"review {pr}").output
        if not isinstance(pending, DeferredToolRequests):
            continue  # the agent chose not to propose a review
        call = pending.approvals[0]

        # 2. The developer decides. Build the framework's resolution object
        #    and the per-call metadata the bridge reads (rationale/tags).
        results, meta = resolve(call, decision)
        results.metadata = {call.tool_call_id: meta}

        # 3. CHAP records it: approve/override/reject on the audit chain.
        bridge.record_results(pending, results)

    report(bridge)


def resolve(call, decision):
    """Turn a scripted decision into a Pydantic AI resolution + CHAP metadata."""
    kind = decision[0]
    args = call.args_as_dict()
    if kind == "approve":
        _, note, tags = decision
        return _one(call, ToolApproved()), {"rationale": note, "tags": tags}
    if kind == "reject":
        _, note, tags = decision
        return _one(call, ToolDenied(message=note)), {"rationale": note, "tags": tags}
    # edit -> approve with overridden args -> decide.override
    _, changes, rationale, tags = decision
    edited = {**args, **changes}
    return (
        _one(call, ToolApproved(override_args=edited)),
        {"rationale": rationale, "tags": tags, "intent_preserved": True},
    )


def _one(call, resolution):
    results = DeferredToolResults()
    results.approvals[call.tool_call_id] = resolution
    return results


def report(bridge) -> None:
    entries = bridge.audit()

    # The record is real: show the decisions the agent loop produced.
    decisions = [e for e in entries if e["envelope"]["method"].startswith("decide.")]
    print("Decisions CHAP recorded from the live agent loop")
    print("=" * 52)
    for e in decisions:
        env = e["envelope"]
        verb = env["method"].split(".", 1)[1]
        tags = env["params"].get("tags") or []
        tag_str = f"  [{', '.join(tags)}]" if tags else ""
        print(f"  decide.{verb:<9}{tag_str}")
    print()

    # The punchline: the same override learning report as the core scenario,
    # but sourced from decisions a real agent framework produced.
    overrides = [e for e in decisions if e["envelope"]["method"] == "decide.override"]
    total = len(overrides)
    counts: Counter = Counter()
    for e in overrides:
        for tag in (e["envelope"]["params"].get("tags") or []):
            counts[tag] += 1

    print("Override Learning Report (wsp_my_reviews)")
    print("=" * 52)
    print(f"Total overrides: {total}")
    width = max((len(t) for t in counts), default=0)
    for tag, n in counts.most_common():
        pct = (n / total * 100) if total else 0
        print(f"  {tag:<{width}}  {'#' * n:<8} {n}  ({pct:.1f}%)")
    print()
    print("Every decision above came from driving a real Pydantic AI agent")
    print("through its approval gate; CHAP recorded each on a verifiable chain.")


# ---------------------------------------------------------------------------
# LIVE MODE
# ---------------------------------------------------------------------------
# To run this against a real model instead of the deterministic stub, set an
# API key and replace the agent definition above with:
#
#     agent = Agent("anthropic:claude-sonnet-4-5", output_type=[str, DeferredToolRequests])
#
# and drive the approval prompt from real developer input instead of the
# DECISIONS script. Everything on the CHAP side is identical: the bridge and
# the audit chain do not change. That is the point: the same integration
# carries you from an offline demo to a live system.


if __name__ == "__main__":
    main()
