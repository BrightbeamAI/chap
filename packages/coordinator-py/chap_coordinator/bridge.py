"""
chap_coordinator.bridge
=======================

The CHAP half of a framework bridge, standard library only.

A framework bridge puts what an agent proposes in front of a person, waits
for their decision, and carries out what they decided. chap-langgraph,
chap-pydantic-ai, chap-llama-index, chap-ag2 and chap-google-adk each keep
their framework's half and share this one:

``ReviewClient(coord, workspace, agent, reviewer)``
    The agent's calls to one workspace. ``open`` puts an artefact under
    review, ``wait`` reads the task until a decision takes it out of
    review, and ``review_async`` does both for async code. A wait that ends
    without a decision, on a timeout, a cancellation or an error, withdraws
    the review, so no decision lands on work nobody will carry out.
    ``record`` and ``decide`` write a decision made outside CHAP in a
    person's name, marked as recorded, in a workspace that requires no
    signatures.
``Decision``
    A decision as the coordinator recorded it.
``wait_for_decision`` and ``wait_for_decision_async``
    The wait on its own, for anything that reads a task.
``artefact_digest(artefact)``
    The ``approved_artefact_digest`` that binds a decision to the artefact
    the person was shown.
``decided_arguments(decision, proposed)``
    The arguments a reviewed tool call runs with, or None where it must not
    run.
``json_patch(before, after)``
    The RFC 6902 patch a recorded override carries.

``coord`` is an in-process :class:`~chap_coordinator.Coordinator`, an
:class:`~chap_coordinator.client.HttpCoordinator`, or a
:class:`~chap_coordinator.client.Participant` for the agent, which signs the
agent's calls with its key. A refusal raises
:class:`~chap_coordinator.client.ChapError`.

In a workspace that requires signatures a bridge joins no person and records
no decision in anyone's name: a person joins from their own client, such as
the desk, with their own key, and signs their own decision, which the bridge
reads back.
"""
from __future__ import annotations

import asyncio
import dataclasses
import time
from dataclasses import dataclass
from functools import partial
from typing import Any, Callable, Iterable, Optional, TypeVar
from uuid import uuid4

from .canonical import content_hash
from .client import ChapError, Participant
from .coordinator import Coordinator
from .jsonrpc import E
from .server import task_view

__all__ = [
    "RECORDED_VIA",
    "ChapError",
    "Decision",
    "PersonRequired",
    "ReviewClient",
    "ReviewRejected",
    "artefact_digest",
    "decided_arguments",
    "is_decided",
    "json_patch",
    "wait_for_decision",
    "wait_for_decision_async",
]

#: A task in these states waits for its reviewer: under review, or paused
#: during its review, which control.resume takes back to review.
_UNDECIDED = ("review_requested", "paused")

#: The ``via`` of a decision a bridge records in a person's name. The
#: coordinator keeps it on the log with the call, so a reader of the log,
#: chap-analytics among them, tells a decision made elsewhere and recorded by
#: a bridge from one made at a desk.
RECORDED_VIA = "bridge-record"

T = TypeVar("T")


def is_decided(view: dict) -> bool:
    """Whether a decision, or another ending, has taken the task out of review."""
    return view.get("state") not in _UNDECIDED


def artefact_digest(artefact: Any) -> str:
    """The ``approved_artefact_digest`` of a decision on ``artefact``. The
    coordinator refuses a decision that names it unless ``artefact`` is the
    artefact under review."""
    return content_hash(artefact)


class PersonRequired(RuntimeError):
    """Only the person can do this.

    The workspace requires signatures, so the bridge records no decision in
    anyone's name there, and joins no person: a person joins from their own
    client, such as the desk, with their own key, and signs their own
    decision, which the bridge reads back.
    """


