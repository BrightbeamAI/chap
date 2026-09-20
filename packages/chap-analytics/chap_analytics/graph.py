"""
The chain as a graph.

The workspace, its participants, tasks, review passes, artefacts, decisions,
whispers, deliberations and handoffs are nodes. Who delegated, who was assigned, who
reviewed, who decided, what an override was based on, what an execution
fulfils, which task superseded which, who handed work to whom, who asked and
who answered, who voted: those are the edges. Every edge is an envelope or a
field on one, so the graph is a second projection of the same chain as the
tables, built from a :class:`~chap_analytics.frames.Frames`.

Two views come out of it. :func:`lineage` is everything that led to one task,
in order, the picture an auditor asks for first. :func:`collaboration` is the
people and agents of a workspace with the work that passed between them,
where load, concentration and separation of duties are visible.

The node and edge types are fixed (:data:`NODE_TYPES`, :data:`EDGE_TYPES`).
That vocabulary is the ontology of a CHAP workspace and the node-link export
follows it, so a graph tool or a knowledge graph can take the export directly.
Nothing here needs more than numpy and pandas; :func:`to_networkx` hands the
graph to networkx when it is installed.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Iterable
from xml.sax.saxutils import escape

import numpy as np
import pandas as pd

from .frames import Frames

__all__ = [
    "Node", "Edge", "Graph", "NODE_TYPES", "EDGE_TYPES", "build", "lineage",
    "lineage_table", "collaboration", "centrality", "concentration", "coverage",
    "duties", "layout_spring", "layout_lanes", "to_node_link", "to_graphml",
    "to_networkx", "ontology_svg", "SvgText", "dumps", "SHIPPED",
]

#: Node types and what each stands for.
NODE_TYPES: dict[str, str] = {
    "workspace": "The workspace the chain belongs to.",
    "participant": "A human, agent, service or group that sent or received envelopes. `kind` carries which.",
    "task": "A unit of work with a lifecycle.",
    "review": "One review pass on a task: the artefact under review and the reviewers asked.",
    "artefact": "A draft under review, or the corrected artefact an override produced.",
    "decision": "A reviewer's approve, reject, override or abstain on a review pass.",
    "whisper": "A deadline-bound question an agent asked mid-task.",
    "deliberation": "A multi-party vote.",
    "handoff": "A proposal to move tasks from one participant to another.",
}

#: Edge types, source type -> target type, and what each records.
EDGE_TYPES: dict[str, tuple[str, str, str]] = {
    "member_of": ("participant", "workspace", "participant.join"),
    "delegated": ("participant", "task", "task.create, by the delegator"),
    "assigned_to": ("task", "participant", "the current assignee, after any handoff or routing"),
    "produced": ("participant", "artefact", "task.complete or review.request carrying the artefact"),
    "under_review": ("review", "artefact", "the artefact a review pass judged"),
    "requested": ("participant", "review", "review.request, or the task.complete that opened the review"),
    "asked_to": ("review", "participant", "review.request `to`; where a review was opened implicitly, the reviewers who decided it"),
    "reviews": ("review", "task", "the task the pass belongs to"),
    "decided": ("participant", "decision", "decide.approve, decide.reject, decide.override or abstain.declare"),
    "decision_on": ("decision", "review", "the pass the decision settled or contributed to"),
    "based_on": ("artefact", "artefact", "an override's corrected artefact and the draft it was derived from"),
    "overrode": ("decision", "artefact", "the corrected artefact an override decision produced"),
    "fulfils": ("artefact", "decision", "task.complete `fulfils`: the decision an execution's artefact says it carries out; the producer's claim, recorded without verification"),
    "supersedes": ("task", "task", "escalate.raise or control.supersede: the successor and the task it replaced"),
    "asked": ("participant", "whisper", "whisper.ask"),
    "whispered_to": ("whisper", "participant", "whisper.ask `to`"),
    "answered": ("participant", "whisper", "whisper.answer"),
    "about": ("whisper", "task", "the task a whisper concerns"),
    "proposed": ("participant", "handoff", "handoff.propose"),
    "offered_to": ("handoff", "participant", "the recipient named on the proposal"),
    "resolved": ("participant", "handoff", "handoff.accept or handoff.decline; `resolution` carries which"),
    "covers": ("handoff", "task", "a task the handoff proposed to move"),
    "opened": ("participant", "deliberation", "deliberate.open"),
    "concerns": ("deliberation", "task", "deliberate.open naming the task the vote is about"),
    "voted": ("participant", "deliberation", "deliberate.vote; `vote` carries yea, nay or abstain"),
}


class SvgText(str):
    """SVG markup as a string that a notebook displays as a picture."""

    def _repr_svg_(self) -> str:
        return str(self)


@dataclass
class Node:
    """One node: its id, its type from :data:`NODE_TYPES`, a label to draw, and attributes."""
    id: str
    type: str
    label: str
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class Edge:
    """One edge: source and target node ids, its type from :data:`EDGE_TYPES`, and the envelope's ``seq`` and ``ts`` where it has one."""
    source: str
    target: str
    type: str
    seq: int | None = None
    ts: Any = None
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class Graph:
    """Nodes by id and a list of edges. ``workspace`` names the chain it came from."""
    workspace: str
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)

    def add_node(self, id: str, type: str, label: str | None = None, **attrs: Any) -> Node:
        """Add a node, or merge attributes into the one already there under that id."""
        if id not in self.nodes:
            self.nodes[id] = Node(id=id, type=type, label=label or id, attrs=dict(attrs))
        else:
            self.nodes[id].attrs.update({k: v for k, v in attrs.items() if v is not None})
        return self.nodes[id]

    def add_edge(self, source: str, target: str, type: str, *, seq: int | None = None,
                 ts: Any = None, **attrs: Any) -> Edge:
        """Add an edge of a declared type; an undeclared type raises ``ValueError``."""
        if type not in EDGE_TYPES:
            raise ValueError(f"Unknown edge type {type!r}")
        e = Edge(source=source, target=target, type=type, seq=seq, ts=ts, attrs=dict(attrs))
        self.edges.append(e)
        return e

    def neighbours(self, id: str) -> set[str]:
        """The ids one edge away from ``id``, in either direction."""
        out = set()
        for e in self.edges:
            if e.source == id:
                out.add(e.target)
            elif e.target == id:
                out.add(e.source)
        return out

    def subgraph(self, ids: Iterable[str]) -> "Graph":
        """The graph induced on ``ids``: those nodes and the edges between them."""
        keep = set(ids)
        g = Graph(self.workspace)
        for i in keep:
            if i in self.nodes:
                n = self.nodes[i]
                g.nodes[i] = Node(n.id, n.type, n.label, dict(n.attrs))
        g.edges = [Edge(e.source, e.target, e.type, e.seq, e.ts, dict(e.attrs))
                   for e in self.edges if e.source in keep and e.target in keep]
        return g

    def __repr__(self) -> str:
        by_type: dict[str, int] = defaultdict(int)
        for n in self.nodes.values():
            by_type[n.type] += 1
        parts = ", ".join(f"{k}={v}" for k, v in sorted(by_type.items()))
        return f"<Graph {self.workspace!r} {len(self.nodes)} nodes ({parts}), {len(self.edges)} edges>"


