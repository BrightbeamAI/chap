"""
Differential conformance fuzzer for the two reference coordinators.

CHAP's headline guarantee is that two independently-authored coordinators
produce byte-identical results (SPECIFICATION.md). Static vectors check that on
hand-written cases; this checks it on random ones.

For each seed it drives the Python reference with a random sequence of calls,
records the concrete envelopes it sent (ids are server-minted, so a sequence
cannot be written in advance), replays that exact sequence into the
TypeScript reference in a subprocess, and asserts the two agree on every
response, on the state each holds after every call, and on the audit-chain
head. A subprocess boundary is used so no serialisation layer masks or
invents a difference. An answer of -32603 from either fails the seed: it is a
fault in the coordinator, whatever the other answered.

Each seed runs in one of five configurations, by turns:

- plain: no signatures checked; a share of the calls carries a `sig` that
  only marks them as signed.
- signed: the workspace advertises security-signed/1.0 and every member
  signs with a real Ed25519 key. A share of the calls is unsigned, carries a
  signature that does not verify, names a kid the sender does not hold, or
  is signed with another member's key, and members rotate and revoke keys
  and are given new ones by an admin's invitation.
- humans: the same under the signature policy "humans", where an agent's
  unsigned call is taken.
- oidc: signed, with a stub token verifier and a stub credential verifier in
  both references and step-up enforced. People join with tokens that name
  them, or another participant, and bind their key, another key or none.
- invite: the workspace admits by invitation, so joins meet the admin's
  invitations and the refusals of those that have none, and both references
  hold reads to the membership floor.

The action set covers the core and review lifecycle, control, handoff, whisper,
deliberation and routing, including canonical control.snapshot artefacts and
subsequent rollback, participant pauses, which the calls that assign work
then meet, and the admin's workspace.invite.

Each envelope is recorded as the client sent it, taken before dispatch, and
every seed also asserts that dispatch left it untouched. A coordinator records
what it received: a handler that writes into the request makes the recorded
envelope differ from the signed one, and would otherwise be replayed into the
other reference as though both had produced it.

The state each reference holds is compared with its nulls. Both coordinators
read a null in a field of their own records as a field left out, so the
projections leave such a null out; a null anywhere else, in what a client sent
or what the log holds, is compared as it is, and so is a whisper's
default_if_lapsed, whose null is the default a lapse applies.

Each seed is also replayed into both references on a SQLite file, restarted,
its pending whispers lapsed, and restarted again. Each call writes only what
it changed, so the check is that nothing was missed: a coordinator restarted
on the file holds what the one that wrote it held, and the two references
wrote the same audit entries. Then each reference replays the seed again with
two coordinators on one file, taking the calls by turns, while the store fails
a share of the writes and reloads, and now and then the other coordinator
writes between a call's keep-up and its write. The two references must answer
every call alike there too, failures included, and leave the same entries.

Action selection reads the workspace as it stands: decisions go to tasks under
review and to reviewers the review named, resumes go to paused tasks, handoffs
are resolved by the participant they were offered to, votes come from
participants who have not voted. A share of the steps is a deliberate miss, so
the refusals are compared as well as the successes. Overrides draw from the
whole JSON Patch operation set against documents with arrays and nesting,
including the `1e1` and `1_0` index tokens behind #103 and digits outside
ASCII, which neither reference reads as a number. Reviews are opened on no
artefact, or a null one, now and then, and amended.

Another share is malformed: a call to any method with one or two of the params
it reads missing, null or of another type, the workspace and the sender
included, params that are not an object, and envelopes that are not a call at
all. The state comparison is what makes these worth sending: a handler that
changed state and then refused or raised shows up there even when both
references answered alike.

Usage:
    python fuzz.py --seeds 200          # sweep seeds 0..199
    python fuzz.py --seed 42            # one seed, verbose on divergence
    python fuzz.py --seeds 50 --steps 60
    python fuzz.py --seeds 40 --stats   # with per-method coverage
    python fuzz.py --seeds 20 --config signed
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.canonical import canonicalize
from chap_coordinator.crypto import derive_private_key, jwk_thumbprint, public_jwk, sign
from chap_coordinator.storage.sqlite import SqliteStore

_ROOT = Path(__file__).resolve().parents[2]
_REPLAY = _ROOT / "conformance" / "differential" / "replay.ts"

PROFILES = ["core/1.0", "review/1.0", "control/1.0", "handoff/1.0",
            "whisper/1.0", "deliberation/1.0", "routing/1.0"]
HUMANS = ["human:a", "human:c"]
AGENTS = ["agent:b", "agent:d"]
# Participants who are not members at the start: an invitation may name them,
# and they may join. The address scopes are refused at both.
NEWCOMERS = ["human:e", "agent:f", "service:g"]
SCOPES = ["group:x", "workspace:y"]


@dataclass(frozen=True)
class Config:
    """How the workspace is set up, and what the two references are given."""

    name: str
    #: The workspace advertises security-signed/1.0, and members sign with
    #: real keys.
    signed: bool = False
    #: The workspace's signature policy.
    policy: str = "all"
    #: Both references hold the stub verifiers and enforce step-up, and the
    #: workspace advertises identity-oidc/1.0.
    oidc: bool = False
    #: The workspace's join policy.
    join_policy: str = "open"
    #: Both references hold reads to the membership floor.
    read_membership: bool = False


CONFIGS = [
    Config("plain"),
    Config("signed", signed=True),
    Config("humans", signed=True, policy="humans"),
    Config("oidc", signed=True, oidc=True),
    Config("invite", join_policy="invite", read_membership=True),
]


def stub_token_verifier(token: str) -> dict | None:
    """The fuzzer's token verifier, the same in replay.ts: a token is
    `stub.` and the JSON of its claims, and any other string does not
    verify."""
    if not isinstance(token, str) or not token.startswith("stub."):
        return None
    try:
        claims = json.loads(token[len("stub."):])
    except ValueError:
        return None
    return claims if isinstance(claims, dict) else None


def stub_credential_verifier(presentation: dict) -> dict | None:
    """The fuzzer's credential verifier, the same in replay.ts: a
    presentation's `subject` object is what it verifies to."""
    subject = presentation.get("subject") if isinstance(presentation, dict) else None
    return subject if isinstance(subject, dict) else None


def coordinator_options(config: Config) -> dict:
    """The options both references are given, as replay.ts reads them."""
    options: dict = dict(deterministic_ids=True, deterministic_clock=True,
                         enable_chain=True, default_profiles=PROFILES)
    if config.oidc:
        options.update(verify_oidc_token=stub_token_verifier,
                       verify_vc=stub_credential_verifier, enforce_step_up=True)
    if config.read_membership:
        options.update(require_read_membership=True)
    return options


