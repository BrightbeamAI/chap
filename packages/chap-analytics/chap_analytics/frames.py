"""
Projecting a chain into tables.

The chain is replayed exactly once into an index of what happened, and every
table is then a view over that index. Replaying rather than reading server
state is what lets the whole layer work against a plain ``audit.read``, which
is all an MCP client can obtain.

Two rules hold throughout.

Every table has exactly the columns ``schema.py`` declares, in that order,
with those dtypes. A column the source could not populate is present and null.
Downstream code can therefore reference any column without first asking where
the chain came from.

Nothing is invented. Where a value is computed rather than recorded, its
provenance in the schema says so, and where the computation cannot be trusted,
the column is left null instead of guessed.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from . import schema
from .load import Chain

__all__ = ["frames", "Frames"]

_TERMINAL = {"completed", "cancelled", "superseded"}
_SETTLED = _TERMINAL | {"declined", "abstained", "escalated"}


# ---------------------------------------------------------------- helpers

def _ts(value: Any) -> pd.Timestamp | None:
    if not value:
        return None
    try:
        out = pd.to_datetime(value, utc=True, format="ISO8601")
    except (ValueError, TypeError):
        return None
    return None if pd.isna(out) else out


def _decimal(value: Any) -> float | None:
    """
    Parse a CHAP number.

    Fractional values travel as decimal strings, per SPECIFICATION §7, because
    canonicalisation admits only integers. Every notebook that forgets this
    gets a column of strings and a silent failure downstream, so it is handled
    once, here.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (ValueError, TypeError):
        return None


def _uri_kind(uri: Any) -> str | None:
    if not isinstance(uri, str) or ":" not in uri:
        return None
    return uri.split(":", 1)[0]


def _top_path(pointer: Any) -> str | None:
    """First segment of an RFC 6901 pointer: '/comments/0/severity' -> 'comments'."""
    if not isinstance(pointer, str):
        return None
    parts = [p for p in pointer.split("/") if p != ""]
    return parts[0] if parts else "(root)"


def _rule_satisfied(rule: str, approvers: set[str], addressed: int) -> bool:
    """Mirrors the coordinator's reviewSatisfied, so `is_final` means what it says."""
    rule = rule or "any_one_approves"
    if rule == "all_approve":
        return addressed > 0 and len(approvers) >= addressed
    if rule.startswith("quorum:"):
        try:
            return len(approvers) >= int(rule.split(":", 1)[1])
        except ValueError:
            return False
    return len(approvers) >= 1


# ---------------------------------------------------------------- the index

@dataclass
class _Task:
    task_id: str
    kind: str | None = None
    delegator: str | None = None
    original_assignee: str | None = None
    assignee: str | None = None
    mode: str | None = None
    review_required: bool | None = None
    created_at: pd.Timestamp | None = None
    state: str = "created"
    settled_at: pd.Timestamp | None = None
    confidence: float | None = None
    criticality: str | None = None
    risk_tier: str | None = None
    supersedes: str | None = None
    # review
    requested_at: pd.Timestamp | None = None
    requested_to: list[str] = field(default_factory=list)
    rule: str | None = None
    artefact: Any = None
    decisions: list[dict] = field(default_factory=list)


@dataclass
class _Delib:
    deliberation_id: str
    task_id: str | None = None
    opener: str | None = None
    rule: str | None = None
    question: str | None = None
    opened_at: pd.Timestamp | None = None
    closed_at: pd.Timestamp | None = None
    participants: list[str] = field(default_factory=list)
    votes: list[dict] = field(default_factory=list)


@dataclass
class _Whisper:
    whisper_id: str
    task_id: str | None = None
    asker: str | None = None
    question: str | None = None
    asked_at: pd.Timestamp | None = None
    deadline_ms: int | None = None
    had_options: bool = False
    answered_at: pd.Timestamp | None = None
    answered_by: str | None = None
    answer: str | None = None


@dataclass
class _Index:
    tasks: dict[str, _Task] = field(default_factory=dict)
    delibs: dict[str, _Delib] = field(default_factory=dict)
    whispers: dict[str, _Whisper] = field(default_factory=dict)
    members: dict[str, dict] = field(default_factory=dict)
    overrides: list[dict] = field(default_factory=list)
    routing: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)


