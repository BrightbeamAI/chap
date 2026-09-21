"""
The chain as a graph: every edge is an envelope, the lineage of a task is
what led to it, and the collaboration graph shows load, concentration and
duties that a table hides.
"""
from __future__ import annotations

import json
import xml.dom.minidom

import pytest

pytest.importorskip("pandas")
pytest.importorskip("chap_coordinator")

from chap_analytics import Chain, frames, graph  # noqa: E402
from chap_analytics.sample import support_desk, synthetic  # noqa: E402


@pytest.fixture(scope="module")
def desk():
    return frames(support_desk())


@pytest.fixture(scope="module")
def g(desk):
    return graph.build(desk)


def test_every_edge_type_is_declared_and_typed_consistently(g):
    for e in g.edges:
        assert e.type in graph.EDGE_TYPES
        src_type, dst_type, _ = graph.EDGE_TYPES[e.type]
        assert g.nodes[e.source].type == src_type, (e.type, e.source)
        assert g.nodes[e.target].type == dst_type, (e.type, e.target)
    assert set(n.type for n in g.nodes.values()) <= set(graph.NODE_TYPES)


def test_the_graph_reconciles_with_the_tables(desk, g):
    by_type = {}
    for n in g.nodes.values():
        by_type[n.type] = by_type.get(n.type, 0) + 1
    assert by_type["task"] == len(desk.tasks)
    assert by_type["decision"] == len(desk.decisions)
    assert by_type["whisper"] == len(desk.whispers)
    assert by_type["handoff"] == len(desk.handoffs)
    assert by_type["deliberation"] == len(desk.deliberations)
    assert by_type["participant"] == len(desk.participants)
    decided = sum(1 for e in g.edges if e.type == "decided")
    assert decided == len(desk.decisions)
    overrode = sum(1 for e in g.edges if e.type == "overrode")
    assert overrode == len(desk.overrides)
    votes = sum(1 for e in g.edges if e.type == "voted")
    assert votes == len(desk.votes)


def test_lineage_follows_an_escalation_back_to_its_origin(desk, g):
    successor = desk.tasks[desk.tasks["supersedes"].notna()].iloc[0]
    lin = graph.lineage(g, successor["task_id"])
    types = {n.type for n in lin.nodes.values()}
    assert {"task", "review", "artefact", "participant"} <= types
    assert successor["supersedes"] in lin.nodes
    # Participants are endpoints, so another task they touched stays out.
    assert sum(1 for n in lin.nodes.values() if n.type == "task") == 2
    table = graph.lineage_table(g, successor["task_id"])
    assert table["ts"].is_monotonic_increasing
    assert list(table["action"])[:2] == ["delegated", "assigned_to"]
    lanes = graph.layout_lanes(table)
    assert lanes["y"].nunique() == table["actor"].nunique()
    with pytest.raises(KeyError):
        graph.lineage(g, "tsk_missing")


def test_lineage_of_an_overridden_task_carries_the_correction(desk, g):
    tid = desk.overrides.iloc[0]["task_id"]
    lin = graph.lineage(g, tid)
    kinds = {n.attrs.get("kind") for n in lin.nodes.values() if n.type == "artefact"}
    assert {"draft", "override"} <= kinds
    assert any(e.type == "based_on" for e in lin.edges)


def test_collaboration_weights_are_decision_counts(desk):
    c = graph.collaboration(desk)
    reviewed = c[c["relation"] == "reviewed"]
    assert reviewed["weight"].sum() == len(desk.decisions.dropna(subset=["reviewer", "assignee"]))
    assert set(reviewed["target"]) == {"agent:drafter"}
    handed = c[c["relation"] == "handed_off"].iloc[0]
    assert handed["source"] == "human:maya" and handed["target"] == "human:sam" and handed["accepted"] == 1


def test_centrality_puts_the_agent_in_the_middle(desk):
    cen = graph.centrality(desk)
    top = cen.iloc[0]
    assert top["participant"] == "agent:drafter" and top["in_degree"] == 4
    assert cen["betweenness"].between(0, 1).all()


def test_concentration_sees_a_dominant_reviewer():
    skewed = frames(synthetic(0, tasks=150, reviewer_weights=(8, 1, 1)))
    even = frames(synthetic(0, tasks=150))
    s = graph.concentration(skewed).iloc[0]
    e = graph.concentration(even).iloc[0]
    assert s["top_reviewer"] == "human:ana" and s["top_share"] > 0.6 > e["top_share"]
    assert s["hhi"] > e["hhi"] and s["sufficient"]


def test_coverage_counts_people_on_the_path(desk):
    cov = graph.coverage(desk)
    assert cov.attrs["share"] == 1.0 and cov.attrs["uncovered"] == []
    # The handoff tasks were done by a person with no review: covered by performance.
    manual = cov[cov["kind"] == "manual_reply"]
    assert manual["human_performed"].all() and not manual["human_decision"].any()
    unwatched = frames(synthetic(0, tasks=40, delegator_reviews=True))
    u = graph.coverage(unwatched)
    assert u.attrs["share"] == 0.0 and len(u.attrs["uncovered"]) == u.attrs["shipped"] > 0