# Every method a reference dispatches, with the params its handler reads. The
# malformed calls draw on these, so each method meets a missing or ill-typed
# value in every param it reads, and its refusal, its state and its log are
# compared like any other call's.
METHOD_PARAMS: dict[str, tuple[str, ...]] = {
    "workspace.create": ("profiles", "mode", "mode_ceiling", "routing_policy_uri",
                         "step_up_window_sec", "min_acr", "signature_policy", "join_policy"),
    "workspace.describe": (),
    "workspace.set_profiles": ("profiles",),
    "workspace.invite": ("uri", "type", "role", "jwks", "oidc_sub"),
    "participant.join": ("type", "role", "display_name", "capabilities", "scopes", "jwks",
                         "oidc_token", "vc_presentation"),
    "participant.leave": (),
    "participant.rotate_key": ("old_kid", "new_jwk"),
    "participant.revoke_key": ("target_uri", "kid", "reason"),
    "task.create": ("kind", "input", "assignee", "to", "mode", "review_required", "deadline",
                    "routing_hints", "idempotency_key"),
    "task.update": ("task_id", "state", "progress_note"),
    "task.complete": ("task_id", "output", "confidence"),
    "audit.read": ("range", "filter"),
    "review.request": ("task_id", "artefact", "to", "rule", "deadline"),
    "review.acknowledge": ("task_id",),
    "decide.approve": ("task_id", "comment", "tags", "approved_artefact_digest"),
    "decide.reject": ("task_id", "comment", "tags", "request_revision",
                      "approved_artefact_digest"),
    "decide.override": ("task_id", "diff", "rationale", "tags", "policy_refs", "logical_id",
                        "instance_id", "intent_preserved", "approved_artefact_digest"),
    "abstain.declare": ("task_id", "reason", "category"),
    "escalate.raise": ("original_task_id", "new_task"),
    "whisper.ask": ("task_id", "to", "question", "options", "deadline_ms",
                    "default_if_lapsed", "urgency", "whisper_id"),
    "whisper.answer": ("whisper_id", "answer", "answer_text", "answer_option", "comment"),
    "deliberate.open": ("to", "participants", "rule", "question", "weights", "veto",
                        "deadline", "task_id", "deliberation_id"),
    "deliberate.comment": ("deliberation_id", "comment", "text"),
    "deliberate.vote": ("deliberation_id", "vote", "weight", "comment", "veto_invoked"),
    "deliberate.close": ("deliberation_id",),
    "handoff.propose": ("to", "tasks", "summary", "context_links", "handoff_id"),
    "handoff.accept": ("handoff_id", "accepted_task_ids", "comment"),
    "handoff.decline": ("handoff_id", "reason", "suggested_target"),
    "control.pause": ("scope", "task_id", "participant_uri", "uri", "in_flight_policy",
                      "reason"),
    "control.resume": ("scope", "task_id", "participant_uri", "uri"),
    "control.cancel": ("task_id", "reason"),
    "control.snapshot": ("include", "label"),
    "control.rollback": ("to_snapshot_artefact_id", "snapshot_artefact_id", "snapshot_id",
                         "what_to_restore", "reason"),
    "control.supersede": ("task_id", "successor_task", "reason"),
    "control.set_mode_ceiling": ("new_ceiling", "mode_ceiling", "reason"),
    "task.route": ("task_id", "candidates"),
    "review.depth": ("task_id", "artefact_routing_hints"),
    "escalate.auto": ("task_id", "default_escalation_target"),
    "audit.verify_chain": ("from_seq", "to_seq"),
    "audit.verify_receipt": ("receipt",),
    "audit.submit_to_scitt": ("range", "issuer"),
}

# The signature on a call in the plain configuration. Neither reference checks
# it, since the workspace does not require signatures, and both compare signed
# calls by what was signed, so one value serves every call.
SIG = "ed25519:fuzz:" + "A" * 86 + "=="

# Digits outside ASCII. Python's str.isdigit() and int() read them as numbers;
# neither reference may.
UNICODE_DIGITS = ("١", "²", "٢٣", "１２")

# Values a param of the wrong type or shape takes. 2**53 lies past the
# canonical number range, so it is refused at ingress.
JUNK = (None, 0, 7, -1, True, False, "", "x", "human:a", [], ["x"], [1, 2], {},
        {"a": 1}, [{}], [None], {"kind": 5, "assignee": 3}, "__proto__",
        "constructor", 2 ** 53, *UNICODE_DIGITS)


def _type_of(uri: str) -> str:
    """The participant type a URI's scheme names."""
    return uri.split(":", 1)[0]


# An auth_time far in the future: the token is fresh whenever the run happens.
# Step-up measures a token's age on the wall clock, so a stale one would
# answer with an age that differs between the two references' runs; its
# refusal is left to the unit tests.
FRESH = 4102444800