def _replay(chain: Chain) -> _Index:
    ix = _Index()
    # Deliberation and whisper ids are minted by the coordinator and returned
    # in the result, which the log does not carry. They are recoverable
    # because every later envelope names the id it acts on, so the opening
    # envelope is matched to the first id seen for it.
    pending_delib: list[_Delib] = []
    pending_whisper: list[_Whisper] = []

    def task(tid: str | None) -> _Task | None:
        if not tid:
            return None
        return ix.tasks.setdefault(tid, _Task(task_id=tid))

    for entry in chain.events:
        env = entry.get("envelope") or {}
        p = env.get("params") or {}
        method = env.get("method") or ""
        actor = p.get("from") or env.get("from")
        ts = _ts(env.get("ts")) or _ts(entry.get("arrived"))
        seq = entry.get("seq")
        # escalate.raise names its subject original_task_id rather than
        # task_id. The event is still about that task, and treating it
        # otherwise leaves the task out of the id ordering.
        tid = p.get("task_id") or p.get("original_task_id")

        ix.events.append({
            "seq": seq,
            "workspace": p.get("workspace") or chain.workspace,
            "ts": ts,
            "arrived": _ts(entry.get("arrived")),
            "method": method,
            "actor": actor,
            "actor_kind": _uri_kind(actor),
            "task_id": tid,
            "prev_hash": entry.get("prev_hash"),
            "chained": entry.get("prev_hash") is not None,
        })

        if method == "participant.join":
            ix.members[actor] = {
                "participant": actor, "kind": p.get("type"), "role": p.get("role"),
                "joined_at": ts, "left_at": None,
            }

        elif method == "participant.leave":
            if actor in ix.members:
                ix.members[actor]["left_at"] = ts

        elif method == "task.create":
            # The id is in the result, not the envelope. Tasks are matched by
            # the order they were created, which the log preserves.
            hints = p.get("routing_hints") or {}
            t = _Task(
                task_id="",  # filled below once an id is observed
                kind=p.get("kind"), delegator=actor,
                original_assignee=p.get("assignee"), assignee=p.get("assignee"),
                mode=p.get("mode"), review_required=p.get("review_required"),
                created_at=ts, state="created",
                criticality=hints.get("criticality"), risk_tier=hints.get("risk_tier"),
                confidence=_decimal(hints.get("confidence")),
            )
            ix.tasks[f"__pending_{seq}"] = t

        elif method == "task.update" and tid:
            t = task(tid)
            new_state = p.get("state")
            if new_state:
                t.state = new_state
                if new_state in _SETTLED:
                    t.settled_at = ts

        elif method == "task.complete" and tid:
            t = task(tid)
            conf = _decimal(p.get("confidence"))
            if conf is None:
                conf = _decimal((p.get("routing_hints") or {}).get("confidence"))
            if conf is not None:
                t.confidence = conf
            if t.review_required:
                t.state = "review_requested"
                t.requested_at = t.requested_at or ts
                t.artefact = p.get("output") if t.artefact is None else t.artefact
                t.rule = t.rule or "any_one_approves"
            else:
                t.state = "completed"
                t.settled_at = ts

        elif method == "review.request" and tid:
            t = task(tid)
            t.state = "review_requested"
            t.requested_at = t.requested_at or ts
            to = p.get("to")
            addressed = [to] if isinstance(to, str) else list(to or [])
            for r in addressed:
                if r not in t.requested_to:
                    t.requested_to.append(r)
            if p.get("rule"):
                t.rule = p["rule"]
            t.rule = t.rule or "any_one_approves"
            t.artefact = p.get("artefact")

        elif method in ("decide.approve", "decide.reject", "decide.override", "abstain.declare") and tid:
            t = task(tid)
            kind = {"decide.approve": "approve", "decide.reject": "reject",
                    "decide.override": "override", "abstain.declare": "abstain"}[method]
            t.decisions.append({
                "seq": seq, "reviewer": actor, "kind": kind, "ts": ts,
                "comment": p.get("comment") or p.get("reason"),
                "tags": list(p.get("tags") or []),
                "request_revision": bool(p.get("request_revision")) if kind == "reject" else None,
                "abstain_category": p.get("category") if kind == "abstain" else None,
                "digest_bound": p.get("approved_artefact_digest") is not None,
            })
            if kind == "override":
                diff = list(p.get("diff") or [])
                ix.overrides.append({
                    "seq": seq, "task_id": tid, "reviewer": actor, "ts": ts,
                    "rationale": p.get("rationale"), "tags": list(p.get("tags") or []),
                    "policy_refs": list(p.get("policy_refs") or []),
                    "intent_preserved": p.get("intent_preserved"),
                    "logical_id": p.get("logical_id"),
                    "diff": diff, "based_on": t.artefact,
                })
                t.state, t.settled_at = "completed", ts
            elif kind == "abstain":
                t.state, t.settled_at = "abstained", ts
            elif kind == "reject":
                if p.get("request_revision"):
                    t.state = "in_progress"
                else:
                    t.state, t.settled_at = "declined", ts
            else:
                approvers = {d["reviewer"] for d in t.decisions if d["kind"] == "approve"}
                if _rule_satisfied(t.rule or "any_one_approves", approvers, len(t.requested_to)):
                    t.state, t.settled_at = "completed", ts

        elif method == "escalate.raise":
            orig = p.get("original_task_id")
            if orig:
                t = task(orig)
                t.state, t.settled_at = "escalated", ts
            spec = p.get("new_task") or {}
            ix.tasks[f"__pending_{seq}"] = _Task(
                task_id="", kind=spec.get("kind"), delegator=actor,
                original_assignee=spec.get("assignee"), assignee=spec.get("assignee"),
                created_at=ts, state="created", supersedes=orig)

        elif method == "control.cancel" and tid:
            t = task(tid)
            t.state, t.settled_at = "cancelled", ts

        elif method == "control.supersede" and tid:
            t = task(tid)
            t.state, t.settled_at = "superseded", ts
            spec = p.get("successor_task") or {}
            ix.tasks[f"__pending_{seq}"] = _Task(
                task_id="", kind=spec.get("kind"), delegator=actor,
                original_assignee=spec.get("assignee"), assignee=spec.get("assignee"),
                created_at=ts, state="created", supersedes=tid)

        elif method == "control.pause" and tid and p.get("scope", "task") == "task":
            task(tid).state = "paused"

        elif method == "control.resume" and tid and p.get("scope", "task") == "task":
            task(tid).state = "in_progress"

        elif method == "deliberate.open":
            to = p.get("to") or p.get("participants") or []
            d = _Delib(
                deliberation_id="", task_id=tid, opener=actor, rule=p.get("rule"),
                question=p.get("question"), opened_at=ts,
                participants=[to] if isinstance(to, str) else list(to),
            )
            pending_delib.append(d)

        elif method in ("deliberate.vote", "deliberate.comment", "deliberate.close"):
            did = p.get("deliberation_id")
            if did and did not in ix.delibs and pending_delib:
                d = pending_delib.pop(0)
                d.deliberation_id = did
                ix.delibs[did] = d
            d = ix.delibs.get(did)
            if d is None:
                continue
            if method == "deliberate.vote":
                d.votes.append({
                    "seq": seq, "voter": actor, "vote": p.get("vote"), "ts": ts,
                    "comment": p.get("comment"),
                    "veto_invoked": bool(p.get("veto_invoked")),
                })
            elif method == "deliberate.close":
                d.closed_at = ts

        elif method == "whisper.ask":
            pending_whisper.append(_Whisper(
                whisper_id="", task_id=tid, asker=actor, question=p.get("question"),
                asked_at=ts, deadline_ms=p.get("deadline_ms"),
                had_options=bool(p.get("options")),
            ))

        elif method == "whisper.answer":
            wid = p.get("whisper_id")
            if wid and wid not in ix.whispers and pending_whisper:
                w = pending_whisper.pop(0)
                w.whisper_id = wid
                ix.whispers[wid] = w
            w = ix.whispers.get(wid)
            if w is not None:
                w.answered_at = ts
                w.answered_by = actor
                w.answer = p.get("answer_option") or p.get("answer")

        elif method in ("task.route", "review.depth", "escalate.auto"):
            ix.routing.append({
                "seq": seq, "task_id": tid, "method": method, "ts": ts,
                "workspace": p.get("workspace") or chain.workspace,
            })

    # Tasks created but never referenced again keep their placeholder key. Any
    # task acted on later exists under its real id, so the placeholders are
    # matched to real ids in creation order.
    _resolve(ix, chain, pending_delib, pending_whisper)
    _merge_state(ix, chain)
    return ix


