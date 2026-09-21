"""
Projecting a chain into tables.

The chain is replayed exactly once into an index of what happened, and every
table is then a view over that index. Replaying the envelopes is what lets the
whole layer work against a plain ``audit.read``, which is what an MCP client
can obtain.

Three rules hold throughout.

Every table has exactly the columns ``schema.py`` declares, in that order,
with those dtypes. A column the source lacked a value for is present and null,
so downstream code can reference any column whatever the chain came from.

Every value is recorded or computed, and the schema says which. Where a
computation rests on an assumption the chain fails to support, the column is
left null.

Where the replay is an inference rather than a reading, the table says so in a
column. Server-minted identifiers are the case that matters: they are returned
in the *result* and the log records envelopes, so pairing a creation to its id
is sometimes forced by the ordering and sometimes a guess. ``id_certain``
distinguishes the two.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from . import schema
from ._patch import PatchError, apply_json_patch
from .load import Chain

__all__ = ["frames", "Frames"]

_TERMINAL = {"completed", "cancelled", "superseded"}
_SETTLED = _TERMINAL | {"declined", "abstained", "escalated"}

#: URI schemes that address a set rather than a person. A rule waits on the
#: reviewers it can name; a set is addressed anonymously.
_BROADCAST = ("workspace:", "group:")


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


def _as_list(value: Any) -> list:
    """
    A list from a field that should hold one.

    A single string is a list of one, which is how ``to`` is often sent. A
    sequence is itself. Anything else is malformed and yields an empty list,
    which keeps fabricated participants out of the tables.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


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


def _apply_patch(artefact: Any, diff: list) -> Any:
    """
    The corrected artefact: the patch applied to what the reviewer saw.

    None where the patch fails to apply. A redactor leaves the artefact as
    None. A patch the coordinator accepted applies to the artefact the
    coordinator held, so a failure here means the reconstructed base differs
    from that artefact, and a result computed from it would be wrong.
    """
    if artefact is None or not diff:
        return None
    try:
        return apply_json_patch(artefact, diff)
    except PatchError:
        return None


def _named_reviewers(requested_to: list[str]) -> list[str]:
    """The reviewers a rule can wait on, which excludes broadcast addresses."""
    return [r for r in requested_to
            if isinstance(r, str) and not r.startswith(_BROADCAST)]


def _rule_satisfied(rule: str | None, approvers: set[str], requested_to: list[str]) -> bool:
    """
    Mirrors the coordinator's review-satisfied predicate, so ``is_final`` means
    what it says.

    ``all_approve`` waits on the reviewers it can name. A review addressed to a
    group or to the workspace has no bounded set to wait on, so the coordinator
    treats it as first-approve.
    """
    rule = rule or "any_one_approves"
    if rule == "all_approve":
        named = _named_reviewers(requested_to)
        if not named:
            return len(approvers) >= 1
        return all(r in approvers for r in named)
    if rule.startswith("quorum:"):
        try:
            return len(approvers) >= int(rule.split(":", 1)[1])
        except ValueError:
            return False
    return len(approvers) >= 1


def _forced_prefix(creations: list[int], sightings: list[int]) -> list[bool]:
    """
    Which of these pairings the log forces, one flag per pairing, in order.

    An id is minted server-side and returned in the result, so it becomes
    visible when a later envelope names it. Pairing the *i*-th creation with
    the *i*-th id to appear is therefore an inference. It is forced when one
    unclaimed creation alone could have produced that id: the creation
    precedes the sighting, and the next creation follows it.

    The property is inductive from the front. While every earlier pairing was
    forced, the earliest unclaimed creation is the only candidate for the next
    id; once one pairing is open to two candidates, everything after it is too.
    So the flags run true until the first break and false thereafter, and they
    are computed together.

    Two tasks created before either is worked on, or a successor minted between
    two creations, are the ordinary ways this breaks, and ``id_certain`` is
    where a row says which case it is in.
    """
    out: list[bool] = []
    forced = len(sightings) <= len(creations)
    for i, seen in enumerate(sightings):
        if forced:
            if seen is None or creations[i] is None or creations[i] >= seen:
                forced = False
            elif i + 1 < len(creations) and creations[i + 1] < seen:
                # A second creation got in before this id was named, so either
                # of them could own it. Strictly before: the id an envelope
                # mints goes back in the result, so escalate.raise naming the
                # task it supersedes leaves its own successor out of the running.
                forced = False
        out.append(forced)
    return out


def _identify(pending: list, admissible) -> tuple[int, bool | None]:
    """
    Which outstanding creation an envelope is about, where the protocol says.

    The id is in the result of the creating call, so the usual answer is "the
    oldest unclaimed one". Often the protocol knows better. A coordinator
    accepts a vote from an invited participant, an acceptance from the named
    recipient, and declares a lapse once the deadline has passed. An envelope
    that was accepted therefore rules out every outstanding candidate the
    coordinator would have refused it for, and where one candidate is left the
    pairing is a reading.

    Returns the index to take and how far the protocol settled it: ``True``
    settled, ``False`` narrowed the field, ``None`` left it to the ordering.
    """
    if not pending:
        return 0, None
    allowed = [i for i, item in enumerate(pending) if admissible(item)]
    if len(allowed) == 1:
        return allowed[0], True
    if allowed:
        return allowed[0], False
    return 0, None


def _deadline_passed(w: _Whisper, arrived: pd.Timestamp | None) -> bool:
    """
    Whether this whisper's deadline had expired when a lapse notice arrived.

    Measured on the coordinator's clock at both ends, as the coordinator
    measures it: the notice is written by the coordinator, and a client that
    stamps its own ``ts`` into the ask is on a different clock.
    """
    if arrived is None or w.arrived_at is None:
        return False
    ms = _decimal(w.deadline_ms)
    if ms is None:
        return False
    return arrived >= w.arrived_at + pd.Timedelta(milliseconds=ms)


def _may_answer(w: _Whisper, actor: str | None) -> bool:
    """Whether the coordinator would accept an answer to this whisper from ``actor``."""
    if any(isinstance(a, str) and a.startswith(_BROADCAST) for a in w.askee):
        return True
    return actor in w.askee


# ---------------------------------------------------------------- the index

@dataclass
class _Review:
    """
    One review pass over a task.

    A task can be reviewed more than once. A rejection that asks for a revision
    returns it to ``in_progress``, and a later ``review.request`` replaces the
    review outright: the coordinator starts a fresh one with an empty decision
    list. Holding the decisions on the pass keeps each quorum about one
    artefact.
    """

    requested_at: pd.Timestamp | None = None
    requested_to: list[str] = field(default_factory=list)
    rule: str | None = None
    artefact: Any = None
    decisions: list[dict] = field(default_factory=list)
    settled_at: pd.Timestamp | None = None


#: What a task is given when it is made, rather than what happens to it
#: afterwards. Every one of these is fixed at creation, which is what lets a
#: creation be re-attached to a different task once the pairing is settled.
_CREATION_FIELDS = ("kind", "delegator", "original_assignee", "mode",
                    "review_required", "created_at", "criticality",
                    "risk_tier", "supersedes")


@dataclass
class _Creation:
    """
    What a creation envelope said, before it is known which task it made.

    Held apart from the task and attributed as the replay goes, because the
    replay reads it as it goes. Whether completing a task opens a review
    depends on ``review_required``, which is set at creation and named there
    alone. So the attribution has to be made before the completion is replayed.
    """

    pos: int
    kind: str | None = None
    delegator: str | None = None
    original_assignee: str | None = None
    assignee: str | None = None
    mode: str | None = None
    review_required: bool | None = None
    created_at: pd.Timestamp | None = None
    confidence: float | None = None
    criticality: str | None = None
    risk_tier: str | None = None
    supersedes: str | None = None
    #: Attributes a successor took from the task it replaces rather than from
    #: its own spec. What was inherited is only as certain as its source.
    inherited: frozenset[str] = frozenset()