class Recorder:
    """Drive the Python reference and record the envelopes it accepts."""

    def __init__(self, seed: int, steps: int, config: Config):
        self.rnd = random.Random(seed)
        self.config = config
        self.coord = Coordinator(CoordinatorOptions(**coordinator_options(config)))
        self.envelopes: list[dict] = []
        self.dispatched: list[dict] = []
        self.responses: list[dict] = []
        self.states: list[dict] = []
        # Envelopes sent with a `sig`, so a later step can send one again.
        self.signed: list[dict] = []
        self.tasks: list[str] = []
        self.handoffs: list[str] = []
        self.snapshots: list[str] = []
        self.whispers: list[str] = []
        self.delibs: list[str] = []
        # The artefact last put under review for a task, so an override can
        # patch the document that is actually there.
        self.artefacts: dict[str, dict] = {}
        # The key each participant signs with now, and how many keys it has
        # been given, so each new key has a kid of its own.
        self.keys: dict[str, tuple[object, dict]] = {}
        self.issued: Counter[str] = Counter()
        self.sent: Counter[str] = Counter()
        self.ok: Counter[str] = Counter()
        self._n = 0
        self._bootstrap()
        for _ in range(steps):
            self._step()

    # -- keys and signatures -------------------------------------------------

    def _key(self, uri: str) -> tuple[object, dict]:
        """The key `uri` signs with now: its first, until it is given another."""
        if uri not in self.keys:
            key = derive_private_key(uri)
            self.keys[uri] = (key, public_jwk(uri, key))
        return self.keys[uri]

    def _next_key(self, uri: str) -> tuple[object, dict]:
        """A key `uri` has not held, with a kid of its own."""
        self.issued[uri] += 1
        name = f"{uri}#{self.issued[uri]}"
        key = derive_private_key(name)
        return key, public_jwk(name, key)

    def _sign(self, env: dict) -> None:
        """Sign a call as the configuration has it, and now and then badly."""
        rnd = self.rnd
        if not self.config.signed:
            # A call is signed when it carries a `sig` string, checked or not
            # (SPECIFICATION 10.1), so its copies meet the signed-copy rules.
            if rnd.random() < 0.1:
                env["sig"] = SIG
                self.signed.append(json.loads(json.dumps(env)))
            return
        params = env.get("params")
        sender = params.get("from") if isinstance(params, dict) else None
        if not isinstance(sender, str):
            return
        if _type_of(sender) == "agent" and self.config.policy == "humans" and rnd.random() < 0.7:
            return
        if rnd.random() < 0.06:
            # The time a key is chosen by: long before any key was given, or
            # written in digits outside ASCII.
            params["ts"] = rnd.choice(["2000-01-01T00:00:00.000Z",
                                       "２０２３-11-14T22:13:21.000Z"])
        roll = rnd.random()
        if roll < 0.04:
            return
        key, jwk = self._key(sender)
        kid = jwk["kid"]
        if roll < 0.07:
            key = self._key(rnd.choice([u for u in (*HUMANS, *AGENTS) if u != sender]))[0]
        elif roll < 0.1:
            kid = "k-unknown"
        try:
            sig = sign(canonicalize({k: v for k, v in env.items() if k != "sig"}), key, kid)
        except (TypeError, ValueError):
            return
        if roll < 0.13:
            head, body = sig.rsplit(":", 1)
            sig = f"{head}:{'B' if body[0] == 'A' else 'A'}{body[1:]}"
        elif roll < 0.15:
            sig = rnd.choice([f"ed25519:{kid}", f"webauthn:{kid}:AAAA", f"rsa:{kid}:AAAA"])
        env["sig"] = sig
        self.signed.append(json.loads(json.dumps(env)))

    def _token(self, uri: str, faithful: bool = False) -> str:
        """A stub token for `uri`: one naming it and binding its key, or, now
        and then, one naming another participant, naming none, binding another
        key, naming its key by the nonce, or carrying no auth_time or a weaker
        acr."""
        jwk = self._key(uri)[1]
        claims: dict = {"iss": "https://stub.example", "sub": "sub-" + uri, "aud": "chap",
                        "chap_participant_uri": uri, "auth_time": FRESH, "acr": "urn:acr:mfa",
                        "cnf": {"jwk": jwk}}
        if faithful:
            return "stub." + json.dumps(claims, separators=(",", ":"))
        roll = self.rnd.random()
        if roll < 0.05:
            return "not-a-stub-token"
        if roll < 0.12:
            claims["chap_participant_uri"] = self.rnd.choice([*HUMANS, *NEWCOMERS])
        elif roll < 0.18:
            del claims["chap_participant_uri"]
        elif roll < 0.24:
            claims["cnf"] = {"jwk": self._key(self.rnd.choice(AGENTS))[1]}
        elif roll < 0.32:
            del claims["cnf"]
            claims["nonce"] = jwk_thumbprint(jwk)
        elif roll < 0.36:
            del claims["auth_time"]
        elif roll < 0.4:
            claims["acr"] = "urn:acr:pwd"
        return "stub." + json.dumps(claims, separators=(",", ":"))

    def _join_params(self, uri: str, faithful: bool = False) -> dict:
        """What `uri` joins with: its type, and its key where members sign,
        bound by a token for a person where the workspace verifies people."""
        params: dict = {"type": _type_of(uri)}
        if self.config.signed:
            params["jwks"] = {"keys": [self._key(uri)[1]]}
            if self.config.oidc and _type_of(uri) == "human":
                params["oidc_token"] = self._token(uri, faithful)
        return params

    # -- sending -------------------------------------------------------------

    def _send(self, method: str, actor: object, **params) -> dict:
        self._n += 1
        env = {"jsonrpc": "2.0", "id": f"e{self._n}", "method": method,
               "params": {"workspace": "w", "from": actor, **params}}
        self._sign(env)
        return self._send_envelope(method, env)

    def _send_params(self, method: str, params: object) -> dict:
        """Send params exactly as given, workspace and from included or not."""
        self._n += 1
        env = {"jsonrpc": "2.0", "id": f"e{self._n}", "method": method}
        if params is not ...:
            env["params"] = params
            self._sign(env)
        return self._send_envelope(method, env)

    def _send_envelope(self, method: str, env: object) -> dict:
        """Send an envelope exactly as given, whether or not it is one."""
        # Record what the client sent, before dispatch could touch it. A handler
        # that writes into params would otherwise be replayed into the other
        # reference, and the two would agree on a value only one of them had
        # produced. Keeping the sent copy also lets the recorded envelope be
        # compared with the dispatched one, which is the invariant below.
        sent = json.loads(json.dumps(env))
        resp = self.coord.dispatch(env)
        self.dispatched.append(env)
        self.envelopes.append(sent)
        self.responses.append(resp)
        self.states.append(project(self.coord))
        self.sent[method] += 1
        if "result" in resp:
            self.ok[method] += 1
        return resp

    def recording_matches_what_was_sent(self) -> list[str]:
        """Methods whose envelope the coordinator altered during dispatch.

        A coordinator records what it received. Anything here is a handler
        writing into the request, which makes the recorded envelope differ
        from the signed one and makes this fuzzer replay one reference's
        mutation into the other.
        """
        return [sent["method"]
                for sent, after in zip(self.envelopes, self.dispatched)
                if sent != after]

    def _bootstrap(self) -> None:
        cfg = self.config
        create: dict = {"profiles": [*PROFILES,
                                     *(["security-signed/1.0"] if cfg.signed else []),
                                     *(["identity-oidc/1.0"] if cfg.oidc else [])]}
        if cfg.signed:
            create["signature_policy"] = cfg.policy
        if cfg.join_policy != "open":
            create["join_policy"] = cfg.join_policy
        if cfg.oidc:
            # A minimum acr left out, given as null, which is none, or given.
            create["step_up_window_sec"] = 300
            min_acr = self.rnd.choice([..., None, "urn:acr:mfa"])
            if min_acr is not ...:
                create["min_acr"] = min_acr
        self._send("workspace.create", "human:a", **create)
        # The first member takes the admin role, and invites the others where
        # the workspace admits by invitation.
        self._send("participant.join", "human:a", role="admin",
                   **self._join_params("human:a", faithful=True))
        for uri in ("human:c", *AGENTS):
            if cfg.join_policy == "invite":
                self._send("workspace.invite", "human:a", uri=uri, type=_type_of(uri))
            self._send("participant.join", uri, **self._join_params(uri, faithful=True))

    def _task(self) -> str | None:
        return self.rnd.choice(self.tasks) if self.tasks else None

    def _result(self, r: dict, key: str) -> str | None:
        return r.get("result", {}).get(key) if "result" in r else None

    # -- what the workspace can legally be asked to do right now -----------

    def _by_state(self, *states: str) -> list[str]:
        tasks = self.coord.get_workspace("w").tasks
        return [t for t in self.tasks if t in tasks and tasks[t].state in states]

    def _open_handoffs(self) -> list[str]:
        hs = self.coord.get_workspace("w").handoffs
        return [h for h in self.handoffs if h in hs and hs[h].state == "proposed"]

    def _unanswered_whispers(self) -> list[str]:
        ws = self.coord.get_workspace("w").whispers
        return [w for w in self.whispers
                if w in ws and ws[w].state == "pending"]

    def _open_delibs(self) -> list[str]:
        ds = self.coord.get_workspace("w").deliberations
        return [d for d in self.delibs
                if d in ds and ds[d].state == "open" and self._can_vote(d)]

    def _can_vote(self, did: str) -> list[str]:
        """Participants of this deliberation who have not voted yet."""
        d = self.coord.get_workspace("w").deliberations[did]
        voted = {v.get("voter") for v in (d.votes or [])}
        return [p for p in (d.participants or []) if p not in voted and p in HUMANS]

    def _paused_participants(self) -> list[str]:
        members = self.coord.get_workspace("w").members
        return [u for u in (*HUMANS, *AGENTS) if u in members and members[u].paused]

    def _member(self, uri: str) -> bool:
        return uri in self.coord.get_workspace("w").members

    def _reviewers(self, tid: str) -> list[str]:
        """Who the open review on this task is addressed to."""
        task = self.coord.get_workspace("w").tasks.get(tid)
        if task is None or task.review is None:
            return list(HUMANS)
        named = [r for r in (task.review.requested_to or []) if r in HUMANS]
        return named or list(HUMANS)

    def _menu(self) -> list[str]:
        """Actions weighted towards what the current state admits.

        A generator that picks a random task for every decision spends its
        budget comparing error responses. Weighting towards legal transitions
        puts the review path, which is the profile CHAP is judged on, in the
        middle of the run rather than at its edges. A fixed share of misses
        keeps the refusals covered.
        """
        live = self._by_state("created", "in_progress")
        menu = ["create", "create", "update", "complete", "complete",
                "whisper", "deliberate", "snapshot", "miss", "pause_participant",
                "ceiling", "leave_and_rejoin", "rejoin", "malformed", "malformed", "malformed",
                "invite", "newcomer"]
        if self.config.signed:
            menu += ["rotate", "revoke"]
        if self._paused_participants():
            menu += ["resume_participant"] * 2
        if self.signed:
            menu += ["signed_copy"]
        if live:
            menu += ["review"] * 4
        if self._by_state("review_requested"):
            menu += ["decide"] * 8 + ["acknowledge"] * 3 + ["amend"]
        if self._by_state("paused"):
            menu += ["resume"] * 3
        if live:
            menu += ["pause", "handoff", "route", "depth", "auto", "escalate",
                     "supersede", "cancel"]
        if self._open_handoffs():
            menu += ["resolve_handoff"] * 3
        if self._unanswered_whispers():
            menu += ["answer_whisper"] * 2
        if self._open_delibs():
            menu += ["vote"] * 2
        if self.snapshots:
            menu += ["rollback"] * 2
        return menu

    # -- the documents under review, and the patches applied to them -------

    def _artefact(self) -> dict:
        """A document with arrays and nesting, so a patch has somewhere to go,
        and nulls in it, which both references keep as they were sent."""
        rnd = self.rnd
        return {
            "text": rnd.choice(["draft", "second pass"]),
            "items": [{"id": i, "note": rnd.choice([f"n{i}", f"n{i}", None])}
                      for i in range(rnd.randint(1, 3))],
            "meta": {"tags": rnd.sample(["tone", "fact", "legal"], rnd.randint(1, 3)),
                     "score": rnd.randint(0, 9), "owner": None},
        }

    def _patch(self, doc: dict) -> list[dict]:
        """A JSON Patch against `doc`, drawn from the whole operation set.

        The index tokens matter: `1e1` and `1_0` are the tokens behind #103,
        where one reference parsed them as ten and the other refused. They are
        generated deliberately so a divergence of that class shows up here.
        """
        rnd = self.rnd
        last = len(doc["items"]) - 1
        choices: list[list[dict]] = [
            [{"op": "replace", "path": "/text", "value": "corrected"}],
            [{"op": "add", "path": "/items/-", "value": {"id": 9, "note": "added"}}],
            [{"op": "remove", "path": f"/items/{last}"}],
            [{"op": "replace", "path": f"/items/{rnd.randint(0, last)}/note", "value": "fixed"}],
            [{"op": "move", "from": "/meta/score", "path": "/score"}],
            [{"op": "copy", "from": "/text", "path": "/meta/original"}],
            [{"op": "test", "path": "/text", "value": doc["text"]},
             {"op": "replace", "path": "/meta/score", "value": 5}],
            [{"op": "add", "path": "/meta/tags/0", "value": "urgent"},
             {"op": "remove", "path": "/meta/tags/-"}],
            # Index tokens that are not plain digits. Both references must
            # refuse them, and must refuse them the same way.
            [{"op": "replace", "path": "/items/1e1/note", "value": "ten"}],
            [{"op": "replace", "path": "/items/1_0/note", "value": "ten"}],
            [{"op": "add", "path": "/items/01", "value": {"id": 1}}],
            [{"op": "replace", "path": f"/items/{rnd.choice(UNICODE_DIGITS)}/note", "value": "one"}],
            [{"op": "remove", "path": f"/items/{rnd.choice(UNICODE_DIGITS)}"}],
        ]
        return rnd.choice(choices)

    def _rule(self) -> str:
        """A review or deliberation rule, now and then with a count written in
        digits outside ASCII."""
        if self.rnd.random() < 0.1:
            return "quorum:" + self.rnd.choice(UNICODE_DIGITS)
        return self.rnd.choice(["any_one_approves", "all_approve", "quorum:2"])

    def _under_review(self) -> dict:
        """What a review is opened on: as a rule a document, now and then a
        null artefact or none at all."""
        roll = self.rnd.random()
        if roll < 0.1:
            return {"artefact": None}
        if roll < 0.2:
            return {}
        return {"artefact": self._artefact()}

    def _step(self) -> None:  # noqa: C901 - a menu of actions
        rnd = self.rnd
        a = rnd.choice(self._menu())
        if a == "create" or not self.tasks:
            self._create()
            return
        human = rnd.choice(HUMANS)

        if a == "miss":
            # A call aimed at a task that is in the wrong state for it, so the
            # refusals stay compared as well as the successes.
            self._miss()
            return
        if a == "malformed":
            self._malformed()
            return
        if a == "signed_copy":
            # A signed call sent again unchanged: refused -32075 where it took
            # effect, answered with the recorded refusal where it was refused,
            # and evaluated again where its refusal was not recorded.
            env = rnd.choice(self.signed)
            self._send_envelope(env["method"], json.loads(json.dumps(env)))
            return
        if a == "decide":
            tid = rnd.choice(self._by_state("review_requested"))
            human = rnd.choice(self._reviewers(tid))
            if rnd.random() < 0.15:
                # The author of the work, or another agent: refused where the
                # review needs a person or the decider made the work.
                human = rnd.choice([self._assignee(tid), *AGENTS])
            kind = rnd.choice(["decide.approve", "decide.reject",
                               "decide.override", "decide.override",
                               "abstain.declare"])
            if kind == "abstain.declare":
                self._send(kind, human, task_id=tid, reason="conflict of interest")
            elif kind == "decide.override":
                doc = self.artefacts.get(tid) or {"text": "draft", "items": [{"id": 0}],
                                                  "meta": {"tags": [], "score": 0}}
                self._send(kind, human, task_id=tid, rationale="r",
                           diff=self._patch(doc),
                           tags=rnd.sample(["tone", "fact"], rnd.randint(0, 2)))
            else:
                p: dict = {"task_id": tid, "comment": "c"}
                if kind == "decide.reject":
                    p["request_revision"] = rnd.random() < 0.5
                self._send(kind, human, **p)
            return
        if a == "amend":
            # A review asked for again while it is open: by its author, which
            # amends it, or by anyone else, on a document, a null or nothing.
            tid = rnd.choice(self._by_state("review_requested"))
            who = rnd.choice([self._assignee(tid), self._assignee(tid), *HUMANS])
            self._send("review.request", who, task_id=tid, to=rnd.sample(HUMANS, rnd.randint(1, 2)),
                       **self._under_review())
            return
        if a == "invite":
            self._invite()
            return
        if a == "newcomer":
            self._newcomer()
            return
        if a == "rotate":
            # A member signs a new key in with the key it holds.
            who = rnd.choice([*HUMANS, *AGENTS])
            new = self._next_key(who)
            old_kid = self._key(who)[1]["kid"] if rnd.random() < 0.85 else "k-unknown"
            r = self._send("participant.rotate_key", who, old_kid=old_kid, new_jwk=new[1])
            if "result" in r:
                self.keys[who] = new
            return
        if a == "revoke":
            # A key revoked by an admin, or by its holder; the admin's own
            # now and then, which leaves the workspace with no admin who signs.
            target = rnd.choice(["human:c", "human:c", *AGENTS, *AGENTS, "human:a"])
            sender = "human:a" if rnd.random() < 0.7 else rnd.choice([target, *AGENTS])
            self._send("participant.revoke_key", sender, target_uri=target,
                       kid=self._key(target)[1]["kid"], reason="lost")
            return
        if a == "acknowledge":
            # A reviewer says they have begun, at times twice in a round, and
            # now and then the author or an agent tries: taken once a round
            # from whoever may decide the review, and refused otherwise.
            tid = rnd.choice(self._by_state("review_requested"))
            who = rnd.choice(self._reviewers(tid))
            if rnd.random() < 0.15:
                who = rnd.choice([self._assignee(tid), *AGENTS])
            self._send("review.acknowledge", who, task_id=tid)
            return
        if a == "resume":
            self._send("control.resume", "human:a",
                       task_id=rnd.choice(self._by_state("paused")))
            return
        if a == "pause_participant":
            # A paused participant is given no new work, so task.create,
            # task.route, escalate.raise, control.supersede and handoff.accept
            # meet the pause on both sides of the comparison.
            self._send("control.pause", "human:a", scope="participant",
                       participant_uri=rnd.choice([*HUMANS, *AGENTS]))
            return
        if a == "resume_participant":
            self._send("control.resume", "human:a", scope="participant",
                       participant_uri=rnd.choice(self._paused_participants()))
            return
        if a == "ceiling":
            # Never below trial, the workspace's default mode, so task.create
            # keeps working while a production successor meets the ceiling.
            self._send("control.set_mode_ceiling", "human:a", reason="adjust",
                       new_ceiling=rnd.choice(["trial", "production", "production"]))
            return
        if a == "leave_and_rejoin":
            # A member leaves and joins again at once, which also lifts a pause.
            who = rnd.choice([*HUMANS, *AGENTS])
            self._send("participant.leave", who)
            self._send("participant.join", who, **self._join_params(who))
            return
        if a == "rejoin":
            # A member joins again: with nothing new, with a key it does not
            # hold, under the other type, or carrying credentials no verifier
            # here reads, which the log keeps as their digests.
            who = rnd.choice([*HUMANS, *AGENTS])
            kind = "human" if who.startswith("human:") else "agent"
            roll = rnd.random()
            if roll < 0.2:
                self._send("participant.join", who, type=kind)
            elif roll < 0.35:
                self._send("participant.join", who, type=kind, jwks={"keys": [
                    {"kty": "OKP", "crv": "Ed25519", "kid": f"k{rnd.randint(0, 3)}", "x": "AAAA"}]})
            elif roll < 0.5:
                self._send("participant.join", who, **self._join_params(who))
            elif roll < 0.65:
                self._send("participant.join", who, type="agent" if kind == "human" else "human")
            elif roll < 0.75:
                self._send("participant.join", who, type=kind, role="admin")
            elif roll < 0.85 and self.config.oidc:
                # A presentation whose subject binds the member's key, or
                # another holder's.
                self._send("participant.join", who, type=kind, vc_presentation={"subject": {
                    "holder": rnd.choice([f"did:example:{who}", "did:example:x"]),
                    "cnf_jwk": self._key(rnd.choice([who, *AGENTS]))[1]}})
            else:
                self._send("participant.join", who, type=kind, oidc_token=f"token-{rnd.randint(0, 3)}",
                           vc_presentation={"holder": "did:example:x", "n": rnd.randint(0, 3)})
            return
        if a == "resolve_handoff":
            hid = rnd.choice(self._open_handoffs())
            who = self.coord.get_workspace("w").handoffs[hid].recipient
            roll = rnd.random()
            if roll < 0.2:
                # Refused since #151: an explicit empty acceptance is an audit
                # record of a transfer that did not happen.
                self._send("handoff.accept", who, handoff_id=hid, accepted_task_ids=[])
            elif roll < 0.7:
                self._send("handoff.accept", who, handoff_id=hid)
            else:
                self._send("handoff.decline", who, handoff_id=hid, reason="not mine")
            return
        if a == "answer_whisper":
            wid = rnd.choice(self._unanswered_whispers())
            asked = self.coord.get_workspace("w").whispers[wid].askee or list(HUMANS)
            self._send("whisper.answer", rnd.choice(asked), whisper_id=wid,
                       answer=rnd.choice(["yes", "no"]))
            return
        if a == "vote":
            did = rnd.choice(self._open_delibs())
            self._send("deliberate.vote", rnd.choice(self._can_vote(did)),
                       deliberation_id=did, vote=rnd.choice(["yea", "nay", "abstain"]))
            if rnd.random() < 0.4:
                self._send("deliberate.close", human, deliberation_id=did)
            return
        if a == "rollback":
            roll = rnd.random()
            if roll < 0.2:
                params = {"what_to_restore": []}      # refused since #152
            elif roll < 0.6:
                params = {}
            else:
                params = {"what_to_restore":
                          rnd.sample(["members", "mode_ceiling"], rnd.randint(1, 2))}
            self._send("control.rollback", human,
                       to_snapshot_artefact_id=rnd.choice(self.snapshots), **params)
            return
        if a == "snapshot":
            slices = ["members", "open_tasks", "mode_ceiling", "policy", "audit"]
            roll = rnd.random()
            if roll < 0.15:
                params = {"include": []}              # refused since #152
            elif roll < 0.45:
                params = {}
            else:
                params = {"include": rnd.sample(slices, rnd.randint(1, len(slices)))}
            snapshot_id = self._result(self._send("control.snapshot", human, **params),
                                       "snapshot_artefact_id")
            if snapshot_id:
                self.snapshots.append(snapshot_id)
            return

        live = self._by_state("created", "in_progress")
        if not live:
            self._create()
            return
        tid = rnd.choice(live)
        if a == "update":
            self._send("task.update", self._assignee(tid), task_id=tid,
                       state=rnd.choice(["in_progress", "in_progress", "declined"]))
        elif a == "review":
            under = self._under_review()
            to = rnd.sample(HUMANS, rnd.randint(1, 2))
            if rnd.random() < 0.15:
                # An agent, perhaps the assignee itself: refused where the
                # task requires a person's review.
                to.append(rnd.choice(AGENTS))
            r = self._send("review.request", self._assignee(tid), task_id=tid,
                           to=to, rule=self._rule(), **under)
            if "result" in r and isinstance(under.get("artefact"), dict):
                self.artefacts[tid] = under["artefact"]
            else:
                self.artefacts.pop(tid, None)
        elif a == "complete":
            # As a rule with an output, now and then with none or a null one.
            roll = rnd.random()
            output = {} if roll < 0.08 else {"output": None} if roll < 0.12 else {"output": self._artefact()}
            if isinstance(output.get("output"), dict):
                self.artefacts[tid] = output["output"]
            else:
                self.artefacts.pop(tid, None)
            self._send("task.complete", self._assignee(tid), task_id=tid, **output)
        elif a == "pause":
            self._send("control.pause", "human:a", task_id=tid, reason="hold")
        elif a == "escalate":
            new_task = {"kind": "k", "input": {}, "assignee": AGENTS[0]}
            if rnd.random() < 0.3:
                new_task["mode"] = rnd.choice(["shadow", "trial", "production"])
            r = self._send("escalate.raise", "human:a", original_task_id=tid, reason="up",
                           new_task=new_task)
            nt = self._result(r, "new_task_id")
            if nt:
                self.tasks.remove(tid)
                self.tasks.append(nt)
        elif a == "supersede":
            successor = {"kind": "v2", "input": {}, "assignee": AGENTS[0]}
            if rnd.random() < 0.3:
                successor["review_required"] = rnd.choice([True, False])
            r = self._send("control.supersede", "human:a", task_id=tid, reason="redo",
                           successor_task=successor)
            nt = self._result(r, "new_task_id")
            if nt:
                self.tasks.remove(tid)
                self.tasks.append(nt)
        elif a == "cancel":
            if "result" in self._send("control.cancel", "human:a", task_id=tid, reason="no"):
                self.tasks.remove(tid)
        elif a == "handoff":
            r = self._send("handoff.propose", self._assignee(tid),
                           to=rnd.choice(HUMANS), tasks=[{"task_id": tid}])
            hid = self._result(r, "handoff_id")
            if hid:
                self.handoffs.append(hid)
        elif a == "whisper":
            r = self._send("whisper.ask", self._assignee(tid), task_id=tid,
                           to=[rnd.choice(HUMANS)], question=f"q{self._n}",
                           deadline_ms=rnd.choice([0, 600_000]),
                           default_if_lapsed=rnd.choice(["no", None]))
            wid = self._result(r, "whisper_id")
            if wid:
                self.whispers.append(wid)
        elif a == "deliberate":
            r = self._send("deliberate.open", human, to=rnd.sample(HUMANS, rnd.randint(1, 2)),
                           rule=self._rule(), question=f"Q{self._n}", task_id=tid)
            did = self._result(r, "deliberation_id")
            if did:
                self.delibs.append(did)
        elif a == "route":
            self._send("task.route", "human:a", task_id=tid, candidates=list(AGENTS))
        elif a == "depth":
            self._send("review.depth", "human:a", task_id=tid,
                       artefact_routing_hints={"criticality": "high"})
        elif a == "auto":
            self._send("escalate.auto", "human:a", task_id=tid,
                       default_escalation_target=rnd.choice(HUMANS))

    def _invite(self) -> None:
        """An invitation, as a rule from the admin and now and then from a
        member who is not one: for a newcomer, an address scope or a member,
        with a role, keys or a token subject. Keys an invitation registers or
        pins are the ones the participant signs with from then on."""
        rnd = self.rnd
        sender = "human:a" if rnd.random() < 0.8 else rnd.choice(["human:c", *AGENTS])
        uri = rnd.choice([*NEWCOMERS, *NEWCOMERS, *SCOPES, *HUMANS, *AGENTS])
        params: dict = {"uri": uri, "type": _type_of(uri)}
        if rnd.random() < 0.1:
            params["type"] = rnd.choice(["human", "agent", "service"])
        if rnd.random() < 0.3:
            params["role"] = rnd.choice(["admin", "admin", "reviewer"])
        new = None
        if rnd.random() < 0.5:
            new = self._next_key(uri)
            params["jwks"] = {"keys": [new[1]]}
        if self.config.oidc and rnd.random() < 0.3:
            params["oidc_sub"] = "sub-" + uri
        r = self._send("workspace.invite", sender, **params)
        if new is not None and "result" in r:
            self.keys[uri] = new

    def _newcomer(self) -> None:
        """A participant who is not a member joins, invited or not, as the
        configuration has members join, and now and then asking for the admin
        role or bringing no key."""
        rnd = self.rnd
        uri = rnd.choice([*NEWCOMERS, *NEWCOMERS, *SCOPES])
        params = self._join_params(uri)
        roll = rnd.random()
        if roll < 0.2:
            params["role"] = "admin"
        elif roll < 0.3:
            params.pop("jwks", None)
        self._send("participant.join", uri, **params)

    def _create(self) -> None:
        """A new task, a share of them requiring review, so the successors
        that escalate.raise and control.supersede make carry it too."""
        rnd = self.rnd
        extra = {"review_required": True} if rnd.random() < 0.25 else {}
        r = self._send("task.create", "human:a", kind=rnd.choice(["a", "b"]),
                       input={"n": self._n, "context": None}, assignee=rnd.choice(AGENTS), **extra)
        tid = self._result(r, "task_id")
        if tid:
            self.tasks.append(tid)

    def _miss(self) -> None:
        """One call the current state refuses, so error paths stay compared."""
        rnd = self.rnd
        tid = self._task()
        if tid is None:
            return
        rnd.choice([
            lambda: self._send("decide.approve", rnd.choice(HUMANS), task_id=tid,
                               comment="c"),
            lambda: self._send("control.resume", "human:a", task_id=tid),
            lambda: self._send("task.update", AGENTS[0], task_id=tid, state="paused"),
            lambda: self._send("abstain.declare", rnd.choice(HUMANS), task_id=tid,
                               reason="not mine"),
            lambda: self._send("review.acknowledge", rnd.choice(HUMANS), task_id=tid),
            lambda: self._send("handoff.accept", rnd.choice(HUMANS),
                               handoff_id="hnd_absent"),
            lambda: self._send("whisper.answer", rnd.choice(HUMANS),
                               whisper_id="whp_absent", answer="yes"),
            # Calls from a non-member to methods held to the membership floor,
            # and from a caller whose `from` is not a string.
            lambda: self._send("task.route", "human:outsider", task_id=tid,
                               candidates=list(AGENTS)),
            lambda: self._send("participant.leave", "human:outsider"),
            lambda: self._send("participant.leave", None),
            # task.update opens no review, and a range past the end of the log
            # still reports where the next read starts.
            lambda: self._send("task.update", self._assignee(tid), task_id=tid,
                               state="review_requested"),
            lambda: self._send("audit.read", "human:a",
                               range={"from_seq": 0, "to_seq": 100_000}),
            lambda: self._send("task.route", ["human:a"], task_id=tid,
                               candidates=list(AGENTS)),
        ])()

    def _plausible(self, name: str) -> object:
        """A well-formed value for a param, drawn from the workspace's state."""
        rnd = self.rnd
        members = [*HUMANS, *AGENTS]
        known = {
            "task_id": self.tasks, "original_task_id": self.tasks,
            "handoff_id": self.handoffs, "whisper_id": self.whispers,
            "deliberation_id": self.delibs, "to_snapshot_artefact_id": self.snapshots,
            "snapshot_artefact_id": self.snapshots, "snapshot_id": self.snapshots,
        }
        if name in known:
            return rnd.choice(known[name] or ["absent"])
        if name in ("to", "participants", "candidates"):
            return rnd.sample(members, 2)
        if name in ("assignee", "target_uri", "participant_uri", "uri",
                    "suggested_target", "default_escalation_target"):
            return rnd.choice([*members, *NEWCOMERS])
        if name in ("jwks",):
            return {"keys": [self._key(rnd.choice(members))[1]]}
        if name == "new_jwk":
            return self._next_key(rnd.choice(members))[1]
        if name in ("old_kid", "kid"):
            return self._key(rnd.choice(members))[1]["kid"]
        if name == "oidc_token":
            return self._token(rnd.choice(members))
        if name == "vc_presentation":
            return {"subject": {"holder": "did:example:x", "cnf_jwk": self._key(rnd.choice(members))[1]}}
        if name == "deadline":
            return rnd.choice(["2030-01-01T00:00:00.000Z",
                               "٢٠٣٠-01-01T00:00:00.000Z"])
        choices = {
            "type": ["human", "agent", "service"], "state": ["in_progress", "declined", "completed"],
            "rule": ["any_one_approves", "quorum:2", "all_approve", "quorum:٢"],
            "scope": ["task", "participant", "workspace"],
            "vote": ["yea", "nay", "abstain"],
            "mode": ["shadow", "trial", "production"],
            "new_ceiling": ["trial", "production"], "mode_ceiling": ["trial", "production"],
            "signature_policy": ["all", "humans"], "join_policy": ["open", "invite"],
            "role": ["admin", "reviewer"],
        }
        if name in choices:
            return rnd.choice(choices[name])
        if name in ("input", "artefact", "output", "routing_hints", "artefact_routing_hints",
                    "capabilities", "filter", "range", "receipt", "weights", "veto"):
            return {"k": rnd.randint(0, 3)}
        if name in ("new_task", "successor_task"):
            return {"kind": "k", "input": {}, "assignee": AGENTS[0]}
        if name in ("tags", "policy_refs", "include", "what_to_restore", "context_links",
                    "accepted_task_ids", "profiles", "scopes"):
            return ["members"]
        if name == "diff":
            return [{"op": "replace", "path": "/text", "value": "y"}]
        if name in ("review_required", "request_revision", "intent_preserved", "veto_invoked"):
            return rnd.choice([True, False])
        if name in ("deadline_ms", "weight", "step_up_window_sec", "from_seq", "to_seq"):
            return rnd.randint(0, 5)
        if name == "tasks":
            return [{"task_id": rnd.choice(self.tasks or ["absent"])}]
        if name == "options":
            return [{"id": "a"}, {"id": "b"}]
        return rnd.choice(["text", "a note"])

    def _malformed(self) -> None:
        """A call with a param missing, or of the wrong type or shape.

        It starts from params a handler could accept and then breaks one or
        two of them, the workspace and the sender included now and then, so
        the checks a method makes before it changes anything are compared,
        and so is what it leaves behind when it refuses.
        """
        rnd = self.rnd
        method = rnd.choice(sorted(METHOD_PARAMS))
        names = METHOD_PARAMS[method]
        if rnd.random() < 0.05:
            # Something that is not a JSON-RPC call, or a call with no id.
            self._n += 1
            self._send_envelope("(not a call)", rnd.choice([
                None, [], "x", 5, {}, {"jsonrpc": "1.0", "id": f"e{self._n}", "method": method},
                {"jsonrpc": "2.0", "id": f"e{self._n}"},
                {"jsonrpc": "2.0", "id": f"e{self._n}", "method": rnd.choice(JUNK)},
                {"jsonrpc": "2.0", "method": "workspace.describe", "params": {"workspace": "w"}},
            ]))
            return
        if rnd.random() < 0.05:
            # params that are not an object at all, or absent.
            self._send_params(method, rnd.choice([..., None, [], "x", 5]))
            return
        params: dict = {"workspace": "w", "from": rnd.choice([*HUMANS, *AGENTS])}
        for name in names:
            if rnd.random() < 0.7:
                params[name] = self._plausible(name)
        targets = list(names) + ["workspace", "from"] * (1 if names else 3)
        for name in rnd.sample(targets, min(len(targets), rnd.randint(1, 2))):
            if rnd.random() < 0.2:
                params.pop(name, None)
            else:
                params[name] = rnd.choice(JUNK)
        self._send_params(method, params)

    def _assignee(self, tid: str) -> str:
        t = self.coord.get_workspace("w").tasks.get(tid)
        return t.assignee if t else AGENTS[0]

    def chain_head(self) -> str | None:
        return self.coord.get_workspace("w").chain_head