def _resolve(ix: _Index, chain: Chain, pending_delib: list[_Delib],
             pending_whisper: list[_Whisper]) -> None:
    """
    Attach the ids the coordinator minted to the creations that produced them.

    A task, whisper or deliberation id is generated server-side and returned
    in the *result*. The audit log records envelopes, not results, so an id
    only becomes visible when a later envelope acts on it. Two consequences,
    both handled here rather than papered over.

    Something acted on later is matched by that id directly. Something created
    and never touched again has no id anywhere in the envelope stream: it is
    matched against server state where the source carried it, and otherwise
    given a synthetic id so a count is not silently short. A synthetic id is
    marked as such, because it is ours and not the coordinator's.
    """
    _resolve_tasks(ix, chain)
    _resolve_pending(
        ix.delibs, pending_delib, (chain.state or {}).get("deliberations") or {},
        "opened_at", "deliberation_id", "deliberation")
    _resolve_pending(
        ix.whispers, pending_whisper, (chain.state or {}).get("whispers") or {},
        "asked_at", "whisper_id", "whisper")


def _resolve_tasks(ix: _Index, chain: Chain) -> None:
    """
    Pair each creation with the id the coordinator gave it.

    Every task in a workspace came from a creation envelope, and the log
    preserves the order both were made in, so creations and ids pair up in
    order. With server state that pairing is exact, because state names every
    task that exists. Without it, ids are known only for tasks some later
    envelope referenced, and the pairing is an inference from order: sound for
    work that proceeds one task at a time, and capable of mismatching
    attributes across heavily interleaved tasks. Anything left unpaired is
    given an id marked ``unidentified`` rather than dropped, so a count is
    never quietly short.
    """
    placeholders = sorted((k for k in ix.tasks if k.startswith("__pending_")),
                          key=lambda k: int(k.split("_")[-1]))
    if not placeholders:
        for tid, t in ix.tasks.items():
            t.task_id = tid
        return

    if chain.has_state:
        state_tasks = (chain.state or {}).get("tasks") or {}
        real_ids = sorted(state_tasks,
                          key=lambda tid: (state_tasks[tid].get("created_at") or "", tid))
    else:
        # Only ids some later envelope named are knowable. Ordered by when
        # they were first seen, which tracks creation order in sequential work.
        first_seen: dict[str, int] = {}
        for e in ix.events:
            tid = e.get("task_id")
            if tid and tid not in first_seen:
                first_seen[tid] = e["seq"] or 0
        real_ids = sorted(first_seen, key=lambda tid: first_seen[tid])

    for i, placeholder in enumerate(placeholders):
        src = ix.tasks.pop(placeholder)
        real = real_ids[i] if i < len(real_ids) else f"(unidentified-{i})"
        dst = ix.tasks.get(real)
        if dst is None:
            src.task_id = real
            ix.tasks[real] = src
            continue
        for f in ("kind", "delegator", "original_assignee", "mode",
                  "review_required", "created_at", "criticality", "risk_tier",
                  "supersedes"):
            if getattr(dst, f) in (None, ""):
                setattr(dst, f, getattr(src, f))
        if dst.assignee is None:
            dst.assignee = src.original_assignee
        if dst.confidence is None:
            dst.confidence = src.confidence

    for tid, t in ix.tasks.items():
        t.task_id = tid