@dataclass(frozen=True)
class Decision:
    """A decision on a review, as the coordinator recorded it.

    ``approved`` is true when the task completed through an approval or an
    override, and ``output`` is then the artefact as decided: as proposed,
    or the reviewer's version. Any other ending approves nothing, and
    ``output`` is None.
    """

    task_id: str
    state: str
    kind: Optional[str] = None      # approve, override, reject or abstain
    reviewer: Optional[str] = None
    output: Any = None
    comment: Optional[str] = None   # the reviewer's note, or an override's rationale
    tags: tuple = ()

    @property
    def approved(self) -> bool:
        return self.state == "completed" and self.kind in ("approve", "override")

    @property
    def revision_requested(self) -> bool:
        """The reviewer rejected the proposal and asked the agent for another."""
        return self.kind == "reject" and self.state == "in_progress"

    @property
    def outcome(self) -> str:
        """How the review ended: approve, override, revise (a rejection that
        asks for another proposal), reject or abstain. Any other ending is
        the task's state: escalated, cancelled, or in_progress for a review
        its agent withdrew."""
        if self.approved:
            return self.kind  # type: ignore[return-value]
        if self.revision_requested:
            return "revise"
        if (self.kind, self.state) in (("reject", "declined"), ("abstain", "abstained")):
            return self.kind  # type: ignore[return-value]
        return self.state

    @classmethod
    def from_view(cls, task_id: str, view: dict) -> "Decision":
        """The decision a task's view holds, as ``GET /api/tasks/<id>``
        shows it: the last one of the review's current round."""
        decisions = (view.get("review") or {}).get("decisions") or []
        last = decisions[-1] if decisions else {}
        decision = cls(
            task_id=task_id,
            state=view.get("state"),
            kind=last.get("kind"),
            reviewer=last.get("reviewer"),
            comment=last.get("comment") or last.get("rationale"),
            tags=tuple(last.get("tags") or ()),
        )
        if decision.approved:
            return dataclasses.replace(decision, output=view.get("output"))
        return decision


class ReviewRejected(RuntimeError):
    """The reviewer approved nothing.

    ``outcome`` says how the review ended, as :attr:`Decision.outcome` does:
    reject, revise when the reviewer asked for another proposal, abstain,
    or the state another ending left the task in, which ``state`` holds. It
    derives from RuntimeError, which LangGraph's default retry policy does
    not retry.
    """

    def __init__(self, task_id: str, rationale: Optional[str] = None, *,
                 reviewer: Optional[str] = None, state: str = "declined",
                 outcome: Optional[str] = None) -> None:
        detail = f": {rationale}" if rationale else ""
        super().__init__(f"{task_id} was not approved ({state}){detail}")
        self.task_id = task_id
        self.rationale = rationale
        self.reviewer = reviewer
        self.state = state
        self.outcome = outcome or {"declined": "reject", "in_progress": "revise",
                                   "abstained": "abstain"}.get(state, state)

    @classmethod
    def from_decision(cls, decision: Decision) -> "ReviewRejected":
        return cls(decision.task_id, decision.comment, reviewer=decision.reviewer,
                   state=decision.state, outcome=decision.outcome)


def decided_arguments(decision: Decision, proposed: dict) -> Optional[dict]:
    """The arguments a reviewed tool call runs with, as the reviewer decided.

    ``proposed`` is the call as it was reviewed: ``tool``, ``args`` and
    ``tool_call_id``. The proposal's own arguments when the reviewer approved
    it as it was; theirs when they changed the arguments and nothing else;
    None when the call must not run: nothing was approved, or the edit took
    the arguments away, made them anything other than an object, or changed
    another part of the call, which the framework would not carry out.
    """
    if not decision.approved:
        return None
    output = decision.output
    if output == proposed:
        return proposed["args"]
    if (isinstance(output, dict) and output.keys() == proposed.keys()
            and all(output[key] == proposed[key] for key in proposed if key != "args")
            and isinstance(output["args"], dict)):
        return output["args"]
    return None