# Past every deadline the fuzzer sets, so a store replay lapses each whisper
# still pending, outside any call.
_LAPSE_AT = "2100-01-01T00:00:00.000Z"


def _replay_ts(envelopes: list[dict], config: Config, store: str | None = None,
               cross: str | None = None, rough: list[dict] | None = None) -> dict:
    payload = json.dumps({"options": {"defaultProfiles": PROFILES, "stubVerifiers": config.oidc,
                                      "readMembership": config.read_membership},
                          "workspace": "w", "envelopes": envelopes,
                          **({"store": store, "lapse_at": _LAPSE_AT} if store else {}),
                          **({"cross": cross} if cross else {}),
                          **({"rough": rough} if rough is not None else {})})
    proc = subprocess.run(
        ["npx", "tsx", str(_REPLAY)],
        input=payload, capture_output=True, text=True, cwd=str(_ROOT))
    if proc.returncode != 0:
        raise RuntimeError(f"replay.ts failed:\n{proc.stderr}")
    return json.loads(proc.stdout)


def _plain(coord: Coordinator) -> str:
    out = {}
    for ws_id, ws in coord.workspaces.items():
        data = asdict(ws)
        data["audit"] = [e.to_dict() for e in ws.audit]
        out[ws_id] = data
    return json.dumps(out, sort_keys=True, default=str)