def _resolve_pending(registry: dict, pending: list, state_items: dict,
                     sort_key: str, id_attr: str, label: str) -> None:
    """Give an id to every creation the envelope stream never named again."""
    if not pending:
        return
    unobserved = sorted((k for k in state_items if k not in registry))
    for i, item in enumerate(pending):
        real = unobserved[i] if i < len(unobserved) else f"(unidentified-{label}-{i})"
        setattr(item, id_attr, real)
        registry.setdefault(real, item)


def _merge_state(ix: _Index, chain: Chain) -> None:
    """Overlay authoritative server state where the source carried it."""
    if not chain.has_state:
        return
    state = chain.state or {}

    for tid, task in (state.get("tasks") or {}).items():
        t = ix.tasks.get(tid)
        if t is None:
            t = ix.tasks[tid] = _Task(task_id=tid)
        # State is authoritative: it is what the coordinator believes.
        t.state = task.get("state", t.state)
        t.assignee = task.get("assignee", t.assignee)
        t.kind = task.get("kind", t.kind)
        t.delegator = task.get("delegator", t.delegator)
        t.mode = task.get("mode", t.mode)
        t.supersedes = task.get("supersedes", t.supersedes)
        if task.get("review_required") is not None:
            t.review_required = task["review_required"]
        t.created_at = _ts(task.get("created_at")) or t.created_at
        if t.confidence is None:
            t.confidence = _decimal(task.get("confidence"))
        review = task.get("review") or {}
        if review:
            t.requested_at = _ts(review.get("requested_at")) or t.requested_at
            t.rule = review.get("rule") or t.rule
            t.requested_to = list(review.get("requested_to") or t.requested_to)

    for ov in ix.overrides:
        stored = (state.get("overrides") or {})
        for art in stored.values():
            if art.get("task_id") == ov["task_id"] and art.get("reviewer") == ov["reviewer"]:
                ov["based_on"] = art.get("based_on_artefact", ov.get("based_on"))
                ov["result"] = art.get("result")
                break

    for did, d in (state.get("deliberations") or {}).items():
        entry = ix.delibs.get(did)
        if entry is None:
            entry = ix.delibs[did] = _Delib(deliberation_id=did)
        entry.task_id = d.get("task_id", entry.task_id)
        entry.rule = d.get("rule", entry.rule)
        entry.question = d.get("question", entry.question)
        entry.participants = list(d.get("participants") or entry.participants)
        outcome = d.get("outcome") or {}
        entry.outcome = outcome.get("outcome") if isinstance(outcome, dict) else None

    for uri, m in (state.get("members") or {}).items():
        rec = ix.members.setdefault(uri, {"participant": uri, "joined_at": None, "left_at": None})
        rec["kind"] = m.get("type", rec.get("kind"))
        rec["role"] = m.get("role", rec.get("role"))