# ============================================================================
#   Building the graph from the tables
# ============================================================================

def _kind(uri: str | None) -> str | None:
    if not isinstance(uri, str) or ":" not in uri:
        return None
    return uri.split(":", 1)[0]


def _s(v: Any) -> str | None:
    return v if isinstance(v, str) else None


def _seq(v: Any) -> int | None:
    try:
        return int(v) if v is not None and not (isinstance(v, float) and math.isnan(v)) else None
    except (TypeError, ValueError):
        return None


def build(f: Frames) -> Graph:
    """The whole chain as a graph."""
    g = Graph(f.chain.workspace)
    ws = f.chain.workspace
    g.add_node(ws, "workspace", ws)

    def participant(uri: str | None) -> str | None:
        uri = _s(uri)
        if uri is None:
            return None
        g.add_node(uri, "participant", uri, kind=_kind(uri))
        return uri

    for r in f.participants.itertuples(index=False):
        p = participant(r.participant)
        if p:
            g.nodes[p].attrs.update({"role": _s(r.role), "kind": _s(r.kind) or _kind(p)})
            g.add_edge(p, ws, "member_of", ts=r.joined_at)

    # Tasks and their delegation, assignment and supersession.
    for t in f.tasks.itertuples(index=False):
        g.add_node(t.task_id, "task", t.task_id, kind=_s(t.kind), state=_s(t.state),
                   outcome=_s(t.outcome), mode=_s(t.mode), created_at=t.created_at,
                   settled_at=t.settled_at, id_certain=bool(t.id_certain) if pd.notna(t.id_certain) else None)
        d = participant(t.delegator)
        if d:
            g.add_edge(d, t.task_id, "delegated", ts=t.created_at)
        a = participant(t.assignee)
        if a:
            g.add_edge(t.task_id, a, "assigned_to", certain=bool(t.assignee_certain) if pd.notna(t.assignee_certain) else None)
        if _s(t.supersedes):
            g.add_node(t.supersedes, "task", t.supersedes)
            g.add_edge(t.task_id, t.supersedes, "supersedes", ts=t.created_at)

    # The envelopes themselves, by sequence number, for the fields the tables
    # fold: who a review was addressed to, who a whisper was asked of.
    params_by_seq: dict[int, dict] = {}
    for e in f.chain.events:
        env = e.get("envelope") if isinstance(e, dict) else None
        if isinstance(env, dict) and isinstance(e.get("seq"), int) and isinstance(env.get("params"), dict):
            params_by_seq[e["seq"]] = env["params"]

    def addressed(seq: int | None) -> list[str]:
        to = params_by_seq.get(seq, {}).get("to") if seq is not None else None
        if isinstance(to, str):
            to = [to]
        return [u for u in (to or []) if isinstance(u, str) and u]

    # Review passes, the artefacts under them, and who asked whom.
    requesters: dict[tuple[str, int], tuple[str | None, Any, int | None]] = {}
    ev = f.events
    req_events = ev[ev["method"].isin(("review.request", "task.complete"))].sort_values("seq")
    if not req_events.empty:
        # A task.complete followed by a review.request on the same artefact
        # opens one pass; count review.request first and fall back to
        # task.complete where a task's review was opened implicitly.
        opened: dict[str, list[tuple[str | None, Any, int | None, str]]] = defaultdict(list)
        for r in req_events.itertuples(index=False):
            if _s(r.task_id):
                opened[r.task_id].append((_s(r.actor), r.ts, _seq(r.seq), r.method))
        for tid, evs in opened.items():
            explicit = [e for e in evs if e[3] == "review.request"] or evs
            for i, e in enumerate(explicit):
                requesters[(tid, i)] = (e[0], e[1], e[2])

    passes = f.decisions.groupby(["task_id", "review_index"], dropna=False) if not f.decisions.empty else []
    seen_passes: set[tuple[str, int]] = set()
    for (tid, idx), part in passes:
        idx = int(idx) if pd.notna(idx) else 0
        seen_passes.add((tid, idx))
        rid = f"review:{tid}:{idx}"
        first = part.sort_values("seq").iloc[0]
        g.add_node(rid, "review", f"review {idx} of {tid}", rule=_s(first.rule),
                   requested_at=first.requested_at, review_index=idx)
        g.add_edge(rid, tid, "reviews")
        art = f"draft:{tid}:{idx}"
        g.add_node(art, "artefact", f"draft {idx} of {tid}", kind="draft")
        g.add_edge(rid, art, "under_review")
        requester, rts, rseq = requesters.get((tid, idx), (None, None, None))
        if requester:
            participant(requester)
            g.add_edge(requester, rid, "requested", ts=rts, seq=rseq)
            g.add_edge(requester, art, "produced", ts=rts, seq=rseq)
        asked = addressed(rseq) or list(part["reviewer"].dropna().unique())
        for reviewer in asked:
            participant(reviewer)
            g.add_edge(rid, reviewer, "asked_to", ts=rts if rts is not None else first.requested_at, seq=rseq)
        for d in part.sort_values("seq").itertuples(index=False):
            did = f"decision:{tid}:{_seq(d.seq)}"
            g.add_node(did, "decision", f"{d.kind} by {d.reviewer}", kind=_s(d.kind), ts=d.ts,
                       is_final=bool(d.is_final) if pd.notna(d.is_final) else None,
                       latency_s=float(d.latency_s) if pd.notna(d.latency_s) else None,
                       tags=list(d.tags) if isinstance(d.tags, list) else [])
            p = participant(d.reviewer)
            if p:
                g.add_edge(p, did, "decided", seq=_seq(d.seq), ts=d.ts)
            g.add_edge(did, rid, "decision_on", seq=_seq(d.seq), ts=d.ts)

    # Review passes that never reached a decision, because the task is still
    # open or was escalated, cancelled or superseded first: the request is on
    # the chain even though no decision is, so the pass and its draft are
    # nodes too.
    for (tid, idx), (requester, rts, rseq) in requesters.items():
        if (tid, idx) in seen_passes or tid not in g.nodes:
            continue
        rid = f"review:{tid}:{idx}"
        g.add_node(rid, "review", f"review {idx} of {tid}", requested_at=rts, review_index=idx,
                   undecided=True)
        g.add_edge(rid, tid, "reviews")
        art = f"draft:{tid}:{idx}"
        g.add_node(art, "artefact", f"draft {idx} of {tid}", kind="draft")
        g.add_edge(rid, art, "under_review")
        if requester:
            participant(requester)
            g.add_edge(requester, rid, "requested", ts=rts, seq=rseq)
            g.add_edge(requester, art, "produced", ts=rts, seq=rseq)
        for reviewer in addressed(rseq):
            participant(reviewer)
            g.add_edge(rid, reviewer, "asked_to", ts=rts, seq=rseq)

    # Overrides: the corrected artefact and what it was based on.
    for o in f.overrides.itertuples(index=False):
        did = f"decision:{o.task_id}:{_seq(o.seq)}"
        if did not in g.nodes:
            g.add_node(did, "decision", f"override by {o.reviewer}", kind="override", ts=o.ts)
            p = participant(o.reviewer)
            if p:
                g.add_edge(p, did, "decided", seq=_seq(o.seq), ts=o.ts)
        corrected = f"override:{o.task_id}:{_seq(o.seq)}"
        g.add_node(corrected, "artefact", f"corrected {o.task_id}", kind="override",
                   intent_preserved=bool(o.intent_preserved) if pd.notna(o.intent_preserved) else None,
                   top_path=_s(o.top_path), tags=list(o.tags) if isinstance(o.tags, list) else [])
        g.add_edge(did, corrected, "overrode", seq=_seq(o.seq), ts=o.ts)
        # The draft this override was based on is the artefact of the pass it settled.
        pass_idx = None
        if not f.decisions.empty:
            row = f.decisions[(f.decisions["task_id"] == o.task_id) & (f.decisions["seq"] == o.seq)]
            if not row.empty and pd.notna(row.iloc[0]["review_index"]):
                pass_idx = int(row.iloc[0]["review_index"])
        if pass_idx is not None:
            g.add_edge(corrected, f"draft:{o.task_id}:{pass_idx}", "based_on")

    # Executions that name the decision they carry out: a task.complete whose
    # params carry `fulfils` (CEP-001). The value is the producer's reference
    # to a decision. A mapping with task_id and seq lands on that decision
    # node; any other value becomes a referenced decision node under its own
    # text, since the chain records the claim without resolving it.
    completes = ev[ev["method"] == "task.complete"].sort_values("seq")
    for r in completes.itertuples(index=False):
        ref = params_by_seq.get(_seq(r.seq), {}).get("fulfils")
        if ref is None or not _s(r.task_id):
            continue
        if isinstance(ref, dict) and _s(ref.get("task_id")) and isinstance(ref.get("seq"), int):
            target = f"decision:{ref['task_id']}:{ref['seq']}"
        else:
            target = str(ref)
        art = f"output:{r.task_id}:{_seq(r.seq)}"
        g.add_node(art, "artefact", f"output of {r.task_id}", kind="output")
        a = participant(r.actor)
        if a:
            g.add_edge(a, art, "produced", seq=_seq(r.seq), ts=r.ts)
        if target not in g.nodes:
            g.add_node(target, "decision", target, kind="referenced")
        g.add_edge(art, target, "fulfils", seq=_seq(r.seq), ts=r.ts)

    # Whispers. Who was asked is on the whisper.ask envelope; the asks are
    # matched to the table's rows by task, asker and time, in order.
    ask_queue: dict[tuple[str | None, str | None], deque] = defaultdict(deque)
    asks = ev[ev["method"] == "whisper.ask"].sort_values("seq")
    for r in asks.itertuples(index=False):
        ask_queue[(_s(r.task_id), _s(r.actor))].append((r.ts, addressed(_seq(r.seq))))
    for w in f.whispers.sort_values("asked_at").itertuples(index=False):
        g.add_node(w.whisper_id, "whisper", _s(w.question) or w.whisper_id, state=_s(w.state),
                   lapsed=bool(w.lapsed) if pd.notna(w.lapsed) else None, asked_at=w.asked_at)
        a = participant(w.asker)
        if a:
            g.add_edge(a, w.whisper_id, "asked", ts=w.asked_at)
        if _s(w.task_id):
            g.add_node(w.task_id, "task", w.task_id)
            g.add_edge(w.whisper_id, w.task_id, "about")
        queue = ask_queue.get((_s(w.task_id), _s(w.asker)))
        if queue:
            _, to = queue.popleft()
            for who in to:
                participant(who)
                g.add_edge(w.whisper_id, who, "whispered_to")
        b = participant(w.answered_by)
        if b:
            g.add_edge(b, w.whisper_id, "answered", ts=w.answered_at)

    # Handoffs.
    for h in f.handoffs.itertuples(index=False):
        g.add_node(h.handoff_id, "handoff", h.handoff_id, resolution=_s(h.resolution),
                   proposed_at=h.proposed_at, resolved_at=h.resolved_at)
        p = participant(h.proposer)
        if p:
            g.add_edge(p, h.handoff_id, "proposed", seq=_seq(h.seq), ts=h.proposed_at)
        r = participant(h.recipient)
        if r:
            g.add_edge(h.handoff_id, r, "offered_to")
        rb = participant(h.resolved_by)
        if rb:
            g.add_edge(rb, h.handoff_id, "resolved", ts=h.resolved_at, resolution=_s(h.resolution))
        for tid in (h.task_ids if isinstance(h.task_ids, list) else []):
            if isinstance(tid, str):
                g.add_node(tid, "task", tid)
                g.add_edge(h.handoff_id, tid, "covers")

    # Deliberations and votes.
    for d in f.deliberations.itertuples(index=False):
        g.add_node(d.deliberation_id, "deliberation", _s(d.question) or d.deliberation_id,
                   rule=_s(d.rule), outcome=_s(d.outcome), opened_at=d.opened_at)
        o = participant(d.opener)
        if o:
            g.add_edge(o, d.deliberation_id, "opened", ts=d.opened_at)
        if _s(d.task_id):
            g.add_node(d.task_id, "task", d.task_id)
            g.add_edge(d.deliberation_id, d.task_id, "concerns")
    for v in f.votes.itertuples(index=False):
        p = participant(v.voter)
        if p:
            g.add_edge(p, v.deliberation_id, "voted", seq=_seq(v.seq), ts=v.ts, vote=_s(v.vote))

    return g