# The fields of the coordinators' own records whose null is a value: a
# whisper's default_if_lapsed is the default a lapse applies.
_KEPT_NULLS = {"whispers": ("default_if_lapsed",)}
# The records nested in each collection's objects, by field.
_NESTED = {"members": ("keys",), "tasks": ("history",), "deliberations": ("votes",),
           "handoffs": ("tasks",)}


def _own(record: dict, keep: tuple[str, ...] = ()) -> dict:
    """A record of a coordinator's own, with the fields holding null left out
    but those named in `keep`. Both coordinators read such a null as a field
    left out."""
    return {k: v for k, v in record.items() if v is not None or k in keep}


def _shape(name: str, item: dict) -> dict:
    """One object of a collection with its own nulls left out, and those of
    the records nested in it. What a client sent, an input, an output, an
    artefact or a patch, keeps its nulls, and so does the log."""
    out = _own(item, _KEPT_NULLS.get(name, ()))
    for field in _NESTED.get(name, ()):
        if isinstance(out.get(field), list):
            out[field] = [_own(x) if isinstance(x, dict) else x for x in out[field]]
    review = out.get("review") if name == "tasks" else None
    if isinstance(review, dict):
        review = _own(review)
        if isinstance(review.get("decisions"), list):
            review["decisions"] = [_own(d) if isinstance(d, dict) else d for d in review["decisions"]]
        out["review"] = review
    return out