@dataclass
class _Task:
    task_id: str
    kind: str | None = None
    delegator: str | None = None
    original_assignee: str | None = None
    assignee: str | None = None
    assignee_certain: bool = True
    id_certain: bool = True
    mode: str | None = None
    review_required: bool | None = None
    created_at: pd.Timestamp | None = None
    state: str = "created"
    fulfils: str | None = None
    paused_from: str | None = None
    settled_at: pd.Timestamp | None = None
    confidence: float | None = None
    criticality: str | None = None
    risk_tier: str | None = None
    supersedes: str | None = None
    #: Set once the replay has seen the assignee move or a confidence reported,
    #: so re-attributing a creation leaves what happened afterwards in place.
    assignee_moved: bool = False
    confidence_reported: bool = False
    reviews: list[_Review] = field(default_factory=list)

    def attribute(self, made: _Creation | None) -> None:
        """Take this creation's attributes as this task's own."""
        for f in _CREATION_FIELDS:
            setattr(self, f, getattr(made, f, None))
        if not self.assignee_moved:
            self.assignee = getattr(made, "assignee", None)
        if not self.confidence_reported:
            self.confidence = getattr(made, "confidence", None)

    @property
    def review(self) -> _Review | None:
        """The pass a decision arriving now would belong to."""
        return self.reviews[-1] if self.reviews else None

    @property
    def decisions(self) -> list[dict]:
        """Every decision on the task, across all passes."""
        return [d for r in self.reviews for d in r.decisions]

    def open_review(self, ts: pd.Timestamp | None, to: list[str],
                    rule: str | None, artefact: Any) -> _Review:
        r = _Review(requested_at=ts, requested_to=list(to), rule=rule, artefact=artefact)
        self.reviews.append(r)
        return r


@dataclass
class _Handoff:
    handoff_id: str
    proposer: str | None = None
    recipient: str | None = None
    task_ids: list[str] = field(default_factory=list)
    proposed_at: pd.Timestamp | None = None
    resolved_at: pd.Timestamp | None = None
    resolution: str = "open"
    resolved_by: str | None = None
    accepted: list[str] = field(default_factory=list)
    reason: str | None = None
    seq: int | None = None
    pos: int | None = None
    seen_pos: int | None = None
    id_certain: bool = True
    #: Set where the protocol identified the id, in which case it decides.
    #: None leaves the decision to the ordering check.
    matched_uniquely: bool | None = None


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
    outcome: str | None = None
    pos: int | None = None
    seen_pos: int | None = None
    id_certain: bool = True
    #: Set where the protocol identified the id, in which case it decides.
    #: None leaves the decision to the ordering check.
    matched_uniquely: bool | None = None


@dataclass
class _Whisper:
    whisper_id: str
    task_id: str | None = None
    asker: str | None = None
    question: str | None = None
    asked_at: pd.Timestamp | None = None
    #: When the coordinator accepted the ask, on its own clock.
    arrived_at: pd.Timestamp | None = None
    deadline_ms: int | None = None
    askee: list[str] = field(default_factory=list)
    had_options: bool = False
    answered_at: pd.Timestamp | None = None
    answered_by: str | None = None
    answer: str | None = None
    #: Set when the coordinator announced the lapse, which it does by writing a
    #: notify.message into the log. Authoritative where present.
    lapsed_at: pd.Timestamp | None = None
    state: str | None = None
    pos: int | None = None
    seen_pos: int | None = None
    id_certain: bool = True
    #: Set where the protocol identified the id, in which case it decides.
    #: None leaves the decision to the ordering check.
    matched_uniquely: bool | None = None


@dataclass
class _Index:
    tasks: dict[str, _Task] = field(default_factory=dict)
    delibs: dict[str, _Delib] = field(default_factory=dict)
    whispers: dict[str, _Whisper] = field(default_factory=dict)
    members: dict[str, dict] = field(default_factory=dict)
    handoffs: dict[str, _Handoff] = field(default_factory=dict)
    overrides: list[dict] = field(default_factory=list)
    routing: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    #: Task ids in the order the replay first saw them, wherever they appeared.
    #: Derived from the events table instead, this missed every id that
    #: arrives nested, as handoff.propose's do.
    task_order: list[str] = field(default_factory=list)
    #: Where each id was first seen, as a log position. Positions rather than
    #: seq numbers: two exports concatenated repeat their seq numbers, and a
    #: position is always present and always increasing.
    first_seen: dict[str, int] = field(default_factory=dict)
    #: Every creation envelope, in order, and the id each was attributed to as
    #: the replay went. Kept so the attribution can be revisited once server
    #: state has been consulted.
    creations: list[_Creation] = field(default_factory=list)
    claimed_by: list[str | None] = field(default_factory=list)