# ---------------------------------------------------------------- projection

def _enforce(table: schema.Table, rows: list[dict]) -> pd.DataFrame:
    """Build a frame with exactly the declared columns, order and dtypes."""
    df = pd.DataFrame(rows, columns=table.names) if rows else pd.DataFrame(
        {c.name: pd.Series(dtype="object") for c in table.columns})
    for col in table.columns:
        if col.dtype == "list":
            # Empty list rather than null, so a caller can explode or count
            # without first checking for missingness.
            df[col.name] = df[col.name].apply(lambda v: v if isinstance(v, list) else [])
            continue
        if col.dtype == "object":
            # An arbitrary artefact. Left exactly as it arrived, including
            # None, which is what a redactor leaves behind.
            continue
        try:
            df[col.name] = df[col.name].astype(col.dtype)
        except (TypeError, ValueError):
            # A column the source could not populate: present and null, so
            # downstream code sees missingness rather than a KeyError.
            df[col.name] = pd.Series([pd.NA] * len(df), dtype=col.dtype)
    return df[list(table.names)]


@dataclass
class Frames:
    """
    The tables. Attribute access, or ``frames["decisions"]``.

    ``chain`` is kept so an analysis can say what it was computed from, which
    matters when a column is null because the source could not carry it rather
    than because nothing happened.
    """

    chain: Chain
    events: pd.DataFrame
    tasks: pd.DataFrame
    decisions: pd.DataFrame
    overrides: pd.DataFrame
    patch_ops: pd.DataFrame
    participants: pd.DataFrame
    deliberations: pd.DataFrame
    votes: pd.DataFrame
    whispers: pd.DataFrame
    routing: pd.DataFrame

    def __getitem__(self, name: str) -> pd.DataFrame:
        if name not in schema.BY_NAME:
            raise KeyError(f"{name!r} is not a CHAP table. Try one of: "
                           f"{', '.join(schema.BY_NAME)}")
        return getattr(self, name)

    def __repr__(self) -> str:
        sizes = ", ".join(f"{t.name}={len(self[t.name])}" for t in schema.TABLES)
        return f"<Frames {self.chain.workspace!r} {sizes}>"

    def summary(self) -> str:
        lines = [repr(self.chain), ""]
        width = max(len(t.name) for t in schema.TABLES)
        for t in schema.TABLES:
            lines.append(f"  {t.name:<{width}}  {len(self[t.name]):>6} rows   {t.grain}")
        if not self.chain.has_state:
            lines += ["", "  Read from envelopes alone, so columns whose provenance is",
                      "  'state' are null: deliberation and routing outcomes."]
        return "\n".join(lines)