def project(coord: Coordinator) -> dict:
    """Every workspace's state, by id, as replay.ts projects the TypeScript
    coordinator's, so the two can be compared after every call."""
    return {ws_id: _project_workspace(coord, ws_id) for ws_id in sorted(coord.workspaces)}


def _project_workspace(coord: Coordinator, workspace: str) -> dict | None:
    """One workspace's state in the shape replay.ts projects the TypeScript
    workspace to.

    The coordinators hold the same state in their own types: a Python history
    entry names its sender `from_`, a route decision keeps its extra fields
    apart, and an unset list of tags or accepted tasks is empty where
    TypeScript leaves it out. This maps those differences away, leaves out the
    nulls both read as no value, and keeps every other difference.
    """
    ws = coord.workspaces.get(workspace)
    if ws is None:
        return None
    data = json.loads(json.dumps(asdict(ws), default=str))
    out: dict = _own({k: data[k] for k in (
        "id", "created", "state", "profiles", "mode", "mode_ceiling",
        "routing_policy_uri", "step_up_window_sec", "min_acr", "signature_policy",
        "join_policy", "chain_head", "chain_enabled")})
    out["members"] = sorted((_shape("members", m) for m in data["members"].values()),
                            key=lambda m: str(m.get("uri")))
    tasks = []
    for t in data["tasks"].values():
        t["history"] = [{("from" if k == "from_" else k): v for k, v in h.items()}
                        for h in t.get("history") or []]
        review = t.get("review")
        if isinstance(review, dict):
            for d in review.get("decisions") or []:
                if isinstance(d, dict) and not d.get("tags"):
                    d.pop("tags", None)
        tasks.append(_shape("tasks", t))
    out["tasks"] = sorted(tasks, key=lambda t: str(t.get("id")))
    for name in ("overrides", "whispers", "deliberations", "snapshots", "invitations"):
        out[name] = sorted((_shape(name, x) for x in data[name].values()),
                           key=lambda x: str(x.get("id", x.get("uri"))))
    handoffs = []
    for h in data["handoffs"].values():
        if not h.get("accepted_task_ids"):
            h.pop("accepted_task_ids", None)
        handoffs.append(_shape("handoffs", h))
    out["handoffs"] = sorted(handoffs, key=lambda h: str(h.get("id")))
    decisions = []
    for r in data["route_decisions"].values():
        extra = r.pop("extra", None) or {}
        decisions.append(_shape("route_decisions", {**r, **extra, "kind": "route_decision"}))
    out["route_decisions"] = sorted(decisions, key=lambda r: str(r.get("id")))
    out["idempotency_keys"] = data["idempotency_keys"]
    out["audit"] = [e.to_dict() for e in ws.audit]
    return json.loads(json.dumps(out, default=str))