def _replay(chain: Chain) -> _Index:  # noqa: C901 - one pass, one branch per method
    ix = _Index()
    # Deliberation, whisper and handoff ids are minted by the coordinator and
    # returned in the result. They are recoverable because every later envelope
    # names the id it acts on, so an opening envelope is matched to the first
    # id seen for it. `all_*` keeps the openings in order for the pairing check
    # afterwards; `pending_*` is what is still unmatched.
    all_delib: list[_Delib] = []
    all_whisper: list[_Whisper] = []
    all_handoff: list[_Handoff] = []
    pending_delib: list[_Delib] = []
    pending_whisper: list[_Whisper] = []
    pending_handoff: list[_Handoff] = []
    #: What was matched, in the order it was matched, and whether every match
    #: came off the front of the queue. A match made on evidence rather than on
    #: order leaves the queue out of step, and the ordering check then
    #: describes a different sequence from the one that happened.
    done: dict[str, list] = {"delib": [], "whisper": [], "handoff": []}
    in_order: dict[str, bool] = {"delib": True, "whisper": True, "handoff": True}
    # The workspace defaults a creation inherits. Both are in the log, on
    # workspace.create, and both change what a task is: a task.create that
    # names no mode takes the workspace's, and under modes/1.0 a trial task
    # requires review whatever the caller asked for.
    ws_mode = "trial"
    ws_profiles: list[str] = []

    def review_rule(spec: dict, mode: str | None) -> bool | None:
        """
        Whether a task made from this spec requires review, as the coordinator
        decides it for task.create and control.supersede alike: a trial task
        under modes/1.0 does regardless, otherwise the spec's own flag, and
        otherwise the coordinator leaves it unset.
        """
        if mode == "trial" and any(x == "modes" or x.startswith("modes/") for x in ws_profiles):
            return True
        if "review_required" in spec:
            return bool(spec["review_required"])
        return None

    def task(tid: str | None, pos: int | None = None) -> _Task | None:
        """
        The task this id names, attributing a creation to it on first sight.

        An id becomes visible only when an envelope names it, and the earliest
        unclaimed creation is the one that most likely made it. That
        attribution is provisional: server state settles it properly later, and
        where the evidence leaves it open the row says so. It is made here
        because the replay reads it as it goes.
        """
        if not tid:
            return None
        if tid not in ix.tasks:
            t = ix.tasks[tid] = _Task(task_id=tid)
            ix.task_order.append(tid)
            if pos is not None:
                ix.first_seen[tid] = pos
            for i, owner in enumerate(ix.claimed_by):
                if owner is None:
                    ix.claimed_by[i] = tid
                    t.attribute(ix.creations[i])
                    break
        return ix.tasks[tid]

    def created(made: _Creation) -> None:
        ix.creations.append(made)
        ix.claimed_by.append(None)

    for pos, entry in enumerate(chain.events):
        if not isinstance(entry, dict):
            # A stray element in a hand-assembled export. Skipped, so the
            # entries after it still reach the analysis.
            continue
        env = entry.get("envelope")
        env = env if isinstance(env, dict) else {}
        p = env.get("params")
        p = p if isinstance(p, dict) else {}
        method = env.get("method") or ""
        actor = p.get("from") or env.get("from")
        # The sender's timestamp lives in params in every profile example and
        # is read from there first by the coordinator; the envelope-level ts is
        # the older placement. Whichever is set is the sender's clock, and the
        # coordinator's own clock is the fallback.
        arrived = _ts(entry.get("arrived"))
        ts = _ts(p.get("ts")) or _ts(env.get("ts")) or arrived
        seq = entry.get("seq") if isinstance(entry.get("seq"), int) else None
        # escalate.raise names its subject original_task_id rather than
        # task_id. The event is still about that task, and treating it
        # otherwise leaves the task out of the id ordering.
        tid = p.get("task_id") or p.get("original_task_id")
        tid = tid if isinstance(tid, str) else None

        if tid:
            task(tid, pos)
        ix.events.append({
            "seq": seq,
            "workspace": p.get("workspace") or chain.workspace,
            "ts": ts,
            "arrived": arrived,
            "method": method,
            "actor": actor,
            "actor_kind": _uri_kind(actor),
            "task_id": tid,
            "prev_hash": entry.get("prev_hash"),
            "chained": entry.get("prev_hash") is not None,
            "signed": isinstance(env.get("sig"), str),
            "scitt_submitted": False,
        })

        if method == "audit.submit_to_scitt":
            # A recorded submission covered a range of positions. The call is on
            # the chain only where the coordinator accepted it, so the range is
            # what was sent to the transparency service.
            rng = p.get("range") if isinstance(p.get("range"), dict) else {}
            lo = rng.get("from_seq", 0)
            hi = rng.get("to_seq", pos)
            if isinstance(lo, int) and isinstance(hi, int):
                for row in ix.events:
                    s = row["seq"]
                    if isinstance(s, int) and lo <= s < hi:
                        row["scitt_submitted"] = True

        if method in ("workspace.create", "workspace.set_profiles"):
            if p.get("profiles") is not None:
                ws_profiles = [x for x in _as_list(p.get("profiles")) if isinstance(x, str)]
            if p.get("mode"):
                ws_mode = p["mode"]

        elif method == "participant.join":
            # A second join by a current member leaves the coordinator's record
            # of them as it was. A member who left and joins again is new.
            current = ix.members.get(actor)
            if current is None or current.get("left_at") is not None:
                ix.members[actor] = {
                    "participant": actor, "kind": p.get("type"), "role": p.get("role"),
                    "joined_at": ts, "left_at": None,
                }

        elif method == "participant.leave":
            if actor in ix.members:
                ix.members[actor]["left_at"] = ts

        elif method == "task.create":
            # The id is in the result. Tasks are matched by the order they were
            # created, which the log preserves.
            hints = p.get("routing_hints") if isinstance(p.get("routing_hints"), dict) else {}
            mode = p.get("mode") or ws_mode
            assignee = p.get("assignee") or p.get("to")
            created(_Creation(
                pos=pos, kind=p.get("kind"), delegator=actor,
                original_assignee=assignee, assignee=assignee,
                mode=mode, review_required=review_rule(p, mode), created_at=ts,
                criticality=hints.get("criticality"), risk_tier=hints.get("risk_tier"),
                confidence=_decimal(hints.get("confidence")),
            ))

        elif method == "task.update" and tid:
            t = task(tid)
            new_state = p.get("state")
            if new_state:
                t.state = new_state
                t.settled_at = ts if new_state in _SETTLED else None

        elif method == "task.complete" and tid:
            t = task(tid)
            out = p.get("output")
            if isinstance(out, dict) and isinstance(out.get("fulfils"), str):
                t.fulfils = out["fulfils"]
            conf = _decimal(p.get("confidence"))
            if conf is None:
                hints = p.get("routing_hints") if isinstance(p.get("routing_hints"), dict) else {}
                conf = _decimal(hints.get("confidence"))
            if conf is not None:
                t.confidence, t.confidence_reported = conf, True
            if t.review_required:
                # review/1.0 S3.1: completing a task that requires review opens
                # a review. The coordinator reuses an existing review rather
                # than replacing it, so decisions already cast stay in force;
                # review.request is what starts a fresh pass.
                r = t.review
                if r is None:
                    r = t.open_review(ts, _implicit_reviewers(ix, actor, t.assignee),
                                      "any_one_approves", None)
                r.artefact = p.get("output")
                t.state, t.settled_at = "review_requested", None
            else:
                t.state = "completed"
                t.settled_at = ts

        elif method == "review.request" and tid:
            t = task(tid)
            addressed = _as_list(p.get("to"))
            r = t.review
            if t.state == "review_requested" and r is not None:
                # A review already open: this widens the reviewer set. The
                # coordinator refuses a request that changes the artefact or
                # the rule underneath an open review, so a widen is the only
                # thing this can be, and the decisions already cast stand.
                for reviewer in addressed:
                    if reviewer not in r.requested_to:
                        r.requested_to.append(reviewer)
                if p.get("artefact") is not None:
                    r.artefact = p["artefact"]
            else:
                # A new pass. Its decisions start empty, which is the whole
                # reason passes are tracked separately.
                r = t.open_review(ts, addressed, p.get("rule") or "any_one_approves",
                                  p.get("artefact"))
            t.state, t.settled_at = "review_requested", None

        elif method in ("decide.approve", "decide.reject", "decide.override",
                        "abstain.declare") and tid:
            t = task(tid)
            r = t.review
            if r is None:
                # The coordinator accepts a decision on a task under review, so
                # a review is expected here. Recording the decision against an
                # implicit pass keeps it in the table if one is ever missing.
                r = t.open_review(ts, [], "any_one_approves", None)
            kind = {"decide.approve": "approve", "decide.reject": "reject",
                    "decide.override": "override", "abstain.declare": "abstain"}[method]
            d = {
                "seq": seq, "reviewer": actor, "kind": kind, "ts": ts,
                "comment": p.get("comment") or p.get("reason"),
                "tags": _as_list(p.get("tags")),
                "request_revision": bool(p.get("request_revision")) if kind == "reject" else None,
                "abstain_category": p.get("category") if kind == "abstain" else None,
                "digest_bound": p.get("approved_artefact_digest") is not None,
                "is_final": False,
            }
            r.decisions.append(d)

            if kind == "override":
                diff = p.get("diff")
                diff = diff if isinstance(diff, list) else []
                ix.overrides.append({
                    "seq": seq, "task_id": tid, "reviewer": actor, "ts": ts,
                    "rationale": p.get("rationale"), "tags": _as_list(p.get("tags")),
                    "policy_refs": _as_list(p.get("policy_refs")),
                    "intent_preserved": p.get("intent_preserved"),
                    "logical_id": p.get("logical_id"),
                    "diff": diff, "based_on": r.artefact,
                    "result": _apply_patch(r.artefact, diff),
                })
                d["is_final"] = True
                t.state, t.settled_at, r.settled_at = "completed", ts, ts
            elif kind == "abstain":
                d["is_final"] = True
                t.state, t.settled_at, r.settled_at = "abstained", ts, ts
            elif kind == "reject":
                if p.get("request_revision"):
                    # Sent back: the review stays open.
                    t.state, t.settled_at = "in_progress", None
                else:
                    d["is_final"] = True
                    t.state, t.settled_at, r.settled_at = "declined", ts, ts
            else:
                approvers = {x["reviewer"] for x in r.decisions if x["kind"] == "approve"}
                if _rule_satisfied(r.rule, approvers, r.requested_to):
                    d["is_final"] = True
                    t.state, t.settled_at, r.settled_at = "completed", ts, ts

        elif method == "escalate.raise":
            orig = p.get("original_task_id")
            old = task(orig) if orig else None
            if old is not None:
                old.state, old.settled_at = "escalated", ts
            spec = p.get("new_task") if isinstance(p.get("new_task"), dict) else {}
            # The successor inherits the original's kind where the spec names
            # none, and its mode always. The coordinator leaves review_required
            # at its default here.
            inherited = {"mode"} | ({"kind"} if not spec.get("kind") else set())
            created(_Creation(
                pos=pos, kind=spec.get("kind") or (old.kind if old else None),
                delegator=actor, original_assignee=spec.get("assignee"),
                assignee=spec.get("assignee"), mode=old.mode if old else None,
                created_at=ts, supersedes=orig, inherited=frozenset(inherited)))

        elif method == "control.cancel" and tid:
            t = task(tid)
            t.state, t.settled_at = "cancelled", ts

        elif method == "control.supersede" and tid:
            old = task(tid)
            old.state, old.settled_at = "superseded", ts
            spec = p.get("successor_task") if isinstance(p.get("successor_task"), dict) else {}
            # A successor is a created task and is bound as one: it takes the
            # original's assignee and mode where the spec names none, and the
            # same review rule task.create applies.
            assignee = spec.get("assignee") or old.assignee
            mode = spec.get("mode") or old.mode or ws_mode
            inherited = ({"assignee"} if not spec.get("assignee") else set()) \
                | ({"mode"} if not spec.get("mode") else set())
            created(_Creation(
                pos=pos, kind=spec.get("kind"), delegator=actor,
                original_assignee=assignee, assignee=assignee, mode=mode,
                review_required=review_rule(spec, mode), created_at=ts, supersedes=tid,
                inherited=frozenset(inherited)))

        elif method == "control.pause" and tid and p.get("scope", "task") == "task":
            t = task(tid)
            if t.state != "paused":
                t.paused_from = t.state
            t.state = "paused"

        elif method == "control.resume" and tid and p.get("scope", "task") == "task":
            t = task(tid)
            t.state = t.paused_from or "in_progress"
            t.paused_from = None

        elif method == "deliberate.open":
            d_open = _Delib(
                deliberation_id="", task_id=tid, opener=actor, rule=p.get("rule"),
                question=p.get("question"), opened_at=ts, pos=pos,
                participants=_as_list(p.get("to") or p.get("participants")),
            )
            # A caller may name the id itself, and the coordinator honours it.
            # The opening is then identified by its own envelope, and the
            # ordering check skips it.
            given = p.get("deliberation_id")
            if isinstance(given, str) and given and given not in ix.delibs:
                d_open.deliberation_id, d_open.matched_uniquely = given, True
                ix.delibs[given] = d_open
            else:
                all_delib.append(d_open)
                pending_delib.append(d_open)

        elif method in ("deliberate.vote", "deliberate.comment", "deliberate.close"):
            did = p.get("deliberation_id")
            if did and did not in ix.delibs and pending_delib:
                # A vote is accepted from an invited participant, so an accepted
                # vote names a deliberation the voter belongs to.
                idx, sure = (_identify(pending_delib, lambda d: actor in d.participants)
                             if method == "deliberate.vote" else (0, None))
                d_open = pending_delib.pop(idx)
                d_open.deliberation_id = did
                d_open.seen_pos = pos
                d_open.matched_uniquely = sure
                in_order["delib"] = in_order["delib"] and idx == 0
                ix.delibs[did] = d_open
                done["delib"].append(d_open)
            d_open = ix.delibs.get(did)
            if d_open is None:
                continue
            if method == "deliberate.vote":
                d_open.votes.append({
                    "seq": seq, "voter": actor, "vote": p.get("vote"), "ts": ts,
                    "comment": p.get("comment"),
                    "veto_invoked": bool(p.get("veto_invoked")),
                })
            elif method == "deliberate.close" and d_open.closed_at is None:
                # The coordinator records a second close and leaves the
                # deliberation as it was, so the first close is the one that
                # closed it.
                d_open.closed_at = ts

        elif method == "whisper.ask":
            w = _Whisper(
                whisper_id="", task_id=tid, asker=actor, question=p.get("question"),
                asked_at=ts, arrived_at=arrived or ts, deadline_ms=p.get("deadline_ms"),
                pos=pos, askee=_as_list(p.get("to")), had_options=bool(p.get("options")),
            )
            given = p.get("whisper_id")
            if isinstance(given, str) and given and given not in ix.whispers:
                w.whisper_id, w.matched_uniquely = given, True
                ix.whispers[given] = w
            else:
                all_whisper.append(w)
                pending_whisper.append(w)

        elif method in ("whisper.answer", "notify.message"):
            # notify.message is how the coordinator announces a lapse: it
            # writes one into the log naming the whisper and the default it
            # applied. It is the record of a lapse in the envelope stream, and
            # reading it is what separates "still awaiting an answer" from
            # "the deadline passed and the default stood".
            if method == "notify.message" and p.get("kind") != "whisper_lapsed":
                continue
            wid = p.get("whisper_id")
            if wid and wid not in ix.whispers and pending_whisper:
                # A lapse concerns a whisper whose deadline had passed. An
                # answer comes from someone the whisper was addressed to, or
                # from any member where it was addressed to a group or the
                # workspace.
                if method == "notify.message":
                    when = arrived or ts
                    idx, sure = _identify(pending_whisper, lambda w: _deadline_passed(w, when))
                else:
                    idx, sure = _identify(pending_whisper, lambda w: _may_answer(w, actor))
                w = pending_whisper.pop(idx)
                w.whisper_id = wid
                w.seen_pos = pos
                w.matched_uniquely = sure
                in_order["whisper"] = in_order["whisper"] and idx == 0
                ix.whispers[wid] = w
                done["whisper"].append(w)
            w = ix.whispers.get(wid)
            if w is None:
                continue
            if method == "notify.message":
                w.lapsed_at, w.state = ts, "lapsed"
            else:
                w.answered_at = ts
                w.answered_by = actor
                # The coordinator reads the free text from either name.
                w.answer = p.get("answer_option") or p.get("answer") or p.get("answer_text")
                if w.state != "lapsed":
                    w.state = "answered"

        elif method == "handoff.propose":
            # The tasks are named inside a nested list, and each is a sighting
            # of an id like any other.
            ids = [x.get("task_id") for x in _as_list(p.get("tasks"))
                   if isinstance(x, dict) and isinstance(x.get("task_id"), str)]
            for t_id in ids:
                task(t_id, pos)
            h = _Handoff(handoff_id="", proposer=actor, recipient=p.get("to"),
                         task_ids=ids, proposed_at=ts, seq=seq, pos=pos)
            given = p.get("handoff_id")
            if isinstance(given, str) and given and given not in ix.handoffs:
                h.handoff_id, h.matched_uniquely = given, True
                ix.handoffs[given] = h
            else:
                all_handoff.append(h)
                pending_handoff.append(h)

        elif method in ("handoff.accept", "handoff.decline"):
            hid = p.get("handoff_id")
            if hid and hid not in ix.handoffs and pending_handoff:
                # The named recipient is the one who may accept a direct offer,
                # so an acceptance the coordinator recorded names an offer made
                # to the acceptor or to a group. Anyone may record a decline,
                # so a decline leaves the field as it was.
                idx, sure = (_identify(
                    pending_handoff,
                    lambda h: str(h.recipient or "").startswith("group:")
                    or h.recipient == actor)
                    if method == "handoff.accept" else (0, None))
                h = pending_handoff.pop(idx)
                h.handoff_id = hid
                h.seen_pos = pos
                h.matched_uniquely = sure
                in_order["handoff"] = in_order["handoff"] and idx == 0
                ix.handoffs[hid] = h
                done["handoff"].append(h)
            h = ix.handoffs.get(hid)
            if h is None:
                continue
            if method == "handoff.decline":
                h.reason = p.get("reason")
                # A group recipient is an offer to whoever is free. One member
                # declining leaves it open for the rest, and the coordinator
                # keeps it proposed; only the named recipient can turn a
                # handoff down outright.
                recipient = h.recipient if isinstance(h.recipient, str) else ""
                if not recipient.startswith("group:") and actor == recipient:
                    h.resolution, h.resolved_at, h.resolved_by = "declined", ts, actor
            else:
                h.resolution, h.resolved_at, h.resolved_by = "accepted", ts, actor
                taken = _as_list(p.get("accepted_task_ids")) or h.task_ids
                h.accepted = list(taken)
                # Accepting is the moment responsibility moves.
                for t_id in taken:
                    moved = task(t_id, pos)
                    if moved is not None:
                        moved.assignee, moved.assignee_moved = actor, True

        elif method in ("task.route", "review.depth", "escalate.auto"):
            cands = _as_list(p.get("candidates"))
            ix.routing.append({
                "seq": seq, "task_id": tid, "method": method, "ts": ts,
                "workspace": p.get("workspace") or chain.workspace,
                "candidates": cands, "n_candidates": len(cands),
            })
            if method == "task.route" and tid:
                # The chosen candidate is returned in the result. The assignee
                # is left as it was and flagged as uncertain.
                task(tid).assignee_certain = False

    _resolve(ix, chain, all_delib, all_whisper, all_handoff,
             pending_delib, pending_whisper, pending_handoff, done, in_order)
    _merge_state(ix, chain)
    return ix