def json_patch(before: Any, after: Any, path: str = "") -> list[dict]:
    """An RFC 6902 patch that turns ``before`` into ``after``. Objects are
    compared key by key, in sorted order, so the same edit always gives the
    same patch; arrays and other values are replaced whole."""
    if type(before) is not type(after) or not isinstance(before, dict):
        return [] if before == after else [{"op": "replace", "path": path, "value": after}]
    ops: list[dict] = []
    for key in sorted(before.keys() | after.keys()):
        at = f"{path}/{str(key).replace('~', '~0').replace('/', '~1')}"
        if key not in after:
            ops.append({"op": "remove", "path": at})
        elif key not in before:
            ops.append({"op": "add", "path": at, "value": after[key]})
        else:
            ops.extend(json_patch(before[key], after[key], at))
    return ops


# -- waiting ------------------------------------------------------------------

def _deadline(timeout: Optional[float]) -> Optional[float]:
    return None if timeout is None else time.monotonic() + timeout


def _pause(deadline: Optional[float], poll_seconds: float, expired: str) -> float:
    """How long to sleep before the next look; TimeoutError once the deadline has passed."""
    if deadline is None:
        return poll_seconds
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError(expired)
    return min(poll_seconds, left)


def _until(look: Callable[[], Optional[T]], deadline: Optional[float], poll_seconds: float,
           expired: str) -> T:
    """``look()`` every ``poll_seconds`` until it returns something."""
    while True:
        found = look()
        if found is not None:
            return found
        time.sleep(_pause(deadline, poll_seconds, expired))


async def _until_async(look: Callable[[], Optional[T]], deadline: Optional[float],
                       poll_seconds: float, expired: str) -> T:
    """:func:`_until` for async code. Each look runs in a worker thread, so a
    read over HTTP does not hold up the event loop, and cancelling the
    awaiting task ends the wait."""
    while True:
        found = await asyncio.to_thread(look)
        if found is not None:
            return found
        await asyncio.sleep(_pause(deadline, poll_seconds, expired))


def _decided(read: Callable[[str], Optional[dict]], task_id: str) -> Optional[Decision]:
    """The task's decision, or None as long as it waits for one."""
    view = read(task_id)
    if view is None:
        raise KeyError(f"Unknown task: {task_id}")
    return Decision.from_view(task_id, view) if is_decided(view) else None


def _no_decision(task_id: str, timeout: Optional[float]) -> str:
    return f"No decision on {task_id} after {timeout} seconds"


def wait_for_decision(read: Callable[[str], Optional[dict]], task_id: str, *,
                      timeout: Optional[float] = None, poll_seconds: float = 2.0) -> Decision:
    """Read the task every ``poll_seconds`` until it is out of review, and
    return its decision.

    ``read(task_id)`` returns the task's view, as ``GET /api/tasks/<id>``
    shows it, or None for a task it does not know: an HttpCoordinator's or
    a Participant's ``task``, or :meth:`ReviewClient.read`. Raises KeyError
    for an unknown task, and TimeoutError after ``timeout`` seconds; None
    waits as long as the reviewer takes. The review is left as it is:
    :meth:`ReviewClient.wait` withdraws one it stops waiting for.
    """
    return _until(partial(_decided, read, task_id), _deadline(timeout), poll_seconds,
                  _no_decision(task_id, timeout))


async def wait_for_decision_async(read: Callable[[str], Optional[dict]], task_id: str, *,
                                  timeout: Optional[float] = None,
                                  poll_seconds: float = 2.0) -> Decision:
    """:func:`wait_for_decision` for async code. Each read runs in a worker
    thread, and cancelling the awaiting task ends the wait."""
    return await _until_async(partial(_decided, read, task_id), _deadline(timeout),
                              poll_seconds, _no_decision(task_id, timeout))


# -- the agent's client -------------------------------------------------------