def _replay_py(envelopes: list[dict], store: str, config: Config) -> tuple[list[dict], list[str], bool]:
    """The Python reference on a SQLite file: responses, store errors, and
    whether a restart, before the whispers lapse and after, holds the same."""
    errors: list[str] = []

    def open_coordinator() -> tuple[SqliteStore, Coordinator]:
        sq = SqliteStore(store)
        return sq, Coordinator(CoordinatorOptions(
            **coordinator_options(config), store=sq,
            on_store_error=lambda e, r: errors.append(str(e))))

    sq, coord = open_coordinator()
    responses = [coord.dispatch(json.loads(json.dumps(e))) for e in envelopes]
    same = True
    # The whispers lapse on the restarted coordinator, so a default it lost
    # on the way shows up in the entries the lapses write.
    for lapse in (True, False):
        before = _plain(coord)
        sq.close()
        sq, coord = open_coordinator()
        same = same and before == _plain(coord)
        if lapse:
            coord.check_whisper_lapses("w", _LAPSE_AT)
    sq.close()
    return responses, errors, same


class _RoughStore:
    """A SQLite store that fails the reads and writes it is told to fail, and
    runs a hook once before its next write: another writer's call, which
    lands between this coordinator's keep-up and its write."""

    def __init__(self, path: str):
        self.inner = SqliteStore(path)
        self.fail_write = self.fail_reload = self.fail_version = False
        self.before_write = None

    def load(self):
        return self.inner.load()

    def load_one(self, id: str):
        if self.fail_reload:
            raise OSError("the fuzzer failed this reload")
        return self.inner.load_one(id)

    def version(self, id: str):
        if self.fail_version:
            raise OSError("the fuzzer failed this read")
        return self.inner.version(id)

    def apply(self, change) -> None:
        hook, self.before_write = self.before_write, None
        if hook is not None:
            hook()
        if self.fail_write:
            raise OSError("the fuzzer failed this write")
        self.inner.apply(change)

    def save(self, record) -> None:
        self.inner.save(record)

    def delete(self, id: str) -> None:
        self.inner.delete(id)

    def close(self) -> None:
        self.inner.close()


def _rough_schedule(seed: int, n: int) -> list[dict]:
    """For each call: the coordinator of two that takes it, whether the store
    fails its write, its reload or its read of the version, and whether the
    other coordinator takes the next call between this one's keep-up and its
    write. replay.ts follows the same schedule."""
    rnd = random.Random(seed * 7919 + 1)
    out = []
    for i in range(n):
        write = rnd.random() < 0.06
        out.append({"writer": rnd.randrange(2), "write": write,
                    "reload": (write and rnd.random() < 0.5) or rnd.random() < 0.02,
                    "version": rnd.random() < 0.03,
                    "race": not write and i + 1 < n and rnd.random() < 0.08})
    return out


def _rough_py(envelopes: list[dict], path: str, config: Config, schedule: list[dict]) -> list[dict]:
    """Two Python coordinators on one file, taking the calls as the schedule
    says while the store fails as it says. The answer to each call."""
    stores = [_RoughStore(path), _RoughStore(path)]
    coords = [Coordinator(CoordinatorOptions(**coordinator_options(config), store=s,
                                             on_store_error=lambda e, r: None))
              for s in stores]
    responses: list = [None] * len(envelopes)
    i = 0
    while i < len(envelopes):
        step, raced = schedule[i], []
        store = stores[step["writer"]]
        if step["race"]:
            def other(j: int = i + 1, o: int = 1 - step["writer"]) -> None:
                responses[j] = coords[o].dispatch(json.loads(json.dumps(envelopes[j])))
                raced.append(j)
            store.before_write = other
        store.fail_write, store.fail_reload, store.fail_version = (
            step["write"], step["reload"], step["version"])
        responses[i] = coords[step["writer"]].dispatch(json.loads(json.dumps(envelopes[i])))
        store.fail_write = store.fail_reload = store.fail_version = False
        store.before_write = None
        i += 2 if raced else 1
    for s in stores:
        s.close()
    return responses


def _entries(path: str) -> list:
    con = sqlite3.connect(path)
    try:
        return [(seq, json.loads(data)) for seq, data in con.execute(
            "SELECT seq, data FROM chap_entries WHERE workspace = 'w' ORDER BY seq")]
    finally:
        con.close()


def _same_files(seed: int, py_path: str, ts_path: str, config: Config, what: str) -> tuple[bool, str]:
    """The two files hold the same audit entries, and a Python coordinator
    reads the same state from each."""
    views = []
    for path in (py_path, ts_path):
        store = SqliteStore(path)
        try:
            views.append(project(Coordinator(CoordinatorOptions(
                **coordinator_options(config), store=store))))
        finally:
            store.close()
    if views[0] != views[1]:
        return False, (f"seed {seed}: {what}: the Python coordinator reads the TypeScript store apart\n"
                       f"{_state_difference(*views)}")
    py_entries, ts_entries = _entries(py_path), _entries(ts_path)
    if py_entries != ts_entries:
        at = next((i for i, (a, b) in enumerate(zip(py_entries, ts_entries)) if a != b),
                  min(len(py_entries), len(ts_entries)))
        return False, (f"seed {seed}: {what}: the stores hold different audit entries from seq {at}\n"
                       f"  python: {json.dumps(py_entries[at][1]) if at < len(py_entries) else '<none>'}\n"
                       f"  ts:     {json.dumps(ts_entries[at][1]) if at < len(ts_entries) else '<none>'}")
    return True, ""