def _implicit_reviewers(ix: _Index, producer: str | None, assignee: str | None) -> list[str]:
    """
    The reviewers a completion opens its review to.

    review/1.0 S3.1: a person other than the producer has to satisfy the
    review, so the coordinator addresses the implicit review to the current
    human members other than the completer and the assignee. An implicit
    review is always ``any_one_approves``, and the addressee list reaches no
    table; it is reconstructed rather than read so the pass is the one the
    coordinator holds.
    """
    return [uri for uri, m in ix.members.items()
            if uri != producer and uri != assignee
            and m.get("kind") == "human" and m.get("left_at") is None]


def _resolve(ix: _Index, chain: Chain,
             all_delib: list[_Delib], all_whisper: list[_Whisper],
             all_handoff: list[_Handoff], pending_delib: list[_Delib],
             pending_whisper: list[_Whisper], pending_handoff: list[_Handoff],
             done: dict[str, list], in_order: dict[str, bool]) -> None:
    """
    Attach the ids the coordinator minted to the creations that produced them.

    A task, whisper, deliberation or handoff id is generated server-side and
    returned in the *result*. The audit log records envelopes, so an id
    becomes visible when a later envelope acts on it. Two consequences, both
    handled here.

    Something acted on later is matched by that id, in order, and the ordering
    is checked: where it is forced by the log the row says so, and where it is
    a guess the row says that instead. Something created and left alone has
    its id in server state alone: it is matched there where the source carried
    it, and otherwise given a synthetic id so the count stays right.
    """
    _resolve_tasks(ix, chain)
    state = chain.state or {}

    for items, kind in ((all_delib, "delib"), (all_whisper, "whisper"),
                        (all_handoff, "handoff")):
        forced = (_forced_prefix([x.pos for x in items], [x.seen_pos for x in done[kind]])
                  if in_order[kind] else [False] * len(done[kind]))
        for i, x in enumerate(done[kind]):
            x.id_certain = forced[i] if x.matched_uniquely is None else x.matched_uniquely

    _resolve_pending(ix.delibs, pending_delib, state.get("deliberations") or {},
                     "opened_at", "deliberation_id", "deliberation")
    _resolve_pending(ix.whispers, pending_whisper, state.get("whispers") or {},
                     "asked_at", "whisper_id", "whisper")
    _resolve_pending(ix.handoffs, pending_handoff, state.get("handoffs") or {},
                     "proposed_at", "handoff_id", "handoff")

    # An acceptance moves work, and an acceptance attached to the wrong
    # outstanding offer moves the wrong work: one task is reassigned in the
    # table while it stayed put in fact, and another moved in fact while the
    # table leaves it where it started. Both sides are named by an offer whose
    # own identity is in doubt, so both are flagged on the task, which is the
    # column an analyst reads.
    for h in ix.handoffs.values():
        if h.id_certain:
            continue
        for t_id in set(h.task_ids) | set(h.accepted):
            moved = ix.tasks.get(t_id)
            if moved is not None:
                moved.assignee_certain = False