def _is_store_failure(error: dict) -> bool:
    """A call answered this way took no effect: its write was lost to another
    writer, or never made, and the coordinator says it may be sent again."""
    data = error.get("data")
    return error.get("code") == E.INTERNAL and isinstance(data, dict) and data.get("reason") == "store"


def _in_process(coord: Any) -> Optional[Coordinator]:
    """The Coordinator in this process behind ``coord``, if there is one."""
    target = coord.client if isinstance(coord, Participant) else coord
    return target if isinstance(target, Coordinator) else None


def _signs(described: dict, coord: Any) -> bool:
    """Whether the workspace requires signatures: it advertises
    security-signed/1.0, or the coordinator in this process requires them
    everywhere."""
    if any(p == "security-signed" or p.startswith("security-signed/")
           for p in described.get("profiles") or []):
        return True
    local = _in_process(coord)
    return local is not None and bool(local.options.require_signatures)


def _reviewable(artefact: Any) -> None:
    """Refuse a review of nothing: a person shown an empty artefact approves
    nothing they could read, and the framework would carry on regardless."""
    empty = artefact.strip() == "" if isinstance(artefact, str) else artefact in (None, {}, [])
    if empty:
        raise ValueError("There is nothing to review: the artefact is empty.")


class ReviewClient:
    """The agent's calls to one workspace, for a framework bridge.

    Made, it creates the workspace where there is none and joins the agent,
    before anything reads the workspace: where ``coord`` is a Participant
    that signs, the agent joins with the Participant's key, and every call
    after the join is signed. A workspace that exists already, such as a
    desk's, is taken as it is, and must advertise ``profiles``.

    A review is opened on a task that requires one, so only a person's
    decision completes it. The coordinator addresses it to every person in
    the workspace. ``reviewer`` is the person a recorded decision names by
    default, and in a workspace that requires no signatures the bridge joins
    them before the first review opens, so that decision is theirs to make.

    ``client`` reads a task: anything with ``task(task_id)`` returning the
    ``GET /api/tasks/<id>`` view. A Coordinator in this process, an
    HttpCoordinator and a Participant need none. ``on_envelope`` sees each
    call's method and params before it is sent, and ``name`` begins each
    call's id.
    """

    def __init__(self, coord: Any, workspace: str, agent: str, reviewer: str, *,
                 profiles: Iterable[str] = ("core/1.0", "review/1.0"), client: Any = None,
                 on_envelope: Optional[Callable[[str, dict], None]] = None,
                 name: str = "chap-bridge") -> None:
        scheme = agent.split(":", 1)[0]
        if scheme not in ("agent", "service"):
            raise ValueError(f"agent must be an 'agent:' or 'service:' URI, got {agent!r}")
        if not reviewer.startswith("human:"):
            raise ValueError(f"reviewer must be a 'human:' URI, a person, got {reviewer!r}")
        if isinstance(coord, Participant) and coord.uri != agent:
            raise ValueError(f"the Participant calls as {coord.uri}, and the agent is {agent}")
        self.coord = coord
        self.workspace = workspace
        self.agent = agent
        self.reviewer = reviewer
        self.client = client
        self.on_envelope = on_envelope
        self.name = name
        self._joined: set[str] = set()
        profiles = list(profiles)
        refused = self._send("workspace.create", {"workspace": workspace, "profiles": profiles}).get("error")
        joining: dict[str, Any] = {"type": scheme}
        signer = coord.signer if isinstance(coord, Participant) else None
        if signer is not None:
            joining["jwks"] = {"keys": [signer.public_jwk]}
        self.call("participant.join", joining)
        if refused:
            # The workspace was there already, as a desk's is, or could not
            # be made as asked; either way it must offer what the bridge uses.
            advertised = self.describe().get("profiles") or []
            missing = [p for p in profiles if p not in advertised]
            if missing:
                raise ValueError(f"{workspace} does not advertise {', '.join(missing)} "
                                 f"(workspace.create answered: {refused.get('message')})")

    # -- calls --------------------------------------------------------------

    def _send(self, method: str, params: dict) -> dict:
        """One call and its JSON-RPC response. A call answered with ``data:
        {"reason": "store"}`` took no effect, and is sent once more, with an
        id of its own."""
        for _ in range(2):
            if self.on_envelope is not None:
                self.on_envelope(method, params)
            out = self.coord.dispatch({"jsonrpc": "2.0", "id": f"{self.name}-{method}-{uuid4().hex}",
                                       "method": method, "params": params})
            if not (out.get("error") and _is_store_failure(out["error"])):
                break
        return out

    def call(self, method: str, params: dict, *, sender: Optional[str] = None) -> Any:
        """One call to the workspace, from the agent or ``sender``, and its
        result; ChapError when refused."""
        out = self._send(method, {"workspace": self.workspace, "from": sender or self.agent, **params})
        error = out.get("error")
        if error:
            raise ChapError(error.get("code", E.INTERNAL), error.get("message", ""), error.get("data"), method)
        return out.get("result")

    def describe(self) -> dict:
        """The workspace, read with workspace.describe as the agent."""
        return self.call("workspace.describe", {})

    def requires_signatures(self) -> bool:
        """Whether the workspace requires signatures, read afresh."""
        return _signs(self.describe(), self.coord)

    def read(self, task_id: str) -> Optional[dict]:
        """The task as ``GET /api/tasks/<id>`` shows it, or None for a task
        the coordinator does not know.

        A Coordinator in this process is read after a call through its
        dispatch, which first brings the workspace up to date with its
        store, so a decision another process wrote there is seen. Any other
        coordinator is read with ``client``'s ``task(task_id)``, or its own.
        """
        if self.client is not None:
            return self.client.task(task_id)
        local = _in_process(self.coord)
        if local is not None:
            # dispatch holds this lock for the length of a call, so the task
            # is never read halfway through one.
            with local._lock:
                self.describe()
                return task_view(local, self.workspace, task_id)
        read = getattr(self.coord, "task", None)
        if not callable(read):
            raise TypeError("waiting for a decision reads the task: give the bridge an in-process "
                            "Coordinator, an HttpCoordinator or a Participant, or a client with "
                            "task(task_id) as `client`")
        return read(task_id)

    def audit(self) -> list[dict]:
        """The workspace's audit log."""
        return self.call("audit.read", {})["entries"]

    # -- reviews ------------------------------------------------------------

    def _join_person(self, uri: str) -> None:
        """Join a person in a workspace that requires no signatures."""
        if not uri.startswith("human:"):
            raise ValueError(f"a decision is made by a person, a 'human:' URI, got {uri!r}")
        if uri not in self._joined:
            self.call("participant.join", {"type": "human"}, sender=uri)
            self._joined.add(uri)

    def _ready_for_review(self) -> Optional[bool]:
        """One look before a review opens: True once someone can decide it,
        None as long as nobody can. Where the workspace requires no
        signatures the reviewer joins here; where it does, a person joins
        from their own client."""
        described = self.describe()
        if not _signs(described, self.coord):
            self._join_person(self.reviewer)
            return True
        return True if any(m.get("type") == "human" for m in described.get("members") or []) else None

    def _nobody(self, timeout: Optional[float]) -> str:
        return (f"Nobody joined {self.workspace} to review this within {timeout} seconds, "
                "so no review was opened")

    def _submit(self, artefact: Any, kind: str, input: Any, task_id: Optional[str]) -> str:
        if task_id is None:
            task_id = self.call("task.create", {
                "assignee": self.agent, "kind": kind,
                "input": {} if input is None else input, "review_required": True,
            })["task_id"]
        self.call("task.complete", {"task_id": task_id, "output": artefact})
        return task_id

    def open(self, artefact: Any, *, kind: str, input: Any = None, task_id: Optional[str] = None,
             timeout: Optional[float] = None, poll_seconds: float = 2.0) -> str:
        """Put ``artefact`` under review, and return the task id.

        The review opens on a new task of ``kind``, with ``input`` as its
        input, which requires review, so only a person's decision completes
        it. With ``task_id`` the artefact is the next proposal on a task
        whose reviewer asked for another, or whose review was withdrawn, and
        opens the review's next round.

        Where the workspace requires signatures, a person joins it from
        their own client, and until one has the review waits to open,
        reading the workspace every ``poll_seconds``: TimeoutError after
        ``timeout`` seconds, with nothing written. An empty artefact is
        refused with ValueError.
        """
        return self._open(artefact, kind, input, task_id, _deadline(timeout), timeout, poll_seconds)

    def _open(self, artefact: Any, kind: str, input: Any, task_id: Optional[str],
              deadline: Optional[float], timeout: Optional[float], poll_seconds: float) -> str:
        _reviewable(artefact)
        _until(self._ready_for_review, deadline, poll_seconds, self._nobody(timeout))
        return self._submit(artefact, kind, input, task_id)

    async def open_async(self, artefact: Any, *, kind: str, input: Any = None,
                         task_id: Optional[str] = None, timeout: Optional[float] = None,
                         poll_seconds: float = 2.0) -> str:
        """:meth:`open` for async code. Cancelled once the review is opening,
        the review is withdrawn as soon as it is open."""
        return await self._open_async(artefact, kind, input, task_id, _deadline(timeout), timeout,
                                      poll_seconds)

    async def _open_async(self, artefact: Any, kind: str, input: Any, task_id: Optional[str],
                          deadline: Optional[float], timeout: Optional[float],
                          poll_seconds: float) -> str:
        _reviewable(artefact)
        await _until_async(self._ready_for_review, deadline, poll_seconds, self._nobody(timeout))
        submitting = asyncio.ensure_future(asyncio.to_thread(self._submit, artefact, kind, input, task_id))
        try:
            return await asyncio.shield(submitting)
        except asyncio.CancelledError:
            # The thread opens the review whatever happens here. Once it has,
            # the review is withdrawn: nobody waits for its decision now.
            await asyncio.wait([submitting])
            if not submitting.cancelled() and submitting.exception() is None:
                await asyncio.to_thread(self._withdraw_quietly, submitting.result())
            raise

    def wait(self, task_id: str, *, timeout: Optional[float] = None,
             poll_seconds: float = 2.0) -> Decision:
        """Wait for the decision on a review this client opened, and return it.

        The task is read every ``poll_seconds``. After ``timeout`` seconds the
        review is withdrawn and TimeoutError raised, unless a decision landed
        first, which is returned; None waits as long as the reviewer takes.
        A wait that ends any other way, on an interrupt or an error, also
        withdraws the review before the exception propagates.
        """
        return self._wait(task_id, _deadline(timeout), timeout, poll_seconds)

    def _wait(self, task_id: str, deadline: Optional[float], timeout: Optional[float],
              poll_seconds: float) -> Decision:
        try:
            return _until(partial(_decided, self.read, task_id), deadline, poll_seconds,
                          _no_decision(task_id, timeout))
        except TimeoutError:
            return self._after_timeout(task_id, timeout)
        except BaseException:
            self._withdraw_quietly(task_id)
            raise

    async def wait_async(self, task_id: str, *, timeout: Optional[float] = None,
                         poll_seconds: float = 2.0) -> Decision:
        """:meth:`wait` for async code. Cancelling the awaiting task withdraws
        the review, and the cancellation propagates."""
        return await self._wait_async(task_id, _deadline(timeout), timeout, poll_seconds)

    async def _wait_async(self, task_id: str, deadline: Optional[float], timeout: Optional[float],
                          poll_seconds: float) -> Decision:
        try:
            return await _until_async(partial(_decided, self.read, task_id), deadline,
                                      poll_seconds, _no_decision(task_id, timeout))
        except TimeoutError:
            return await asyncio.to_thread(self._after_timeout, task_id, timeout)
        except BaseException:
            await asyncio.to_thread(self._withdraw_quietly, task_id)
            raise

    def review(self, artefact: Any, *, kind: str, input: Any = None, task_id: Optional[str] = None,
               timeout: Optional[float] = None, poll_seconds: float = 2.0) -> Decision:
        """:meth:`open` then :meth:`wait`, within one ``timeout``."""
        deadline = _deadline(timeout)
        opened = self._open(artefact, kind, input, task_id, deadline, timeout, poll_seconds)
        return self._wait(opened, deadline, timeout, poll_seconds)

    async def review_async(self, artefact: Any, *, kind: str, input: Any = None,
                           task_id: Optional[str] = None, timeout: Optional[float] = None,
                           poll_seconds: float = 2.0) -> Decision:
        """:meth:`open_async` then :meth:`wait_async`, within one ``timeout``.
        Cancelled at any point, it leaves no review open."""
        deadline = _deadline(timeout)
        opened = await self._open_async(artefact, kind, input, task_id, deadline, timeout, poll_seconds)
        return await self._wait_async(opened, deadline, timeout, poll_seconds)

    def withdraw(self, task_id: str, note: str) -> Optional[Decision]:
        """Take the review of ``task_id`` back, so no decision lands on it, and
        return None; or the decision, where one landed first. The task goes
        back to in_progress with ``note`` in its history, and a later
        :meth:`open` with ``task_id`` opens its next round."""
        view = self.read(task_id)
        if view is not None and is_decided(view):
            return Decision.from_view(task_id, view)
        try:
            self.call("task.update", {"task_id": task_id, "state": "in_progress", "progress_note": note})
        except ChapError:
            view = self.read(task_id)
            if view is not None and is_decided(view):
                return Decision.from_view(task_id, view)
            raise
        return None

    def _after_timeout(self, task_id: str, timeout: Optional[float]) -> Decision:
        try:
            decided = self.withdraw(task_id, f"The agent stopped waiting for a decision after {timeout} seconds.")
        except Exception as exc:
            raise TimeoutError(f"{_no_decision(task_id, timeout)}, and its review could not be "
                               f"withdrawn: {exc}") from exc
        if decided is None:
            raise TimeoutError(f"{_no_decision(task_id, timeout)}; its review is withdrawn")
        return decided

    def _withdraw_quietly(self, task_id: str) -> None:
        """Withdraw on the way out of an interrupt, a cancellation or an
        error, which propagates whatever happens here."""
        try:
            self.withdraw(task_id, "The agent stopped waiting for a decision.")
        except Exception:
            pass

    def close(self, task_id: str, note: str) -> None:
        """Decline a task whose reviewer asked for another proposal, where
        the next proposal is reviewed on a task of its own. ``note`` says
        so in the task's history."""
        self.call("task.update", {"task_id": task_id, "state": "declined", "progress_note": note})

    # -- decisions made elsewhere ---------------------------------------------

    def _refuse_where_signed(self, reviewer: str) -> None:
        if self.requires_signatures():
            raise PersonRequired(
                f"{self.workspace} requires signatures, so no decision is recorded here in "
                f"{reviewer}'s name: they decide in their own client, such as the desk, signed "
                "with their own key, and the bridge reads the decision back.")

    def decide(self, task_id: str, kind: str, *, digest: str, reviewer: Optional[str] = None,
               comment: Optional[str] = None, tags: Optional[Iterable[str]] = None,
               category: Optional[str] = None, request_revision: bool = False,
               diff: Optional[list] = None, intent_preserved: Optional[bool] = None) -> Decision:
        """Record a decision made outside CHAP on the open review of
        ``task_id``, in ``reviewer``'s name, and return it as the coordinator
        holds it.

        ``kind`` is approve, reject or override. ``digest`` is the
        :func:`artefact_digest` of what the person was shown, and the
        coordinator refuses the decision unless that is the artefact under
        review. A rejection takes ``category``, and ``request_revision`` to
        ask for another proposal; an override takes ``diff``, an RFC 6902
        patch of the artefact, with ``comment`` as its rationale. The call
        carries ``via: "bridge-record"``, so the log tells it from a
        decision made at a desk.

        Where the task holds this reviewer's decision of the same kind on
        the same artefact already, as when a step that recorded it runs
        again, that decision is returned and nothing is recorded twice.
        Where the workspace requires signatures nothing is written and
        PersonRequired is raised.
        """
        reviewer = reviewer or self.reviewer
        self._refuse_where_signed(reviewer)
        self._join_person(reviewer)
        return self._decide(task_id, kind, digest, reviewer, comment, tags, category,
                            request_revision, diff, intent_preserved)

    def _decide(self, task_id: str, kind: str, digest: str, reviewer: str, comment: Optional[str],
                tags: Optional[Iterable[str]], category: Optional[str], request_revision: bool,
                diff: Optional[list], intent_preserved: Optional[bool]) -> Decision:
        params: dict[str, Any] = {"task_id": task_id, "approved_artefact_digest": digest,
                                  "via": RECORDED_VIA}
        if kind == "override":
            if not diff:
                raise ValueError("an override needs a diff, an RFC 6902 patch of the artefact")
            params.update(diff=diff, rationale=comment or "edited by the reviewer", tags=list(tags or []))
            if intent_preserved is not None:
                params["intent_preserved"] = intent_preserved
        elif kind in ("approve", "reject"):
            if comment:
                params["comment"] = comment
            if tags:
                params["tags"] = list(tags)
            if kind == "reject" and category is not None:
                params["category"] = category
            if kind == "reject" and request_revision:
                params["request_revision"] = True
        else:
            raise ValueError(f"a recorded decision is approve, reject or override, got {kind!r}")
        recorded = self._recorded(task_id, kind, digest, reviewer)
        if recorded is not None:
            return recorded
        self.call(f"decide.{kind}", params, sender=reviewer)
        return Decision.from_view(task_id, self.read(task_id) or {})

    def _recorded(self, task_id: str, kind: str, digest: str, reviewer: str) -> Optional[Decision]:
        """The task's decision, where one has taken it out of review and it is
        ``reviewer``'s of ``kind`` on the artefact whose digest is
        ``digest``; None otherwise."""
        view = self.read(task_id)
        if view is None or not is_decided(view):
            return None
        decisions = (view.get("review") or {}).get("decisions") or []
        last = decisions[-1] if decisions else {}
        if (last.get("reviewer"), last.get("kind")) != (reviewer, kind):
            return None
        if artefact_digest(view.get("artefact")) != digest:
            return None
        return Decision.from_view(task_id, view)

    def record(self, artefact: Any, kind: str, *, task_kind: str, input: Any = None,
               reviewer: Optional[str] = None, comment: Optional[str] = None,
               tags: Optional[Iterable[str]] = None, category: Optional[str] = None,
               diff: Optional[list] = None, intent_preserved: Optional[bool] = None) -> Decision:
        """Record a decision made outside CHAP on ``artefact``: open its review
        on a new task of ``task_kind``, then decide it in ``reviewer``'s name
        as :meth:`decide` does, bound to ``artefact``'s digest. Returns the
        decision as the coordinator holds it.

        Where the workspace requires signatures nothing is written and
        PersonRequired is raised.
        """
        reviewer = reviewer or self.reviewer
        _reviewable(artefact)
        self._refuse_where_signed(reviewer)
        self._join_person(reviewer)
        task_id = self._submit(artefact, task_kind, input, None)
        return self._decide(task_id, kind, artefact_digest(artefact), reviewer, comment, tags,
                            category, False, diff, intent_preserved)