def check_stores(seed: int, rec: "Recorder") -> tuple[bool, str]:
    """Both references on a SQLite file: nothing missed, the same entries.
    Then both again with two writers and a store that fails: the same
    answers and the same entries."""
    config = rec.config
    with tempfile.TemporaryDirectory() as tmp:
        py_path, ts_path = str(Path(tmp) / "py.db"), str(Path(tmp) / "ts.db")
        responses, errors, same = _replay_py(rec.envelopes, py_path, config)
        if responses != rec.responses:
            return False, f"seed {seed}: the Python store changed a response"
        if errors or not same:
            return False, (f"seed {seed}: the Python store "
                           f"{'failed: ' + errors[0] if errors else 'lost state across a restart'}")
        ts = _replay_ts(rec.envelopes, config, ts_path, cross=py_path)
        if ts["errors"] or not ts["restart_same"]:
            return False, (f"seed {seed}: the TypeScript store "
                           f"{'failed: ' + ts['errors'][0] if ts['errors'] else 'lost state across a restart'}")
        # Each coordinator reads the file the other wrote as the one it wrote.
        if ts["own"] != ts["cross"]:
            return False, (f"seed {seed}: the TypeScript coordinator reads the Python store apart\n"
                           f"{_state_difference(ts['own'], ts['cross'])}")
        ok, msg = _same_files(seed, py_path, ts_path, config, "one writer")
        if not ok:
            return False, msg

        schedule = _rough_schedule(seed, len(rec.envelopes))
        rough_py, rough_ts = str(Path(tmp) / "rough-py.db"), str(Path(tmp) / "rough-ts.db")
        py_answers = _rough_py(rec.envelopes, rough_py, config, schedule)
        ts_answers = _replay_ts(rec.envelopes, config, rough_ts, rough=schedule)["responses"]
        idx = _first_divergence(py_answers, ts_answers)
        if idx is not None:
            return False, (
                f"seed {seed}: two writers and store faults: responses diverge at envelope {idx}\n"
                f"  schedule: {json.dumps(schedule[idx])}\n"
                f"  envelope: {json.dumps(rec.envelopes[idx])}\n"
                f"  python:   {json.dumps(py_answers[idx])}\n"
                f"  ts:       {json.dumps(ts_answers[idx])}")
        faults = [i for i, r in enumerate(py_answers)
                  if (r.get("error") or {}).get("code") == -32603
                  and (r["error"].get("data") or {}).get("reason") != "store"]
        if faults:
            return False, (f"seed {seed}: two writers and store faults: envelope {faults[0]} was "
                           f"answered with -32603 for no store failure\n"
                           f"  envelope: {json.dumps(rec.envelopes[faults[0]])}\n"
                           f"  answer:   {json.dumps(py_answers[faults[0]])}")
        ok, msg = _same_files(seed, rough_py, rough_ts, config, "two writers and store faults")
        if not ok:
            return False, msg
    return True, ""


def _state_difference(py: object, ts: object, path: str = "") -> str:
    """The first place two projections differ, as a path and both values."""
    if isinstance(py, dict) and isinstance(ts, dict):
        for key in sorted(set(py) | set(ts)):
            if py.get(key, ...) != ts.get(key, ...):
                if key in py and key in ts:
                    return _state_difference(py[key], ts[key], f"{path}/{key}")
                return (f"  at {path}/{key}\n"
                        f"    python: {json.dumps(py.get(key, '<absent>'))}\n"
                        f"    ts:     {json.dumps(ts.get(key, '<absent>'))}")
    if isinstance(py, list) and isinstance(ts, list) and len(py) == len(ts):
        for i, (a, b) in enumerate(zip(py, ts)):
            if a != b:
                return _state_difference(a, b, f"{path}/{i}")
    return f"  at {path or '/'}\n    python: {json.dumps(py)}\n    ts:     {json.dumps(ts)}"


def _first_divergence(py: list[dict], ts: list[dict]) -> int | None:
    for i in range(min(len(py), len(ts))):
        if py[i] != ts[i]:
            return i
    if len(py) != len(ts):
        return min(len(py), len(ts))
    return None


def check_seed(seed: int, steps: int, config: Config,
               tally: tuple[Counter, Counter] | None = None) -> tuple[bool, str]:
    rec = Recorder(seed, steps, config)
    if tally is not None:
        tally[0].update(rec.sent)
        tally[1].update(rec.ok)
    altered = rec.recording_matches_what_was_sent()
    if altered:
        return False, (
            f"seed {seed}: the coordinator altered the envelope it recorded\n"
            f"  methods: {', '.join(sorted(set(altered)))}\n"
            "  A recorded envelope must be the one the client sent, or a "
            "signature over it no longer verifies.")
    faults = [i for i, r in enumerate(rec.responses)
              if r.get("error", {}).get("code") == -32603]
    if faults:
        i = faults[0]
        return False, (
            f"seed {seed}: the Python reference answered envelope {i} with -32603\n"
            f"  envelope: {json.dumps(rec.envelopes[i])}\n"
            "  -32603 is a fault in the coordinator: every refusal of a call has "
            "its own code.")
    ts = _replay_ts(rec.envelopes, config)
    ts_responses = ts["responses"]
    idx = _first_divergence(rec.responses, ts_responses)
    if idx is not None:
        env = rec.envelopes[idx] if idx < len(rec.envelopes) else None
        return False, (
            f"seed {seed}: responses diverge at envelope {idx}\n"
            f"  envelope: {json.dumps(env)}\n"
            f"  python:   {json.dumps(rec.responses[idx]) if idx < len(rec.responses) else '<none>'}\n"
            f"  ts:       {json.dumps(ts_responses[idx]) if idx < len(ts_responses) else '<none>'}")
    faults = [i for i, r in enumerate(ts_responses)
              if (r.get("error") or {}).get("code") == -32603]
    if faults:
        i = faults[0]
        return False, (
            f"seed {seed}: the TypeScript reference answered envelope {i} with -32603\n"
            f"  envelope: {json.dumps(rec.envelopes[i])}")
    idx = _first_divergence(rec.states, ts["states"])
    if idx is not None:
        return False, (
            f"seed {seed}: state diverges after envelope {idx}\n"
            f"  envelope: {json.dumps(rec.envelopes[idx])}\n"
            f"  response: {json.dumps(rec.responses[idx])}\n"
            f"{_state_difference(rec.states[idx], ts['states'][idx])}")
    if rec.chain_head() != ts["chain_head"]:
        return False, (
            f"seed {seed}: chain heads diverge after {len(rec.envelopes)} envelopes\n"
            f"  python: {rec.chain_head()}\n"
            f"  ts:     {ts['chain_head']}")
    stored, msg = check_stores(seed, rec)
    if not stored:
        return False, msg
    return True, f"seed {seed}: ok ({len(rec.envelopes)} envelopes)"


def _stats_table(sent: Counter, ok: Counter) -> str:
    """Per method, how many envelopes were sent and how many were accepted.

    A method whose calls are nearly all refused is being checked at the edge
    of its profile rather than in the middle of it, which is what this table
    makes visible.
    """
    width = max((len(m) for m in sent), default=6)
    lines = [f"{'method'.ljust(width)}  {'sent':>5} {'accepted':>9} {'rate':>6}",
             "-" * (width + 24)]
    for method in sorted(sent):
        accepted = ok[method]
        rate = accepted / sent[method]
        lines.append(f"{method.ljust(width)}  {sent[method]:5} {accepted:9} {rate:6.0%}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seeds", type=int, default=50, help="check seeds 0..N-1")
    p.add_argument("--first", type=int, default=0, help="the first seed of a sweep")
    p.add_argument("--seed", type=int, help="check a single seed")
    p.add_argument("--steps", type=int, default=40, help="random steps per run")
    p.add_argument("--config", choices=[c.name for c in CONFIGS],
                   help="run every seed in this configuration; by default the "
                        "seeds take the configurations by turns")
    p.add_argument("--stats", action="store_true",
                   help="print sent and succeeded per method, so coverage is "
                        "visible in the log rather than assumed")
    args = p.parse_args(argv)

    seeds = [args.seed] if args.seed is not None else range(args.first, args.first + args.seeds)
    tally: tuple[Counter, Counter] = (Counter(), Counter())
    failures = 0
    ran: Counter[str] = Counter()
    for seed in seeds:
        config = next(c for c in CONFIGS if c.name == args.config) if args.config \
            else CONFIGS[seed % len(CONFIGS)]
        ran[config.name] += 1
        ok, msg = check_seed(seed, args.steps, config, tally)
        if not ok:
            failures += 1
            print(f"[{config.name}] {msg}")
    total = len(seeds)
    if args.stats:
        print(_stats_table(*tally))
    print(f"\n{total - failures}/{total} seeds agreed "
          f"({', '.join(f'{name} {n}' for name, n in sorted(ran.items()))})")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