# ============================================================================
#   Lineage
# ============================================================================

_LINEAGE_STOP = {"workspace"}


def lineage(f: Frames | Graph, task_id: str) -> Graph:
    """
    Everything that led to one task: the task, the tasks it superseded and
    was superseded by, their review passes, artefacts, decisions, whispers,
    handoffs and deliberations, and the participants at each step.

    Participants are included as endpoints and are not traversed through, so
    a reviewer's other work stays out of the picture.
    """
    g = f if isinstance(f, Graph) else build(f)
    if task_id not in g.nodes:
        raise KeyError(f"{task_id!r} is not a task on this chain")
    keep: set[str] = set()
    queue: deque[str] = deque([task_id])
    while queue:
        n = queue.popleft()
        if n in keep:
            continue
        keep.add(n)
        node = g.nodes.get(n)
        if node is None or node.type in ("participant", "workspace"):
            continue
        for e in g.edges:
            other = None
            if e.source == n:
                other = e.target
            elif e.target == n:
                other = e.source
            if other is None or other in keep:
                continue
            if g.nodes[other].type in _LINEAGE_STOP:
                continue
            queue.append(other)
    return g.subgraph(keep)


def lineage_table(f: Frames | Graph, task_id: str) -> pd.DataFrame:
    """
    The lineage as rows in time order: who did what to which node, for a
    swimlane with one lane per participant.
    """
    sub = lineage(f, task_id)
    rows = []
    for e in sub.edges:
        s, t = sub.nodes[e.source], sub.nodes[e.target]
        if s.type == "participant":
            actor, subject = s, t
        elif t.type == "participant":
            actor, subject = t, s
        else:
            continue
        ts = e.ts
        if ts is None or (isinstance(ts, float) and math.isnan(ts)):
            ts = (subject.attrs.get("ts") or subject.attrs.get("created_at") or subject.attrs.get("asked_at")
                  or subject.attrs.get("requested_at") or subject.attrs.get("proposed_at") or subject.attrs.get("opened_at"))
        rows.append({"ts": ts, "seq": e.seq, "actor": actor.id, "actor_kind": actor.attrs.get("kind"),
                     "action": e.type, "node": subject.id, "node_type": subject.type,
                     "label": subject.label, "detail": {k: v for k, v in e.attrs.items() if v is not None}})
    out = pd.DataFrame(rows, columns=["ts", "seq", "actor", "actor_kind", "action", "node",
                                      "node_type", "label", "detail"])
    if not out.empty:
        out["ts"] = pd.to_datetime(out["ts"], utc=True, errors="coerce")
        out = out.sort_values(["ts", "seq"], na_position="last").reset_index(drop=True)
    return out


