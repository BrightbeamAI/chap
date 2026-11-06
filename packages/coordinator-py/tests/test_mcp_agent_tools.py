"""
The agent's tool set is one definition in each language, and the two agree.

``AGENT_MCP_TOOLS`` is what the MCP command offers an agent by default and
what a server that gives an agent its own connection imports. The TypeScript
set is read out of its source, as test_spec_lifecycle_table.py reads the
specification: a pattern that stops matching is a failure, so this cannot
quietly stop covering the set.
"""
from __future__ import annotations

import re
from pathlib import Path

from chap_coordinator.transports.mcp_schemas import TOOL_NAMES
from chap_coordinator.transports.mcp_server import AGENT_MCP_TOOLS

TOOLS_TS = Path(__file__).resolve().parents[2] / "coordinator-mcp" / "src" / "tools.ts"


def test_the_agent_tool_set_matches_the_typescript_one():
    source = TOOLS_TS.read_text(encoding="utf-8")
    m = re.search(r"export const AGENT_MCP_TOOLS: ReadonlySet<string> = new Set\(\[(.*?)\]\);", source, re.S)
    assert m, "AGENT_MCP_TOOLS not found in tools.ts; fix this parser"
    assert set(re.findall(r'"(chap\.[a-z_.]+)"', m.group(1))) == AGENT_MCP_TOOLS


def test_the_agent_tool_set_names_real_tools_and_none_that_decide_who_reviews():
    assert AGENT_MCP_TOOLS <= set(TOOL_NAMES)
    for excluded in ("chap.review.request", "chap.escalate.raise", "chap.participant.join",
                     "chap.workspace.create", "chap.control.pause"):
        assert excluded not in AGENT_MCP_TOOLS