def test_duties_flag_a_service_deciding_what_it_delegated(desk):
    assert graph.duties(desk).empty
    flagged = graph.duties(frames(synthetic(0, tasks=40, delegator_reviews=True)))
    assert set(flagged["check"]) == {"delegator_review", "agent_decided"}
    assert (flagged["actor"] == "service:intake").all()


def test_layouts_are_deterministic_and_bounded(desk):
    c = graph.collaboration(desk)
    a = graph.layout_spring(c, seed=3)
    b = graph.layout_spring(c, seed=3)
    assert a.equals(b) and len(a) == len(set(c["source"]) | set(c["target"]))
    assert a["x"].between(0, 1).all() and a["y"].between(0, 1).all()


def test_exports_round_trip_and_parse(g):
    nl = graph.to_node_link(g)
    text = json.dumps(nl)
    back = json.loads(text)
    assert len(back["nodes"]) == len(g.nodes) and len(back["edges"]) == len(g.edges)
    assert set(back["ontology"]["nodes"]) == set(graph.NODE_TYPES)
    xml.dom.minidom.parseString(graph.to_graphml(g))
    pytest.importorskip("networkx")
    G = graph.to_networkx(g)
    assert G.number_of_nodes() == len(g.nodes) and G.number_of_edges() == len(g.edges)


def test_an_empty_chain_gives_an_empty_graph():
    f = frames(Chain(workspace="w", events=[], state=None, source="test"))
    g0 = graph.build(f)
    assert len(g0.nodes) == 1 and g0.nodes["w"].type == "workspace" and g0.edges == []
    assert graph.collaboration(f).empty and graph.centrality(f).empty
    assert graph.concentration(f).empty and graph.duties(f).empty
    assert graph.coverage(f).empty and graph.layout_spring(graph.collaboration(f)).empty


def test_the_ontology_diagram_is_drawn_from_the_declarations():
    svg = graph.ontology_svg()
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    for node_type in graph.NODE_TYPES:
        assert f">{node_type}<" in svg
    for edge_type in graph.EDGE_TYPES:
        assert f">{edge_type}<" in svg
    assert svg.count("marker-end") == len(graph.EDGE_TYPES)


def test_who_was_asked_comes_from_the_envelopes(desk, g):
    # Every whisper names who it was asked of, and every review pass names the
    # reviewers it was addressed to, decided or still waiting.
    whispered = [e for e in g.edges if e.type == "whispered_to"]
    assert len(whispered) >= len(desk.whispers) and all(g.nodes[e.target].type == "participant" for e in whispered)
    reviews = [n for n in g.nodes.values() if n.type == "review"]
    asked = {e.source for e in g.edges if e.type == "asked_to"}
    undecided = [n for n in reviews if n.attrs.get("undecided")]
    assert reviews and undecided
    assert any(n.id in asked for n in undecided)
    # A quorum pass settled by its first reviewer still names the second.
    f = frames(synthetic(4, tasks=60, quorum_share=1.0, agreement=1.0))
    q = graph.build(f)
    for n in q.nodes.values():
        if n.type == "review" and not n.attrs.get("undecided"):
            assert sum(1 for e in q.edges if e.type == "asked_to" and e.source == n.id) == 2


def test_a_fulfils_claim_on_the_wire_reaches_the_graph():
    # SPECIFICATION 9.4 puts `fulfils` on the artefact, so a producer writes it
    # into the output a task.complete carries, which is what the wrap helper
    # does and what the `tasks` table reads. Node ids here are built from the
    # task and the sequence number, so the named decision cannot be one of
    # them and the claim lands on a referenced decision node.
    from chap_coordinator import Coordinator, CoordinatorOptions
    from chap_coordinator.transports.wrap import wrap_mcp_tool_call

    from chap_analytics import from_coordinator

    c = Coordinator(CoordinatorOptions(default_profiles=["core/1.0", "review/1.0"]))
    c.dispatch({"jsonrpc": "2.0", "id": "1", "method": "workspace.create",
                "params": {"workspace": "w"}})
    c.dispatch({"jsonrpc": "2.0", "id": "2", "method": "participant.join",
                "params": {"workspace": "w", "from": "agent:bot",
                           "type": "agent", "role": "drafter"}})
    res = wrap_mcp_tool_call(c, "w", caller="agent:bot", tool="t", args={},
                             result={"ok": True}, fulfils="art_decision_1")

    g = graph.build(frames(from_coordinator(c, "w")))
    claims = [e for e in g.edges if e.type == "fulfils"]
    assert len(claims) == 1
    source = g.nodes[claims[0].source]
    assert source.type == "artefact" and source.attrs["kind"] == "output"
    assert res["task_id"] in claims[0].source
    assert claims[0].target == "art_decision_1"
    assert g.nodes["art_decision_1"].type == "decision"
    assert g.nodes["art_decision_1"].attrs["kind"] == "referenced"


def test_a_task_complete_without_a_fulfils_claim_adds_no_edge():
    # The output is an artefact like any other; only a named `fulfils` makes
    # the claim, so an ordinary completion leaves the graph unchanged here.
    g = graph.build(frames(support_desk(envelopes_only=True)))
    assert not [e for e in g.edges if e.type == "fulfils"]