def _agree(a: Any, b: Any, *, absence_counts: bool) -> int:
    """
    Whether a creation envelope and a stored task agree about one attribute.

    ``absence_counts`` is for attributes the coordinator leaves as given at
    creation, where a task having no value is as much a fact as the value
    would be: a plain creation produces a task with an empty supersedes link,
    so an absent link is evidence. Attributes the coordinator fills in for
    itself, such as ``mode``, are read the other way: absent in the envelope
    and present in state is silent.
    """
    if a is None and b is None:
        return 0
    if a is None or b is None:
        return -1 if absence_counts else 0
    return 1 if a == b else -1


def _match_score(src: _Creation, stored: dict) -> int:
    """How far a creation envelope and a stored task agree about themselves."""
    hints = stored.get("routing_hints") if isinstance(stored.get("routing_hints"), dict) else {}
    return (
        _agree(src.kind, stored.get("kind"), absence_counts=False)
        + _agree(src.delegator, stored.get("delegator"), absence_counts=False)
        + _agree(src.mode, stored.get("mode"), absence_counts=False)
        + _agree(src.review_required, stored.get("review_required"), absence_counts=False)
        + _agree(src.supersedes, stored.get("supersedes"), absence_counts=True)
        + _agree(src.criticality, hints.get("criticality"), absence_counts=True)
        + _agree(src.risk_tier, hints.get("risk_tier"), absence_counts=True)
        + _agree(src.confidence, _decimal(hints.get("confidence")), absence_counts=True)
    )


def _assign_group(srcs: list[_Creation], candidates: list[str], stored: dict,
                  preferred: list[str | None], forced: list[bool]) -> list[tuple[str | None, bool]]:
    """
    Match creations to stored tasks created in the same millisecond.

    Taken in creation order, the first creation would claim whichever stored
    task happens to score well against it and leave the one that actually
    belonged to it for someone else. So the most clear-cut match is settled
    first: the pair whose best candidate beats its runner-up by the widest
    margin, then the next, until one side runs out.

    Where two stored tasks agree with a creation equally well, the ordering
    evidence decides: ``preferred`` is the id the replay attributed to this
    creation from the order ids appeared, and ``forced`` says whether that
    attribution was the only one the log allows. A tie settled that way is as
    certain as the ordering was; a tie left to chance is reported as one.
    """
    out: dict[int, tuple[str | None, bool]] = {}
    left = list(range(len(srcs)))
    right = list(candidates)
    while left and right:
        best: tuple | None = None
        for si in left:
            scored = sorted(
                ((_match_score(srcs[si], stored[tid]), 0 if tid == preferred[si] else 1, tid)
                 for tid in right),
                key=lambda triple: (-triple[0], triple[1], triple[2]))
            top_score, _, top_id = scored[0]
            margin = top_score - scored[1][0] if len(scored) > 1 else None
            # Widest margin first, then highest score, then creation order,
            # which keeps the result deterministic.
            key = (-(margin if margin is not None else 10**6), -top_score, si)
            if best is None or key < best[0]:
                best = (key, si, top_id, margin)
        _, si, tid, margin = best
        sure = margin is None or margin > 0 or (tid == preferred[si] and forced[si])
        out[si] = (tid, sure)
        left.remove(si)
        right.remove(tid)
    for si in left:
        out[si] = (None, False)
    return [out[i] for i in range(len(srcs))]


def _pair_with_state(ix: _Index, chain: Chain,
                     forced: list[bool]) -> tuple[list[str | None], list[bool]]:
    """
    Pair creations to the ids server state holds, using what the two agree on.

    State names every task that exists, so the pairing is a bijection, but the
    only ordering the log and the snapshot share is the creation timestamp, and
    that has millisecond resolution. Two tasks created in the same millisecond
    are ordered by their ids, which carry a random suffix, so ordering alone
    decides by coin flip and attributes one task's criticality and confidence
    to another, invisibly.

    So within a timestamp the creation is matched to the stored task that
    agrees with it about what it was created as, and where two agree equally
    the order the ids appeared in the log decides, with the certainty that
    ordering carries. Where both leave it open the row is marked uncertain.
    """
    stored = (chain.state or {}).get("tasks") or {}
    groups: dict[str, list[str]] = {}
    for tid, body in stored.items():
        if isinstance(body, dict):
            groups.setdefault(str(body.get("created_at") or ""), []).append(tid)
    ordered = sorted(groups)

    # Creations are dealt out to the timestamp groups in order, as many to each
    # group as that group holds ids. Which creation gets which id inside a
    # group is then settled on the evidence rather than on the order.
    n = len(ix.creations)
    out: list[str | None] = [None] * n
    certain: list[bool] = [False] * n
    i = 0
    for key in ordered:
        members = groups[key]
        take = ix.creations[i:i + len(members)]
        if not take:
            break
        assigned = _assign_group(take, members, stored,
                                 ix.claimed_by[i:i + len(take)], forced[i:i + len(take)])
        for offset, (tid, sure) in enumerate(assigned):
            out[i + offset] = tid
            certain[i + offset] = sure
        i += len(take)
    return out, certain