def frames(chain: Chain) -> Frames:
    """Project a chain into every table."""
    ix = _replay(chain)
    ws = chain.workspace

    tasks_by_id = ix.tasks

    # -- decisions, with review context folded in ---------------------------
    decision_rows: list[dict] = []
    for tid, t in tasks_by_id.items():
        approvers: set[str] = set()
        settled = False
        for i, d in enumerate(sorted(t.decisions, key=lambda x: x["seq"] or 0)):
            if d["kind"] == "approve":
                approvers.add(d["reviewer"])
            final = False
            if not settled:
                if d["kind"] in ("override", "abstain"):
                    final = settled = True
                elif d["kind"] == "reject" and not d.get("request_revision"):
                    final = settled = True
                elif d["kind"] == "approve" and _rule_satisfied(
                        t.rule or "any_one_approves", approvers, len(t.requested_to)):
                    final = settled = True
            lat = None
            if d["ts"] is not None and t.requested_at is not None:
                lat = (d["ts"] - t.requested_at).total_seconds()
            decision_rows.append({
                "task_id": tid, "workspace": ws, "seq": d["seq"],
                "reviewer": d["reviewer"], "kind": d["kind"], "ts": d["ts"],
                "requested_at": t.requested_at, "latency_s": lat,
                "rule": t.rule, "decision_index": i, "is_final": final,
                "comment": d["comment"], "tags": d["tags"], "n_tags": len(d["tags"]),
                "request_revision": d["request_revision"],
                "abstain_category": d["abstain_category"],
                "digest_bound": d["digest_bound"],
                "task_kind": t.kind, "assignee": t.assignee,
            })

    # -- tasks --------------------------------------------------------------
    kinds_by_task = defaultdict(set)
    for r in decision_rows:
        kinds_by_task[r["task_id"]].add(r["kind"])

    task_rows: list[dict] = []
    for tid, t in tasks_by_id.items():
        kinds = kinds_by_task.get(tid, set())
        if "override" in kinds:
            outcome = "overridden"
        elif t.state == "completed" and kinds:
            outcome = "approved"
        elif t.state == "declined":
            outcome = "rejected"
        elif t.state == "abstained":
            outcome = "abstained"
        elif t.state in ("escalated", "cancelled", "superseded"):
            outcome = t.state
        elif t.state == "completed":
            outcome = "completed_without_review"
        else:
            outcome = "open"
        settled = t.state in _SETTLED
        lifetime = None
        if settled and t.created_at is not None and t.settled_at is not None:
            lifetime = (t.settled_at - t.created_at).total_seconds()
        task_rows.append({
            "task_id": tid, "workspace": ws, "kind": t.kind, "delegator": t.delegator,
            "assignee": t.assignee, "original_assignee": t.original_assignee,
            "mode": t.mode, "review_required": t.review_required, "state": t.state,
            "created_at": t.created_at, "settled_at": t.settled_at, "settled": settled,
            "lifetime_s": lifetime, "outcome": outcome,
            "was_reviewed": t.requested_at is not None or bool(kinds),
            "was_overridden": "override" in kinds,
            "n_decisions": len(t.decisions), "confidence": t.confidence,
            "criticality": t.criticality, "risk_tier": t.risk_tier,
            "supersedes": t.supersedes,
        })

    # -- overrides and their operations -------------------------------------
    override_rows: list[dict] = []
    op_rows: list[dict] = []
    for ov in ix.overrides:
        t = tasks_by_id.get(ov["task_id"]) or _Task(task_id=ov["task_id"])
        diff = ov.get("diff") or []
        paths = [op.get("path") for op in diff if isinstance(op, dict)]
        tops = [_top_path(p) for p in paths]
        override_rows.append({
            "task_id": ov["task_id"], "workspace": ws, "seq": ov["seq"],
            "reviewer": ov["reviewer"], "ts": ov["ts"], "rationale": ov["rationale"],
            "tags": ov["tags"], "policy_refs": ov["policy_refs"],
            "intent_preserved": ov["intent_preserved"], "logical_id": ov["logical_id"],
            "n_ops": len(diff),
            "op_kinds": sorted({op.get("op") for op in diff if isinstance(op, dict) and op.get("op")}),
            "paths": paths, "top_path": tops[0] if tops else None,
            "based_on": ov.get("based_on"), "result": ov.get("result"),
            "task_kind": t.kind, "assignee": t.assignee, "confidence": t.confidence,
        })
        for i, op in enumerate(diff):
            if not isinstance(op, dict):
                continue
            path = op.get("path")
            op_rows.append({
                "task_id": ov["task_id"], "workspace": ws, "seq": ov["seq"],
                "reviewer": ov["reviewer"], "op_index": i, "op": op.get("op"),
                "path": path, "top_path": _top_path(path),
                "depth": len([s for s in str(path or "").split("/") if s != ""]),
            })

    # -- participants -------------------------------------------------------
    assigned = defaultdict(int)
    for t in tasks_by_id.values():
        if t.assignee:
            assigned[t.assignee] += 1
    decided: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in decision_rows:
        decided[r["reviewer"]]["all"] += 1
        decided[r["reviewer"]][r["kind"]] += 1

    participant_rows = [{
        "participant": uri, "workspace": ws, "kind": m.get("kind"), "role": m.get("role"),
        "joined_at": m.get("joined_at"), "left_at": m.get("left_at"),
        "n_tasks_assigned": assigned.get(uri, 0),
        "n_decisions": decided.get(uri, {}).get("all", 0),
        "n_overrides": decided.get(uri, {}).get("override", 0),
        "n_abstentions": decided.get(uri, {}).get("abstain", 0),
    } for uri, m in ix.members.items()]

    # -- deliberations and votes -------------------------------------------
    delib_rows: list[dict] = []
    vote_rows: list[dict] = []
    for did, d in ix.delibs.items():
        tally = defaultdict(int)
        for v in d.votes:
            tally[v["vote"]] += 1
            vote_rows.append({
                "deliberation_id": did, "workspace": ws, "seq": v["seq"],
                "voter": v["voter"], "vote": v["vote"], "ts": v["ts"],
                "comment": v["comment"], "veto_invoked": v["veto_invoked"],
            })
        n_part = len(d.participants)
        delib_rows.append({
            "deliberation_id": did, "workspace": ws, "task_id": d.task_id,
            "opener": d.opener, "rule": d.rule, "question": d.question,
            "opened_at": d.opened_at, "closed_at": d.closed_at,
            "n_participants": n_part, "n_votes": len(d.votes),
            "n_yea": tally["yea"], "n_nay": tally["nay"], "n_abstain": tally["abstain"],
            "turnout": (len(d.votes) / n_part) if n_part else None,
            "outcome": getattr(d, "outcome", None),
        })

    # -- whispers -----------------------------------------------------------
    whisper_rows = []
    for wid, w in ix.whispers.items():
        resp = None
        if w.answered_at is not None and w.asked_at is not None:
            resp = (w.answered_at - w.asked_at).total_seconds()
        whisper_rows.append({
            "whisper_id": wid, "workspace": ws, "task_id": w.task_id, "asker": w.asker,
            "question": w.question, "asked_at": w.asked_at, "deadline_ms": w.deadline_ms,
            "answered_at": w.answered_at, "answered_by": w.answered_by, "answer": w.answer,
            "answered": w.answered_at is not None, "response_s": resp,
            "had_options": w.had_options,
        })

    return Frames(
        chain=chain,
        events=_enforce(schema.EVENTS, ix.events),
        tasks=_enforce(schema.TASKS, task_rows),
        decisions=_enforce(schema.DECISIONS, decision_rows),
        overrides=_enforce(schema.OVERRIDES, override_rows),
        patch_ops=_enforce(schema.PATCH_OPS, op_rows),
        participants=_enforce(schema.PARTICIPANTS, participant_rows),
        deliberations=_enforce(schema.DELIBERATIONS, delib_rows),
        votes=_enforce(schema.VOTES, vote_rows),
        whispers=_enforce(schema.WHISPERS, whisper_rows),
        routing=_enforce(schema.ROUTING, ix.routing),
    )