# ============================================================================
#   Collaboration
# ============================================================================

def collaboration(f: Frames) -> pd.DataFrame:
    """
    The participants of a workspace and the work that passed between them, as
    weighted directed edges: one row per (source, target, relation).

    ``reviewed`` runs from a reviewer to the assignee whose work they decided
    on, with ``weight`` decisions and ``mean_latency_s``. ``delegated`` runs
    from delegator to assignee. ``handed_off`` from proposer to recipient with
    the resolution counts. ``whispered`` from asker to answerer.
    """
    rows: list[dict] = []
    d = f.decisions.dropna(subset=["reviewer", "assignee"])
    if not d.empty:
        g = d.groupby(["reviewer", "assignee"])
        for (rv, asg), part in g:
            rows.append({"source": rv, "target": asg, "relation": "reviewed", "weight": len(part),
                         "mean_latency_s": float(part["latency_s"].mean()) if part["latency_s"].notna().any() else None,
                         "overrides": int((part["kind"] == "override").sum()),
                         "rejections": int((part["kind"] == "reject").sum())})
    t = f.tasks.dropna(subset=["delegator", "assignee"])
    if not t.empty:
        for (dl, asg), part in t.groupby(["delegator", "assignee"]):
            if dl == asg:
                continue
            rows.append({"source": dl, "target": asg, "relation": "delegated", "weight": len(part),
                         "mean_latency_s": None, "overrides": 0, "rejections": 0})
    h = f.handoffs.dropna(subset=["proposer", "recipient"])
    if not h.empty:
        for (pr, rc), part in h.groupby(["proposer", "recipient"]):
            rows.append({"source": pr, "target": rc, "relation": "handed_off", "weight": len(part),
                         "mean_latency_s": float(part["response_s"].mean()) if part["response_s"].notna().any() else None,
                         "overrides": 0, "rejections": 0,
                         "accepted": int((part["resolution"] == "accepted").sum()),
                         "declined": int((part["resolution"] == "declined").sum())})
    w = f.whispers.dropna(subset=["asker", "answered_by"])
    if not w.empty:
        for (ak, an), part in w.groupby(["asker", "answered_by"]):
            rows.append({"source": ak, "target": an, "relation": "whispered", "weight": len(part),
                         "mean_latency_s": float(part["response_s"].mean()) if part["response_s"].notna().any() else None,
                         "overrides": 0, "rejections": 0})
    cols = ["source", "target", "relation", "weight", "mean_latency_s", "overrides", "rejections",
            "accepted", "declined"]
    out = pd.DataFrame(rows)
    for c in cols:
        if c not in out.columns:
            out[c] = None
    return out[cols].sort_values(["relation", "weight"], ascending=[True, False]).reset_index(drop=True)