def _resolve_tasks(ix: _Index, chain: Chain) -> None:
    """
    Settle which creation made which task.

    The replay attributed each creation to the first unclaimed id it saw, which
    is right where work proceeds one task at a time and a guess where work
    interleaves. Server state settles it properly, by what the creation and the
    stored task agree they are; from envelopes alone the replay's own
    attribution stands and ``id_certain`` says how far it can be trusted. A
    creation that matches no observed id is given one marked ``unidentified``,
    so the count stays right.
    """
    if not ix.creations:
        return

    # What the order of appearance settles on its own, per creation.
    prefix = _forced_prefix([c.pos for c in ix.creations],
                            [ix.first_seen.get(t) for t in ix.task_order])
    forced = [prefix[i] if i < len(prefix) else False for i in range(len(ix.creations))]

    if chain.has_state:
        targets, certain = _pair_with_state(ix, chain, forced)
        # Detach first, so a creation moving from one task to another takes
        # its kind with it.
        for owner in ix.claimed_by:
            if owner and owner in ix.tasks:
                ix.tasks[owner].attribute(None)
    else:
        targets = list(ix.claimed_by)
        certain = forced

    attributed: list[tuple[_Creation, _Task]] = []
    for i, made in enumerate(ix.creations):
        tid, sure = targets[i], certain[i]
        if not tid:
            tid, sure = f"(unidentified-{i})", False
        t = ix.tasks.get(tid)
        if t is None:
            t = ix.tasks[tid] = _Task(task_id=tid)
        t.attribute(made)
        t.id_certain = sure
        attributed.append((made, t))

    if not chain.has_state:
        # A successor that took its assignee or mode from the task it replaces
        # is only as sure of them as that task's row is. In creation order, so
        # a chain of successors carries the doubt all the way down. With state
        # the inherited values are the coordinator's own.
        for made, t in attributed:
            old = ix.tasks.get(t.supersedes) if made.inherited and t.supersedes else None
            if old is None:
                continue
            if not old.id_certain:
                t.id_certain = False
            if "assignee" in made.inherited and not old.assignee_certain and not t.assignee_moved:
                t.assignee_certain = False

    for tid, t in ix.tasks.items():
        t.task_id = tid


def _resolve_pending(registry: dict, pending: list, state_items: dict,
                     time_field: str, id_attr: str, label: str) -> None:
    """
    Give an id to every creation the envelope stream left unnamed.

    Ordered by the creation timestamp state records. CHAP ids happen to sort
    chronologically today because they are ULIDs, and the timestamp is the
    ordering that holds by design. ``time_field`` is the name the coordinator
    serialises for this kind of record: a misspelt field reads as empty on
    every record, and the sort falls through to the id.
    """
    if not pending:
        return
    unobserved = sorted(
        (k for k in state_items if k not in registry),
        key=lambda k: (str((state_items[k] or {}).get(time_field) or ""), k))
    times = [str((state_items[k] or {}).get(time_field) or "") for k in unobserved]
    # Exact when there are as many candidates as creations and their recorded
    # times are distinct, because then the recorded order is the creation
    # order. A tie falls through to the id, which is random.
    exact = (len(unobserved) == len(pending) and all(times)
             and len(set(times)) == len(times))
    for i, item in enumerate(pending):
        real = unobserved[i] if i < len(unobserved) else f"(unidentified-{label}-{i})"
        setattr(item, id_attr, real)
        item.id_certain = exact and i < len(unobserved)
        registry.setdefault(real, item)


def _merge_state(ix: _Index, chain: Chain) -> None:  # noqa: C901 - one block per table
    """
    Overlay authoritative server state where the source carried it.

    Facts about what happened are the coordinator's to state, and state wins
    for them. Timestamps are the exception: a client may stamp its own time
    into every envelope, and the replay then runs on that one clock. State
    holds the coordinator's clock, and a duration needs both ends on one clock.
    So state fills a timestamp the replay lacks and leaves the ones it has.
    """
    if not chain.has_state:
        return
    state = chain.state or {}

    for tid, stored in (state.get("tasks") or {}).items():
        if not isinstance(stored, dict):
            continue
        t = ix.tasks.get(tid)
        if t is None:
            t = ix.tasks[tid] = _Task(task_id=tid)
        # State is authoritative: it is what the coordinator believes.
        t.state = stored.get("state", t.state)
        t.assignee = stored.get("assignee", t.assignee)
        # State holds who has the task now, whatever moved it there, so the
        # assignee is a reading on this path.
        t.assignee_certain = True
        t.kind = stored.get("kind", t.kind)
        t.delegator = stored.get("delegator", t.delegator)
        t.mode = stored.get("mode", t.mode)
        t.supersedes = stored.get("supersedes", t.supersedes)
        if stored.get("review_required") is not None:
            t.review_required = stored["review_required"]
        t.created_at = t.created_at or _ts(stored.get("created_at"))
        if t.confidence is None:
            t.confidence = _decimal(stored.get("confidence"))
        hints = stored.get("routing_hints") if isinstance(stored.get("routing_hints"), dict) else {}
        t.criticality = t.criticality or hints.get("criticality")
        t.risk_tier = t.risk_tier or hints.get("risk_tier")
        if t.confidence is None:
            t.confidence = _decimal(hints.get("confidence"))
        # An open task has no settlement time, whatever an earlier pass left
        # behind. The column is defined as null while open.
        if t.state not in _SETTLED:
            t.settled_at = None
        review = stored.get("review") or {}
        if review:
            # State carries the current pass only, which is the last one here.
            r = t.review or t.open_review(None, [], None, None)
            r.requested_at = r.requested_at or _ts(review.get("requested_at"))
            r.rule = review.get("rule") or r.rule
            r.requested_to = list(review.get("requested_to") or r.requested_to)

    # Overrides are matched to stored artefacts per task, in order: one
    # reviewer may correct the same task twice, so the pair (task, reviewer)
    # repeats. The stored artefact is what the coordinator computed and wins
    # where it is present; the replayed one stands elsewhere.
    stored_by_task: dict[str, list[dict]] = {}
    for art in (state.get("overrides") or {}).values():
        if isinstance(art, dict):
            stored_by_task.setdefault(art.get("task_id"), []).append(art)
    for arts in stored_by_task.values():
        arts.sort(key=lambda a: str(a.get("ts") or ""))
    seen: dict[str, int] = {}
    for ov in sorted(ix.overrides, key=lambda o: o["seq"] or 0):
        tid = ov["task_id"]
        i = seen.get(tid, 0)
        seen[tid] = i + 1
        arts = stored_by_task.get(tid) or []
        if i < len(arts):
            ov["based_on"] = arts[i].get("based_on_artefact", ov.get("based_on"))
            if "result" in arts[i]:
                ov["result"] = arts[i]["result"]

    for did, stored in (state.get("deliberations") or {}).items():
        if not isinstance(stored, dict):
            continue
        entry = ix.delibs.get(did)
        if entry is None:
            entry = ix.delibs[did] = _Delib(deliberation_id=did, id_certain=True)
        entry.task_id = stored.get("task_id", entry.task_id)
        entry.rule = stored.get("rule", entry.rule)
        entry.question = stored.get("question", entry.question)
        entry.opener = stored.get("opener", entry.opener)
        entry.opened_at = entry.opened_at or _ts(stored.get("opened_at"))
        entry.participants = list(stored.get("participants") or entry.participants)
        outcome = stored.get("outcome")
        if isinstance(outcome, dict):
            entry.outcome = outcome.get("outcome")
        # The coordinator records that a deliberation closed. The closing
        # envelope is the record of when, so closed_at stays as the replay
        # found it.

    for wid, stored in (state.get("whispers") or {}).items():
        if not isinstance(stored, dict):
            continue
        w = ix.whispers.get(wid)
        if w is None:
            w = ix.whispers[wid] = _Whisper(whisper_id=wid)
        w.task_id = stored.get("task_id", w.task_id)
        w.asker = stored.get("asker", w.asker)
        w.question = stored.get("question", w.question)
        w.asked_at = w.asked_at or _ts(stored.get("asked_at"))
        if stored.get("deadline_ms") is not None:
            w.deadline_ms = stored["deadline_ms"]
        w.had_options = bool(stored.get("options")) or w.had_options
        w.state = stored.get("state") or w.state
        w.answered_at = w.answered_at or _ts(stored.get("answered_at"))
        w.answered_by = stored.get("answered_by") or w.answered_by
        answer = stored.get("answer_option") or stored.get("answer_text")
        if answer is not None:
            w.answer = answer

    for hid, stored in (state.get("handoffs") or {}).items():
        if not isinstance(stored, dict):
            continue
        h = ix.handoffs.get(hid)
        if h is None:
            h = ix.handoffs[hid] = _Handoff(handoff_id=hid)
        h.proposer = stored.get("proposer", h.proposer)
        h.recipient = stored.get("recipient", h.recipient)
        ids = [x.get("task_id") for x in _as_list(stored.get("tasks"))
               if isinstance(x, dict) and x.get("task_id")]
        if ids:
            h.task_ids = ids
        h.proposed_at = h.proposed_at or _ts(stored.get("proposed_at"))
        h.reason = stored.get("decline_reason") or h.reason
        h.resolution = {"accepted": "accepted", "declined": "declined"}.get(
            stored.get("state") or "", "open")
        if h.resolution == "open":
            h.resolved_at, h.resolved_by = None, None
        else:
            h.resolved_at = h.resolved_at or _ts(stored.get("resolved_at"))
            h.resolved_by = stored.get("accepted_by") or h.resolved_by
            h.accepted = list(stored.get("accepted_task_ids") or h.accepted)
        if h.resolution != "accepted":
            h.accepted = []

    # Route decisions are matched to recorded calls per (task, method), in
    # order. The Python coordinator stores an escalate.auto artefact before it
    # checks that the escalation target exists, so a refused call can leave an
    # artefact behind with no matching audit entry; where that has happened the
    # artefacts run one ahead of the calls for that task.
    routes = sorted((state.get("route_decisions") or {}).values(),
                    key=lambda a: str(a.get("produced_at") or ""))
    by_task_method: dict[tuple, list[dict]] = {}
    for art in routes:
        by_task_method.setdefault((art.get("task"), art.get("decision_type")), []).append(art)
    taken: dict[tuple, int] = {}
    for row in sorted(ix.routing, key=lambda r: r["seq"] or 0):
        key = (row["task_id"], row["method"])
        i = taken.get(key, 0)
        taken[key] = i + 1
        arts = by_task_method.get(key) or []
        if i >= len(arts):
            continue
        art = arts[i]
        outcome = art.get("outcome")
        row["policy_id"] = art.get("policy_id")
        alts = art.get("alternatives_considered")
        if alts is None:
            alts = (art.get("extra") or {}).get("alternatives_considered")
        row["n_alternatives"] = len(alts or [])
        if row["method"] == "task.route":
            # The routing artefact says who was chosen at the time. Who holds
            # the work now is the task's assignee, which comes from state above.
            row["selected"] = outcome if isinstance(outcome, str) else None
        elif row["method"] == "review.depth":
            row["depth"] = outcome if isinstance(outcome, str) else None
        elif row["method"] == "escalate.auto":
            row["escalated"] = bool((outcome or {}).get("escalate")) \
                if isinstance(outcome, dict) else None

    for uri, m in (state.get("members") or {}).items():
        if not isinstance(m, dict):
            continue
        rec = ix.members.setdefault(uri, {"participant": uri, "joined_at": None, "left_at": None})
        rec["kind"] = m.get("type", rec.get("kind"))
        rec["role"] = m.get("role", rec.get("role"))


# ---------------------------------------------------------------- projection

def _coerce(s: pd.Series, dtype: str) -> pd.Series:
    """
    Cast a column, nulling the cells that fail to cast and keeping the rest.

    ``astype`` fails the whole column on one bad value. One client sending a
    deadline as a string would then empty ``deadline_ms`` for every whisper in
    the workspace, and an empty column looks the same as a source that lacked
    the value. Per-value coercion costs the one cell.

    pandas renders anything as text, so a string column is handled first: a
    container where a string was declared is a malformed value and is nulled,
    and a scalar is rendered as pandas would render it.
    """
    if dtype == "string":
        return s.map(lambda v: pd.NA if isinstance(v, (dict, list, tuple, set)) else v
                     ).astype("string")
    try:
        return s.astype(dtype)
    except (TypeError, ValueError):
        return _coerce_each(s, dtype)


def _coerce_each(s: pd.Series, dtype: str) -> pd.Series:
    """The per-value path of :func:`_coerce`, for a column with at least one value that will not cast."""
    if dtype.startswith("datetime64"):
        return pd.to_datetime(s, errors="coerce", utc=True)
    if dtype == "Int64":
        num = pd.to_numeric(s, errors="coerce")
        return num.where(num.isna() | (num == num.round())).astype("Int64")
    if dtype == "float64":
        return pd.to_numeric(s, errors="coerce").astype(dtype)
    if dtype == "boolean":
        return s.map(lambda v: v if isinstance(v, bool) else pd.NA).astype("boolean")
    raise ValueError(f"schema.py declares a dtype this projection has no coercion for: {dtype}")


def _enforce(table: schema.Table, rows: list[dict]) -> pd.DataFrame:
    """Build a frame with exactly the declared columns, order and dtypes."""
    df = pd.DataFrame(rows, columns=table.names) if rows else pd.DataFrame(
        {c.name: pd.Series(dtype="object") for c in table.columns})
    for col in table.columns:
        if col.dtype == "list":
            # Empty list rather than null, so a caller can explode or count
            # every row.
            df[col.name] = df[col.name].apply(lambda v: v if isinstance(v, list) else [])
            continue
        if col.dtype == "object":
            # An arbitrary artefact. Left exactly as it arrived, including
            # None, which is what a redactor leaves behind.
            continue
        df[col.name] = _coerce(df[col.name], col.dtype)
    return df[list(table.names)]