def _betweenness(nodes: list[str], adj: dict[str, dict[str, float]]) -> dict[str, float]:
    """Brandes' betweenness on a directed graph, unweighted paths."""
    bc = {v: 0.0 for v in nodes}
    for s in nodes:
        stack: list[str] = []
        pred: dict[str, list[str]] = {v: [] for v in nodes}
        sigma = {v: 0.0 for v in nodes}
        sigma[s] = 1.0
        dist = {v: -1 for v in nodes}
        dist[s] = 0
        q: deque[str] = deque([s])
        while q:
            v = q.popleft()
            stack.append(v)
            for w in adj.get(v, {}):
                if dist[w] < 0:
                    dist[w] = dist[v] + 1
                    q.append(w)
                if dist[w] == dist[v] + 1:
                    sigma[w] += sigma[v]
                    pred[w].append(v)
        delta = {v: 0.0 for v in nodes}
        while stack:
            w = stack.pop()
            for v in pred[w]:
                if sigma[w] > 0:
                    delta[v] += sigma[v] / sigma[w] * (1 + delta[w])
            if w != s:
                bc[w] += delta[w]
    n = len(nodes)
    scale = 1.0 / ((n - 1) * (n - 2)) if n > 2 else 0.0
    return {v: b * scale for v, b in bc.items()}


def centrality(f: Frames) -> pd.DataFrame:
    """
    Per participant on the collaboration graph: in and out degree (distinct
    counterparts), weighted in and out (work items), and betweenness, which
    is high for whoever sits between others' work.
    """
    c = collaboration(f)
    cols = ["participant", "kind", "in_degree", "out_degree", "in_weight", "out_weight", "betweenness"]
    if c.empty:
        return pd.DataFrame(columns=cols)
    nodes = sorted(set(c["source"]) | set(c["target"]))
    adj: dict[str, dict[str, float]] = defaultdict(dict)
    for r in c.itertuples(index=False):
        adj[r.source][r.target] = adj[r.source].get(r.target, 0) + float(r.weight)
    bc = _betweenness(nodes, adj)
    rows = []
    for v in nodes:
        outs = adj.get(v, {})
        ins = {s: a[v] for s, a in adj.items() if v in a}
        rows.append({"participant": v, "kind": _kind(v), "in_degree": len(ins), "out_degree": len(outs),
                     "in_weight": float(sum(ins.values())), "out_weight": float(sum(outs.values())),
                     "betweenness": bc[v]})
    return pd.DataFrame(rows, columns=cols).sort_values("betweenness", ascending=False).reset_index(drop=True)


def concentration(f: Frames, *, minimum: int = 10) -> pd.DataFrame:
    """
    For each assignee, how concentrated the reviewing of its work is: the top
    reviewer's share of its decisions and the Herfindahl index over
    reviewers (1.0 means one reviewer decides everything).
    """
    d = f.decisions.dropna(subset=["reviewer", "assignee"])
    cols = ["assignee", "n_decisions", "n_reviewers", "top_reviewer", "top_share", "hhi", "sufficient"]
    if d.empty:
        empty = pd.DataFrame(columns=cols)
        empty.attrs["minimum"] = int(minimum)
        return empty
    rows = []
    for asg, part in d.groupby("assignee"):
        counts = part["reviewer"].value_counts()
        shares = counts / counts.sum()
        rows.append({"assignee": asg, "n_decisions": int(counts.sum()), "n_reviewers": int(len(counts)),
                     "top_reviewer": counts.index[0], "top_share": float(shares.iloc[0]),
                     "hhi": float((shares ** 2).sum()), "sufficient": int(counts.sum()) >= minimum})
    out = pd.DataFrame(rows, columns=cols).sort_values("top_share", ascending=False).reset_index(drop=True)
    out.attrs["minimum"] = int(minimum)
    return out


# ============================================================================
#   Oversight coverage and separation of duties
# ============================================================================

#: Outcomes under which the work went out.
SHIPPED = ("approved", "overridden", "completed_after_rejection",
           "completed_bypassing_review", "completed_without_review")


def coverage(f: Frames) -> pd.DataFrame:
    """
    For each task whose work went out, whether a person was on its path: a
    human decided on the task itself or on a task it superseded, or a human
    did the work. Work an agent produced and shipped with no human decision
    anywhere behind it is uncovered.

    ``attrs`` on the result carry ``shipped``, ``covered``, ``share`` and the
    list of uncovered task ids. A workspace where every shipped task has a
    person on its path has a share of 1.0.
    """
    t = f.tasks
    cols = ["task_id", "kind", "assignee", "outcome", "shipped", "human_decision", "on_task",
            "on_predecessor", "human_performed", "covered"]
    if t.empty:
        out = pd.DataFrame(columns=cols)
        out.attrs.update({"shipped": 0, "covered": 0, "share": float("nan"), "uncovered": []})
        return out
    human_decided: set[str] = set()
    if not f.decisions.empty:
        human_decided = set(f.decisions[f.decisions["reviewer"].map(_kind) == "human"]["task_id"])
    supersedes = dict(zip(t["task_id"], t["supersedes"]))
    rows = []
    for r in t.itertuples(index=False):
        shipped = r.outcome in SHIPPED
        on_task = r.task_id in human_decided
        on_pred = False
        prev = supersedes.get(r.task_id)
        hops = 0
        while isinstance(prev, str) and hops < 100:
            if prev in human_decided:
                on_pred = True
                break
            prev = supersedes.get(prev)
            hops += 1
        performed = _kind(r.assignee) == "human"
        rows.append({"task_id": r.task_id, "kind": r.kind, "assignee": r.assignee, "outcome": r.outcome,
                     "shipped": shipped, "human_decision": on_task or on_pred, "on_task": on_task,
                     "on_predecessor": on_pred, "human_performed": performed,
                     "covered": shipped and (on_task or on_pred or performed)})
    out = pd.DataFrame(rows, columns=cols)
    shipped_n = int(out["shipped"].sum())
    covered_n = int(out["covered"].sum())
    out.attrs.update({"shipped": shipped_n, "covered": covered_n,
                      "share": covered_n / shipped_n if shipped_n else float("nan"),
                      "uncovered": list(out[out["shipped"] & ~out["covered"]]["task_id"])})
    return out