@dataclass
class Frames:
    """
    The tables. Attribute access, ``frames["decisions"]``, or iteration.

    ``chain`` is kept so an analysis can say what it was computed from. That
    matters when a column is null because the source lacked the value rather
    than because the value was absent from the workspace.
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
    handoffs: pd.DataFrame
    routing: pd.DataFrame

    def __getitem__(self, name: str) -> pd.DataFrame:
        if name not in schema.BY_NAME:
            raise KeyError(f"Unknown table {name!r}. The tables are: "
                           f"{', '.join(schema.BY_NAME)}")
        return getattr(self, name)

    def __iter__(self):
        """``(name, table)`` pairs in schema order."""
        for t in schema.TABLES:
            yield t.name, self[t.name]

    def as_dict(self) -> dict[str, pd.DataFrame]:
        """Every table by name, for anything that takes a mapping of frames."""
        return dict(self)

    def to_csv(self, directory: str, **kwargs: Any) -> list[str]:
        """
        Write every table to ``directory`` as ``<table>.csv`` and return the paths.

        Object columns hold artefacts and lists hold tags, and CSV renders both
        as text. For a round trip that keeps their structure, write each table
        with ``to_parquet`` or ``to_pickle`` instead.
        """
        import os

        os.makedirs(directory, exist_ok=True)
        written = []
        for name, table in self:
            path = os.path.join(directory, f"{name}.csv")
            table.to_csv(path, index=False, **kwargs)
            written.append(path)
        return written

    def __repr__(self) -> str:
        sizes = ", ".join(f"{t.name}={len(self[t.name])}" for t in schema.TABLES)
        return f"<Frames {self.chain.workspace!r} {sizes}>"

    def summary(self) -> str:
        lines = [repr(self.chain), ""]
        width = max(len(t.name) for t in schema.TABLES)
        for t in schema.TABLES:
            lines.append(f"  {t.name:<{width}}  {len(self[t.name]):>6} rows   {t.grain}")
        if not self.chain.has_state:
            lines += ["", "  Read from envelopes alone. Columns whose provenance is",
                      "  'state' are null: deliberation and routing outcomes."]
        unsure = [(name, int((~self[name]["id_certain"].fillna(False)).sum()))
                  for name in ("tasks", "deliberations", "whispers", "handoffs")]
        unsure = [(name, n) for name, n in unsure if n]
        if unsure:
            lines += ["", "  Rows whose id is inferred from the order of events, so the",
                      "  attributes on them may belong to a neighbour:"]
            lines += [f"    {name}: {n}" for name, n in unsure]
        return "\n".join(lines)


def frames(chain: Chain) -> Frames:
    """Project a chain into every table."""
    ix = _replay(chain)
    ws = chain.workspace

    tasks_by_id = ix.tasks

    # -- decisions, with the review pass they belong to folded in ------------
    decision_rows: list[dict] = []
    for tid, t in tasks_by_id.items():
        for pass_no, r in enumerate(t.reviews):
            for i, d in enumerate(sorted(r.decisions, key=lambda x: x["seq"] or 0)):
                lat = None
                if d["ts"] is not None and r.requested_at is not None:
                    lat = (d["ts"] - r.requested_at).total_seconds()
                decision_rows.append({
                    "task_id": tid, "workspace": ws, "seq": d["seq"],
                    "reviewer": d["reviewer"], "kind": d["kind"], "ts": d["ts"],
                    "requested_at": r.requested_at, "latency_s": lat,
                    "rule": r.rule, "review_index": pass_no,
                    "decision_index": i, "is_final": d["is_final"],
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
        # How a task ended is decided by the decision that settled its last
        # review pass. A state of completed says the work shipped; a final
        # approval says a reviewer let it. An approval in an earlier pass was
        # superseded when the work was sent back, and a partial approval under
        # all_approve left the review open.
        last = t.reviews[-1].decisions if t.reviews else []
        final = next((d["kind"] for d in last if d["is_final"]), None)
        last_kinds = {d["kind"] for d in last}
        if t.state == "completed":
            if final in ("override", "approve"):
                outcome = "overridden" if final == "override" else "approved"
            elif "reject" in last_kinds:
                # A reviewer sent it back and the work shipped anyway: the one
                # case an approval rate hides.
                outcome = "completed_after_rejection"
            elif t.reviews:
                # A review was open and unsettled when the work shipped.
                outcome = "completed_bypassing_review"
            else:
                outcome = "completed_without_review"
        elif t.state == "declined":
            # A reviewer's final rejection, or the assignee declining the work
            # through task.update on their own.
            outcome = "rejected" if final == "reject" else "declined"
        elif t.state == "abstained":
            outcome = "abstained"
        elif t.state in ("escalated", "cancelled", "superseded"):
            outcome = t.state
        else:
            outcome = "open"
        settled = t.state in _SETTLED
        lifetime = None
        if settled and t.created_at is not None and t.settled_at is not None:
            lifetime = (t.settled_at - t.created_at).total_seconds()
        task_rows.append({
            "task_id": tid, "workspace": ws, "kind": t.kind, "delegator": t.delegator,
            "assignee": t.assignee, "assignee_certain": t.assignee_certain,
            "id_certain": t.id_certain, "original_assignee": t.original_assignee,
            "mode": t.mode, "review_required": t.review_required, "state": t.state,
            "created_at": t.created_at,
            "settled_at": t.settled_at if settled else None, "settled": settled,
            "lifetime_s": lifetime, "outcome": outcome,
            "was_reviewed": bool(t.reviews) or bool(kinds),
            "was_overridden": "override" in kinds,
            "n_reviews": len(t.reviews),
            "n_decisions": len(t.decisions), "confidence": t.confidence,
            "criticality": t.criticality, "risk_tier": t.risk_tier,
            "supersedes": t.supersedes, "fulfils": t.fulfils,
        })

    # -- overrides and their operations -------------------------------------
    override_rows: list[dict] = []
    op_rows: list[dict] = []
    for ov in ix.overrides:
        t = tasks_by_id.get(ov["task_id"]) or _Task(task_id=ov["task_id"])
        diff = ov.get("diff") or []
        ops = [op for op in diff if isinstance(op, dict)]
        paths = [op.get("path") for op in ops]
        tops = [_top_path(p) for p in paths]
        override_rows.append({
            "task_id": ov["task_id"], "workspace": ws, "seq": ov["seq"],
            "reviewer": ov["reviewer"], "ts": ov["ts"], "rationale": ov["rationale"],
            "tags": ov["tags"], "policy_refs": ov["policy_refs"],
            "intent_preserved": ov["intent_preserved"], "logical_id": ov["logical_id"],
            # Counted from the operations that reached patch_ops, so the two
            # tables always reconcile even when the diff was malformed.
            "n_ops": len(ops),
            "op_kinds": sorted({op.get("op") for op in ops if op.get("op")}),
            "paths": paths, "top_path": tops[0] if tops else None,
            "based_on": ov.get("based_on"), "result": ov.get("result"),
            "task_kind": t.kind, "assignee": t.assignee, "confidence": t.confidence,
        })
        for i, op in enumerate(ops):
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
            "outcome": d.outcome, "id_certain": d.id_certain,
        })

    # -- whispers -----------------------------------------------------------
    whisper_rows = []
    for wid, w in ix.whispers.items():
        resp = None
        if w.answered_at is not None and w.asked_at is not None:
            resp = (w.answered_at - w.asked_at).total_seconds()
        # A whisper lapses when its deadline passes and the coordinator applies
        # the default. An answer arriving afterwards leaves the lapse in place,
        # so "answered" and "lapsed" are separate questions. Pending is a
        # third: a question asked a minute ago with a day to run is open.
        lapsed = w.lapsed_at is not None or w.state == "lapsed"
        deadline_ms = _decimal(w.deadline_ms)
        if not lapsed and resp is not None and deadline_ms is not None:
            # At the deadline. The coordinator's own lapse check keeps a whisper
            # alive while now < deadline, so an answer arriving exactly on the
            # deadline is already late.
            lapsed = resp * 1000.0 >= deadline_ms
        state = w.state or ("lapsed" if lapsed else
                            "answered" if w.answered_at is not None else "pending")
        whisper_rows.append({
            "whisper_id": wid, "workspace": ws, "task_id": w.task_id, "asker": w.asker,
            "question": w.question, "asked_at": w.asked_at, "deadline_ms": w.deadline_ms,
            "answered_at": w.answered_at, "answered_by": w.answered_by, "answer": w.answer,
            "answered": w.answered_at is not None, "state": state, "lapsed": lapsed,
            "response_s": resp, "had_options": w.had_options,
            "id_certain": w.id_certain,
        })

    handoff_rows = []
    for hid, h in ix.handoffs.items():
        resp = None
        if h.resolved_at is not None and h.proposed_at is not None:
            resp = (h.resolved_at - h.proposed_at).total_seconds()
        handoff_rows.append({
            "handoff_id": hid, "workspace": ws, "seq": h.seq, "proposer": h.proposer,
            "recipient": h.recipient, "task_ids": h.task_ids, "n_tasks": len(h.task_ids),
            "proposed_at": h.proposed_at, "resolved_at": h.resolved_at,
            "resolution": h.resolution, "resolved_by": h.resolved_by,
            "n_accepted": len(h.accepted), "reason": h.reason, "response_s": resp,
            "id_certain": h.id_certain,
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
        handoffs=_enforce(schema.HANDOFFS, handoff_rows),
        routing=_enforce(schema.ROUTING, ix.routing),
    )