def duties(f: Frames) -> pd.DataFrame:
    """
    Where one actor held two roles that should be separate. One row per
    finding: ``check`` names it, ``subject`` the task or handoff, ``actor``
    who, and ``detail`` says which roles coincided.

    Checks: ``self_review`` (a task's assignee decided its own review),
    ``delegator_review`` (the person who delegated a task decided it),
    ``self_handoff`` (proposer and resolver of a handoff are the same),
    ``agent_decided`` (a decision on a review was made by an agent or service
    rather than a person).
    """
    rows = []
    d = f.decisions
    if not d.empty:
        t = f.tasks.set_index("task_id")
        for r in d.itertuples(index=False):
            if r.task_id in t.index:
                task = t.loc[r.task_id]
                if isinstance(r.reviewer, str) and r.reviewer == task["assignee"]:
                    rows.append({"check": "self_review", "subject": r.task_id, "actor": r.reviewer,
                                 "detail": "assignee decided their own review"})
                if isinstance(r.reviewer, str) and r.reviewer == task["delegator"]:
                    rows.append({"check": "delegator_review", "subject": r.task_id, "actor": r.reviewer,
                                 "detail": "delegator decided the task they delegated"})
            if _kind(r.reviewer) in ("agent", "service"):
                rows.append({"check": "agent_decided", "subject": r.task_id, "actor": r.reviewer,
                             "detail": f"{r.kind} recorded by a {_kind(r.reviewer)}"})
    h = f.handoffs
    if not h.empty:
        for r in h.itertuples(index=False):
            if isinstance(r.resolved_by, str) and r.resolved_by == r.proposer:
                rows.append({"check": "self_handoff", "subject": r.handoff_id, "actor": r.proposer,
                             "detail": "proposer resolved their own handoff"})
    return pd.DataFrame(rows, columns=["check", "subject", "actor", "detail"])


# ============================================================================
#   Layouts
# ============================================================================

def layout_spring(edges: pd.DataFrame | Graph, *, iterations: int = 300, seed: int = 0,
                  size: float = 1.0) -> pd.DataFrame:
    """
    Fruchterman-Reingold positions for the participants of a collaboration
    frame (from :func:`collaboration`) or the nodes of a :class:`Graph`. One
    row per node with ``x`` and ``y`` in ``[0, size]``. Deterministic for a
    seed.
    """
    if isinstance(edges, Graph):
        nodes = list(edges.nodes)
        pairs = [(e.source, e.target, 1.0) for e in edges.edges]
    else:
        if edges.empty:
            return pd.DataFrame(columns=["node", "x", "y"])
        nodes = sorted(set(edges["source"]) | set(edges["target"]))
        pairs = [(r.source, r.target, float(r.weight or 1.0)) for r in edges.itertuples(index=False)]
    n = len(nodes)
    if n == 0:
        return pd.DataFrame(columns=["node", "x", "y"])
    index = {v: i for i, v in enumerate(nodes)}
    rng = np.random.default_rng(seed)
    pos = rng.random((n, 2))
    k = math.sqrt(1.0 / n)
    temp = 0.1
    for _ in range(iterations):
        disp = np.zeros((n, 2))
        delta = pos[:, None, :] - pos[None, :, :]
        dist = np.linalg.norm(delta, axis=2) + 1e-9
        rep = (k * k / dist)[:, :, None] * delta / dist[:, :, None]
        np.fill_diagonal(rep[:, :, 0], 0)
        np.fill_diagonal(rep[:, :, 1], 0)
        disp += rep.sum(axis=1)
        for s, t, w in pairs:
            i, j = index[s], index[t]
            d = pos[i] - pos[j]
            dn = np.linalg.norm(d) + 1e-9
            force = (dn * dn / k) * math.log1p(w) * d / dn
            disp[i] -= force
            disp[j] += force
        length = np.linalg.norm(disp, axis=1) + 1e-9
        pos += disp / length[:, None] * np.minimum(length, temp)[:, None]
        pos = np.clip(pos, 0, 1)
        temp *= 0.985
    lo, hi = pos.min(axis=0), pos.max(axis=0)
    span = np.where(hi - lo > 0, hi - lo, 1.0)
    pos = (pos - lo) / span * size
    return pd.DataFrame({"node": nodes, "x": pos[:, 0], "y": pos[:, 1]})


def layout_lanes(table: pd.DataFrame) -> pd.DataFrame:
    """
    Swimlane coordinates for a :func:`lineage_table`: ``x`` is the position
    in time order, ``lane`` the participant, ``y`` the lane index. Humans are
    placed above agents and services.
    """
    if table.empty:
        return table.assign(x=[], y=[], lane=[])
    order = {"human": 0, "agent": 1, "service": 2, "group": 3}
    lanes = sorted(table["actor"].dropna().unique(), key=lambda a: (order.get(_kind(a), 9), a))
    index = {a: i for i, a in enumerate(lanes)}
    out = table.copy()
    out["x"] = range(len(out))
    out["lane"] = out["actor"]
    out["y"] = out["actor"].map(index)
    return out


# ============================================================================
#   Exports
# ============================================================================

def _plain(v: Any) -> Any:
    if isinstance(v, (pd.Timestamp,)):
        return None if pd.isna(v) else v.isoformat()
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return None if np.isnan(v) else float(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, float) and math.isnan(v):
        return None
    if v is pd.NaT:
        return None
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    return v


def to_node_link(g: Graph) -> dict[str, Any]:
    """
    The graph as node-link JSON: ``{"workspace", "nodes": [...], "edges": [...]}``,
    every node with ``id``, ``type``, ``label`` and its attributes, every edge
    with ``source``, ``target``, ``type``, ``seq``, ``ts`` and its attributes.
    ``ontology`` lists the node and edge types the export uses.
    """
    return {
        "workspace": g.workspace,
        "ontology": {"nodes": NODE_TYPES, "edges": {k: {"from": v[0], "to": v[1], "recorded_by": v[2]}
                                                    for k, v in EDGE_TYPES.items()}},
        "nodes": [{"id": n.id, "type": n.type, "label": n.label, **_plain(n.attrs)} for n in g.nodes.values()],
        "edges": [{"source": e.source, "target": e.target, "type": e.type, "seq": _plain(e.seq),
                   "ts": _plain(e.ts), **_plain(e.attrs)} for e in g.edges],
    }


def to_graphml(g: Graph) -> str:
    """The graph as GraphML text, with ``type`` and ``label`` keys on nodes and ``type`` on edges."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
             '  <key id="type" for="node" attr.name="type" attr.type="string"/>',
             '  <key id="label" for="node" attr.name="label" attr.type="string"/>',
             '  <key id="kind" for="node" attr.name="kind" attr.type="string"/>',
             '  <key id="etype" for="edge" attr.name="type" attr.type="string"/>',
             '  <key id="ts" for="edge" attr.name="ts" attr.type="string"/>',
             f'  <graph id="{escape(g.workspace)}" edgedefault="directed">']
    for n in g.nodes.values():
        lines.append(f'    <node id="{escape(n.id)}">')
        lines.append(f'      <data key="type">{escape(n.type)}</data>')
        lines.append(f'      <data key="label">{escape(str(n.label))}</data>')
        kind = n.attrs.get("kind")
        if isinstance(kind, str):
            lines.append(f'      <data key="kind">{escape(kind)}</data>')
        lines.append('    </node>')
    for i, e in enumerate(g.edges):
        lines.append(f'    <edge id="e{i}" source="{escape(e.source)}" target="{escape(e.target)}">')
        lines.append(f'      <data key="etype">{escape(e.type)}</data>')
        ts = _plain(e.ts)
        if ts:
            lines.append(f'      <data key="ts">{escape(str(ts))}</data>')
        lines.append('    </edge>')
    lines += ['  </graph>', '</graphml>']
    return "\n".join(lines)


def to_networkx(g: Graph):
    """The graph as a ``networkx.MultiDiGraph``. Needs networkx installed."""
    try:
        import networkx as nx
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ImportError("to_networkx needs networkx: pip install 'chap-analytics[graph]'") from exc
    G = nx.MultiDiGraph(workspace=g.workspace)
    for n in g.nodes.values():
        G.add_node(n.id, type=n.type, label=n.label, **_plain(n.attrs))
    for e in g.edges:
        G.add_edge(e.source, e.target, type=e.type, seq=_plain(e.seq), ts=_plain(e.ts), **_plain(e.attrs))
    return G


def dumps(g: Graph, **kwargs: Any) -> str:
    """:func:`to_node_link` as a JSON string."""
    return json.dumps(to_node_link(g), **kwargs)


# ============================================================================
#   The ontology, drawn
# ============================================================================

_FAMILIES = {
    "workspace": "people", "participant": "people",
    "task": "work", "review": "work", "artefact": "work", "decision": "work",
    "whisper": "exchange", "deliberation": "exchange", "handoff": "exchange",
}
_FAMILY_STYLE = {
    "people": ("#fbe9e7", "#c0392b", "People and the workspace"),
    "work": ("#e8eef6", "#1f4e79", "Work and the judgement on it"),
    "exchange": ("#f6f1dc", "#7d6608", "Exchanges between participants"),
}
#: The order of the node column, top to bottom. The task sits in the middle
#: so that what refers to it (reviews above, exchanges below) arcs to it
#: over the shortest spans.
_ONTOLOGY_COLUMN = ["workspace", "review", "artefact", "decision", "task", "whisper", "deliberation", "handoff"]
#: Edge types that record a participant's claim rather than an act the
#: coordinator checked; drawn dashed.
_ONTOLOGY_CLAIMED = {"fulfils"}


def _first_sentence(text: str) -> str:
    """The description up to its first full stop, without code marks, for a box label."""
    text = text.replace("`", "")
    head = text.split(". ")[0]
    return head if head.endswith(".") else head + "."


def _wrap(text: str, width: int, lines: int) -> list[str]:
    words, out, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + (1 if cur else 0) > width and cur:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        out.append(cur)
    if len(out) > lines:
        out = out[:lines]
        out[-1] = out[-1].rstrip(".,;") + "…"
    return out


def ontology_svg() -> SvgText:
    """
    The ontology of a CHAP workspace as an SVG diagram: every node type in
    :data:`NODE_TYPES` as a box and every edge type in :data:`EDGE_TYPES` as
    a labelled arrow between the types it joins. The picture is generated
    from the declarations, so it says what the graph export can contain.

    The participant is a lane down the left, because it touches every other
    type; what a participant does or receives runs as straight spokes to the
    column of node types beside it. What the work says about itself (which
    pass reviews which task, which decision settled which pass, what an
    override was based on) runs as arcs down the right, nested by span.
    """
    mono = "Menlo, Consolas, monospace"
    edge = "#6b7280"
    box_w, box_h, gap = 290, 70, 66
    row_step = box_h + gap
    top = 124
    bar_x0, bar_x1 = 44, 224
    col_x0 = 500
    col_x1 = col_x0 + box_w
    lane = 20  # spacing between parallel spokes to one box
    rows = {name: top + i * row_step + box_h / 2 for i, name in enumerate(_ONTOLOGY_COLUMN)}
    bar_y0, bar_y1 = top - 8, top + (len(_ONTOLOGY_COLUMN) - 1) * row_step + box_h + 8
    width, height = 1080, int(bar_y1 + 92)

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
           'font-family="Helvetica Neue, Helvetica, Arial, sans-serif">',
           '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">'
           f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{edge}"/></marker></defs>',
           f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
           '<text x="24" y="38" font-size="20" font-weight="600" fill="#1f2937">The ontology of a CHAP workspace</text>',
           '<text x="24" y="60" font-size="13" fill="#4b5563">Nine node types; every edge is an envelope or a field on one. '
           'Left: what a participant does or receives. Right: how the work refers to itself.</text>',
           '<text x="24" y="80" font-size="13" fill="#4b5563">A dashed edge is a claim the producer made, which the chain records without checking.</text>']

    labels: list[tuple[float, float, str, str]] = []  # x, y, text, anchor

    def label(x: float, y: float, text: str, anchor: str = "middle") -> None:
        labels.append((x, y, text, anchor))

    # -- the participant lane -------------------------------------------------
    fill, stroke, _ = _FAMILY_STYLE["people"]
    out.append(f'<rect x="{bar_x0}" y="{bar_y0}" width="{bar_x1 - bar_x0}" height="{bar_y1 - bar_y0}" rx="10" '
               f'fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>')
    out.append(f'<text x="{(bar_x0 + bar_x1) / 2}" y="{bar_y0 + 32}" font-size="16" font-weight="600" fill="{stroke}" text-anchor="middle">participant</text>')
    for i, line in enumerate(_wrap(_first_sentence(NODE_TYPES["participant"]), 25, 4)):
        out.append(f'<text x="{(bar_x0 + bar_x1) / 2}" y="{bar_y0 + 56 + 16 * i}" font-size="12" fill="#374151" text-anchor="middle">{escape(line)}</text>')

    # -- spokes: every edge with a participant at one end ------------------------
    spokes: dict[str, list[tuple[str, bool]]] = defaultdict(list)  # box -> [(edge, outward)]
    for name, (src, dst, _) in EDGE_TYPES.items():
        if src == "participant" and dst != "participant":
            spokes[dst].append((name, True))
        elif dst == "participant" and src != "participant":
            spokes[src].append((name, False))
    for box, edges in spokes.items():
        y_mid = rows[box]
        n = len(edges)
        for k, (name, outward) in enumerate(edges):
            y = y_mid + (k - (n - 1) / 2) * lane
            dash = ' stroke-dasharray="6 4"' if name in _ONTOLOGY_CLAIMED else ""
            if outward:
                out.append(f'<line x1="{bar_x1}" y1="{y:.1f}" x2="{col_x0}" y2="{y:.1f}" stroke="{edge}" stroke-width="1.4" marker-end="url(#arrow)"{dash}/>')
            else:
                out.append(f'<line x1="{col_x0}" y1="{y:.1f}" x2="{bar_x1}" y2="{y:.1f}" stroke="{edge}" stroke-width="1.4" marker-end="url(#arrow)"{dash}/>')
            label((bar_x1 + col_x0) / 2, y - 6, name)

    # -- arcs: every edge between two non-participant types --------------------
    index = {name: i for i, name in enumerate(_ONTOLOGY_COLUMN)}
    arcs = [(name, src, dst) for name, (src, dst, _) in EDGE_TYPES.items()
            if "participant" not in (src, dst) and src != dst]
    # Pairs joined both ways share a lane on the right; the second of the two
    # is pushed outward and its label moved up the arc, so the two stay apart.
    seen_pairs: dict[tuple[str, str], int] = defaultdict(int)
    for name, src, dst in arcs:
        span = abs(index[src] - index[dst])
        pair = tuple(sorted((src, dst)))
        k = seen_pairs[pair]
        seen_pairs[pair] += 1
        rx = {1: 34, 2: 84, 3: 146}[span] + 30 * k
        y0, y1 = rows[src], rows[dst]
        ry = abs(y1 - y0) / 2
        sweep = 1 if y1 > y0 else 0
        dash = ' stroke-dasharray="6 4"' if name in _ONTOLOGY_CLAIMED else ""
        out.append(f'<path d="M {col_x1} {y0:.1f} A {rx} {ry:.1f} 0 0 {sweep} {col_x1} {y1:.1f}" fill="none" '
                   f'stroke="{edge}" stroke-width="1.4" marker-end="url(#arrow)"{dash}/>')
        t = 0.5 if k == 0 else 0.78
        theta = math.pi * t
        # From the upper endpoint round to the lower, whichever way the arrow points.
        lx = col_x1 + rx * math.sin(theta)
        ly = min(y0, y1) + ry - ry * math.cos(theta)
        label(lx, ly, name)

    # -- self loops, drawn above the box ----------------------------------------
    for name, (src, dst, _) in EDGE_TYPES.items():
        if src == dst:
            y_top = rows[src] - box_h / 2
            x = col_x0 + box_w / 2 - 28
            out.append(f'<path d="M {x} {y_top} C {x - 14} {y_top - 46}, {x + 70} {y_top - 46}, {x + 56} {y_top}" fill="none" '
                       f'stroke="{edge}" stroke-width="1.4" marker-end="url(#arrow)"/>')
            label(x + 28, y_top - 42, name)

    # -- the boxes ----------------------------------------------------------------
    for name in _ONTOLOGY_COLUMN:
        y = rows[name]
        fill, stroke, _ = _FAMILY_STYLE[_FAMILIES[name]]
        out.append(f'<rect x="{col_x0}" y="{y - box_h / 2:.1f}" width="{box_w}" height="{box_h}" rx="8" fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>')
        out.append(f'<text x="{col_x0 + box_w / 2}" y="{y - 13:.1f}" font-size="16" font-weight="600" fill="{stroke}" text-anchor="middle">{escape(name)}</text>')
        for i, line in enumerate(_wrap(_first_sentence(NODE_TYPES[name]), 44, 2)):
            out.append(f'<text x="{col_x0 + box_w / 2}" y="{y + 8 + 15 * i:.1f}" font-size="12" fill="#374151" text-anchor="middle">{escape(line)}</text>')

    # -- labels last, over the lines ---------------------------------------------
    for (x, y, text, anchor) in labels:
        w = 7.4 * len(text) + 10
        x0 = x - w / 2 if anchor == "middle" else x
        out.append(f'<rect x="{x0:.1f}" y="{y - 9:.1f}" width="{w:.1f}" height="17" rx="3" fill="#ffffff" fill-opacity="0.94"/>')
        out.append(f'<text x="{x:.1f}" y="{y + 4:.1f}" font-size="12.5" fill="#374151" text-anchor="{anchor}" font-family="{mono}">{escape(text)}</text>')

    # -- legend --------------------------------------------------------------------
    lx, ly = 24, height - 26
    for fill, stroke, text in _FAMILY_STYLE.values():
        out.append(f'<rect x="{lx}" y="{ly - 12}" width="18" height="13" rx="2" fill="{fill}" stroke="{stroke}" stroke-width="1.4"/>')
        out.append(f'<text x="{lx + 24}" y="{ly - 1}" font-size="13" fill="#374151">{escape(text)}</text>')
        lx += 24 + 7.6 * len(text) + 34
    out.append("</svg>")
    return SvgText("\n".join(out))
