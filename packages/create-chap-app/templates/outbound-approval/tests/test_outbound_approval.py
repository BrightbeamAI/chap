"""Outbound approval, end to end, with no model and no network.

The desk process runs in this process on a free port with the store in
memory, configured as chap.config.json ships it, where people sign and the
agent and the operator's console call unsigned; again with every call
signed; and again with signatures off. The scripted agent runs its loop in
a thread and connects as agent.py does, joining with its own key where
every call is signed. The approver decides through POST /chap as the desk
would, signing as the desk does, and outbox/ holds only what was approved.
One test runs the desk's own client in Node, as the browser runs it. The
decisions here are scripted because this is a test; in the project the
decision is made in the desk.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["CHAP_MODEL_PROVIDER"] = "scripted"

import agent as drafter  # noqa: E402
import pause  # noqa: E402
import resume  # noqa: E402
from chap_coordinator.client import HttpCoordinator, Participant, Signer  # noqa: E402
from chap_coordinator.server import enrolment_code, load_config, make_coordinator, make_server  # noqa: E402
from providers import make_provider  # noqa: E402

MODES = {
    "as shipped, people sign": {},
    "every call signed": {"require_signatures": True, "signature_policy": "all"},
    "signatures off": {"require_signatures": False, "signature_policy": None},
}


def node() -> str | None:
    """Node 22 or later, where the desk's client has Ed25519 in WebCrypto; None without it."""
    found = shutil.which("node")
    if found is None:
        return None
    version = subprocess.run([found, "--version"], capture_output=True, text=True, timeout=30).stdout
    return found if int(version.strip().lstrip("v").split(".")[0] or 0) >= 20 else None


NODE = node()

#: The approver at the desk, in Node: the desk's client module, as the
#: server serves it to the browser, makes a key that cannot be exported,
#: joins with it, waits for the review addressed to them, and approves it,
#: naming the artefact and the round as the desk does.
DESK_PERSON = """
import { pathToFileURL } from "node:url";
const [base, clientFile, enrolment] = process.argv.slice(1);
const { contentHash, generateSigner, makeClient } = await import(pathToFileURL(clientFile).href);
const get = async (path) => (await fetch(base + path)).json();
const config = await get("/api/config");
const me = config.humans[0];
const signer = await generateSigner(me.uri);
const desk = makeClient({ url: base + "/chap", workspace: config.workspace, from: me.uri, signer, enrolment });
await desk.call("participant.join", { type: "human", role: me.role, display_name: me.display_name,
                                      jwks: { keys: [signer.publicJwk] } });
let review;
for (let i = 0; i < 300 && !review; i++) {
  review = (await get("/api/reviews?reviewer=" + encodeURIComponent(me.uri))).reviews[0];
  if (!review) await new Promise((ok) => setTimeout(ok, 50));
}
const task = await get("/api/tasks/" + encodeURIComponent(review.task_id));
const decided = await desk.call("decide.approve", { task_id: task.task_id,
  approved_artefact_digest: await contentHash(task.artefact), round: await contentHash(task.submission.envelope) });
console.log(JSON.stringify({ kid: signer.kid, task_id: task.task_id, state: decided.state }));
"""


def start_desk(**overrides):
    config = load_config(ROOT / "chap.config.json")
    config.update(store=":memory:", port=0, host="127.0.0.1", **overrides)
    coord = make_coordinator(config)
    server = make_server(config, coord)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, {"config": config, "coord": coord, "base": f"http://127.0.0.1:{server.server_port}",
                    "port": server.server_port}


@pytest.fixture(scope="module", params=list(MODES))
def desk(request, tmp_path_factory):
    server, handle = start_desk(**MODES[request.param])
    handle["keys"] = tmp_path_factory.mktemp("keys")
    try:
        yield handle
    finally:
        server.shutdown()
        server.server_close()


def fetch(url: str):
    with urllib.request.urlopen(url, timeout=5) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if response.headers.get("content-type", "").startswith("application/json") else body


def open_reviews(base: str, reviewer: str) -> list[dict]:
    return fetch(f"{base}/api/reviews?reviewer={reviewer}")["reviews"]


def review_of(base: str, reviewer: str, message_id: str, holding: str | None = None):
    """The open review of one message, with ``holding`` in its body when given, else None."""
    for review in open_reviews(base, reviewer):
        if review["input"]["message_id"] == message_id and (holding is None or holding in review["artefact"]["body"]):
            return review
    return None


def wait_for(read, timeout: float = 5.0):
    """Poll ``read`` until it returns something true, and return that."""
    deadline = time.monotonic() + timeout
    while True:
        value = read()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting")
        time.sleep(0.05)


class AgentRun:
    """The agent's loop with --once in a thread, with its console lines and its outcomes."""

    def __init__(self, agent, provider, source, outbox):
        self.lines: list[str] = []
        self.outcomes = None
        self.thread = threading.Thread(target=self._run, args=(agent, provider, source, outbox), daemon=True)
        self.thread.start()

    def _run(self, agent, provider, source, outbox):
        self.outcomes = drafter.run(agent, provider, source, outbox, once=True, poll_seconds=0.05,
                                    log=self.lines.append)

    def result(self, timeout: float = 10.0):
        self.thread.join(timeout)
        assert not self.thread.is_alive(), f"the agent did not finish: {self.lines}"
        return self.outcomes


def participants(desk):
    """The agent as agent.py connects, and the approver as the desk joins
    them: with a key of their own when signatures are required."""
    config = desk["config"]
    agent = drafter.connect(config, desk["base"] + "/chap", keys=desk["keys"])
    uri = config["humans"][0]["uri"]
    signer = Signer.load_or_create(uri, desk["keys"] / "desk") if config["require_signatures"] else None
    approver = Participant(HttpCoordinator(desk["base"] + "/chap"), config["workspace"], uri, signer,
                           enrolment=enrolment_code(config))
    if signer:
        approver.join("human", "approver")
    return agent, approver


def test_only_an_approved_message_reaches_the_outbox(desk, tmp_path):
    base = desk["base"]
    agent, approver = participants(desk)
    provider = make_provider(drafter.scripted_body)
    assert provider.name == "scripted"
    outbox = tmp_path / "outbox"
    run = AgentRun(agent, provider, ROOT / "messages.csv", outbox)

    messages = drafter.read_messages(ROOT / "messages.csv")
    ids = [m["message_id"] for m in messages]
    assert len(set(ids)) == 3 and all(i.startswith("m-") for i in ids)
    assert drafter.message_id(messages[0]) == ids[0], "the same row keeps the same id"
    first = wait_for(lambda: review_of(base, approver.uri, ids[0]))

    # A trial-mode task opens a review although the agent never asked for one.
    view = agent.task(first["task_id"])
    assert view["state"] == "review_requested" and view["review"]["requested_to"] == [approver.uri]
    assert first["artefact"] == drafter.draft(provider, messages[0])

    # The agent signs only where every call is signed; where people sign,
    # its calls go unsigned and are taken from it as a member.
    assert (agent.signer is not None) is (desk["config"]["signature_policy"] == "all")

    # The agent cannot approve its own work.
    assert agent.send("decide.approve", task_id=first["task_id"])["error"]["code"] == -32011

    # Where people sign, a call in the approver's name that their key did not
    # sign is refused, so no other process decides as them.
    if desk["config"]["require_signatures"]:
        unsigned = HttpCoordinator(base + "/chap").dispatch({
            "jsonrpc": "2.0", "id": "x", "method": "decide.approve",
            "params": {"workspace": desk["config"]["workspace"], "from": approver.uri, "task_id": first["task_id"]}})
        assert unsigned["error"]["code"] == -32070

    # The approver approves one as written.
    assert approver.call("decide.approve", task_id=first["task_id"], comment="send it")["state"] == "completed"

    # Rejects one asking for a revision: the agent drafts again with the
    # note, the review opens again on the new draft, and the approver
    # overrides that draft with an RFC 6902 patch.
    second = wait_for(lambda: review_of(base, approver.uri, ids[1]))
    rejected = approver.call("decide.reject", task_id=second["task_id"], comment="name the new price",
                             request_revision=True)
    assert rejected["state"] == "in_progress"
    revised = wait_for(lambda: review_of(base, approver.uri, ids[1], holding="name the new price"))
    # The new draft opens a new round with no decisions, so the rejection
    # counts only for the draft it was made on; the task's log keeps it.
    assert revised["decisions"] == []
    assert fetch(f"{base}/api/tasks/{second['task_id']}")["decision_log"][-1]["kind"] == "reject"
    overridden = approver.call("decide.override", task_id=second["task_id"], rationale="shorter",
                               diff=[{"op": "replace", "path": "/body", "value": "Edited body"}],
                               intent_preserved=True)
    assert overridden["applied"]["body"] == "Edited body"

    # And rejects one outright.
    third = wait_for(lambda: review_of(base, approver.uri, ids[2]))
    assert approver.call("decide.reject", task_id=third["task_id"], comment="not this week")["state"] == "declined"

    assert run.result() == {ids[0]: "approve", ids[1]: "override", ids[2]: "reject"}
    assert any("drafted again, with the note from the approver" in line for line in run.lines)
    assert sorted(p.name for p in outbox.iterdir()) == sorted([f"{ids[0]}.json", f"{ids[1]}.json"])
    sent = json.loads((outbox / f"{ids[0]}.json").read_text(encoding="utf-8"))
    assert sent["message"] == first["artefact"] and sent["decided_by"] == approver.uri
    edited = json.loads((outbox / f"{ids[1]}.json").read_text(encoding="utf-8"))
    assert edited["message"] == {**revised["artefact"], "body": "Edited body"} and edited["decision"] == "override"

    # The refused approval is on the chain as a refusal; the decisions as
    # calls; and the chain is whole.
    entries = approver.call("audit.read", filter={"task_id": first["task_id"]})["entries"]
    assert any(e.get("outcome") == {"status": "refused", "code": -32011}
               and e["request"]["method"] == "decide.approve" and e["request"]["params"]["from"] == agent.uri
               for e in entries)
    assert any(e.get("envelope", {}).get("method") == "decide.approve" for e in entries)
    assert approver.call("audit.verify_chain")["status"] == "verified"


def test_a_paused_agent_opens_nothing_until_resumed(desk, tmp_path, monkeypatch, capsys):
    base = desk["base"]
    agent, approver = participants(desk)
    provider = make_provider(drafter.scripted_body)
    source = tmp_path / "one.csv"
    source.write_text("to,subject,brief\nkim.lau@example.com,Invoice 77 is paid,Confirm that invoice 77 was received and is paid.\n",
                      encoding="utf-8")
    [mid] = [m["message_id"] for m in drafter.read_messages(source)]
    outbox = tmp_path / "outbox"

    # pause.py, as the operator's console, against this desk.
    monkeypatch.setenv("PORT", str(desk["port"]))
    monkeypatch.setattr(drafter, "KEYS", desk["keys"])
    pause.main(["hold"])
    assert f"Paused {agent.uri} (hold)." in capsys.readouterr().out
    paused = [e for e in approver.call("audit.read", filter={"method": "control.pause"})["entries"]]
    assert [e["envelope"]["params"]["from"] for e in paused] == [drafter.OPERATOR]
    # The console is a service: where signatures are required it signs, under "humans" too.
    assert ("sig" in paused[0]["envelope"]) is bool(desk["config"]["signature_policy"])

    # The first task.create is refused with -32063 and recorded; after that
    # the agent waits on workspace.describe, so the refusal stays the only one.
    run = AgentRun(agent, provider, source, outbox)
    wait_for(lambda: any("refused -32063" in line and "waiting for resume.py" in line for line in run.lines))
    time.sleep(0.3)
    refusals = [e for e in approver.call("audit.read", filter={"outcome": "refused"})["entries"]
                if e["request"]["method"] == "task.create" and e["request"]["params"]["from"] == agent.uri]
    assert len(refusals) == 1 and refusals[0]["outcome"]["code"] == -32063
    assert review_of(base, approver.uri, mid) is None
    assert not any(line.startswith("error:") for line in run.lines), run.lines

    # Resumed, the task goes through on the next pass and waits in the desk.
    resume.main()
    assert f"Resumed {agent.uri}." in capsys.readouterr().out
    review = wait_for(lambda: review_of(base, approver.uri, mid))
    assert any("the agent is resumed, task.create accepted" in line for line in run.lines)
    approver.call("decide.approve", task_id=review["task_id"])
    assert run.result() == {mid: "approve"}
    assert (outbox / f"{mid}.json").exists()


def test_the_desk_is_served(desk):
    config, base = desk["config"], desk["base"]
    with urllib.request.urlopen(f"{base}/", timeout=5) as response:
        assert "CHAP review desk" in response.read().decode("utf-8")
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert response.headers["x-frame-options"] == "DENY"
    assert "export function makeClient" in fetch(f"{base}/chap-client.mjs")
    public = fetch(f"{base}/api/config")
    assert public["workspace"] == config["workspace"] and public["mcp"] is False
    assert {"modes/1.0", "control/1.0"} <= set(public["profiles"])
    assert public["chain_enabled"] is True and public["require_signatures"] is config["require_signatures"]
    assert public["signature_policy"] == config["signature_policy"]
    agent, _ = participants(desk)
    described = agent.describe()
    assert described["mode"] == "trial" and described["profiles"] == config["profiles"]
    with pytest.raises(urllib.error.HTTPError) as refused:
        fetch(f"{base}/api/tasks/tsk_missing")
    assert refused.value.code == 404


@pytest.mark.skipif(NODE is None, reason="the desk's client runs in Node 22 or later, which is not installed")
def test_the_desks_own_client_signs_the_approvers_decision(tmp_path):
    """As shipped, the agent calls unsigned, and the approver's decision is
    signed in the desk, with the desk's own client and a key it made."""
    server, desk = start_desk()
    try:
        base, config = desk["base"], desk["config"]
        agent = drafter.connect(config, base + "/chap", keys=tmp_path / "keys")
        assert agent.signer is None and not (tmp_path / "keys").exists()
        client = tmp_path / "chap-client.mjs"
        client.write_text(fetch(f"{base}/chap-client.mjs"), encoding="utf-8")
        source = tmp_path / "one.csv"
        source.write_text("to,subject,brief\nkim.lau@example.com,Invoice 77,Confirm invoice 77 is paid.\n",
                          encoding="utf-8")
        [mid] = [m["message_id"] for m in drafter.read_messages(source)]
        outbox = tmp_path / "outbox"
        run = AgentRun(agent, make_provider(drafter.scripted_body), source, outbox)
        person = subprocess.run([NODE, "--input-type=module", "-e", DESK_PERSON, base, str(client),
                                 enrolment_code(desk["config"]) or ""],
                                capture_output=True, text=True, timeout=60)
        assert person.returncode == 0, person.stderr
        decided = json.loads(person.stdout)
        assert decided["state"] == "completed"
        assert run.result() == {mid: "approve"}
        assert json.loads((outbox / f"{mid}.json").read_text(encoding="utf-8"))["decided_by"] == config["humans"][0]["uri"]

        # On the chain, the agent's submission carries no signature, and the
        # approval is signed with the key the desk made.
        entries = agent.call("audit.read", filter={"task_id": decided["task_id"]})["entries"]
        calls = {e["envelope"]["method"]: e["envelope"] for e in entries if "envelope" in e}
        assert "sig" not in calls["task.complete"]
        assert calls["decide.approve"]["sig"].startswith(f"ed25519:{decided['kid']}:")
        assert agent.call("audit.verify_chain")["status"] == "verified"
    finally:
        server.shutdown()
        server.server_close()


def test_a_submission_the_store_did_not_record_is_reported_and_made_again(tmp_path):
    server, desk = start_desk()
    desk["keys"] = tmp_path / "keys"
    try:
        agent, approver = participants(desk)
        store = desk["coord"].options.store
        write = store.apply
        failed: list[str] = []

        def full(change):
            # The disk is full when the agent first submits its draft.
            if not failed and any(e.get("envelope", {}).get("method") == "task.complete" for e in change.entries):
                failed.append(change.id)
                raise sqlite3.OperationalError("database or disk is full")
            return write(change)

        store.apply = full
        source = tmp_path / "one.csv"
        source.write_text("to,subject,brief\nkim.lau@example.com,Invoice 78,Confirm invoice 78 is paid.\n",
                          encoding="utf-8")
        [mid] = [m["message_id"] for m in drafter.read_messages(source)]
        run = AgentRun(agent, make_provider(drafter.scripted_body), source, tmp_path / "outbox")
        review = wait_for(lambda: review_of(desk["base"], approver.uri, mid))
        approver.call("decide.approve", task_id=review["task_id"])
        assert run.result() == {mid: "approve"}
        assert failed and [line for line in run.lines if line.startswith("error:")] == [
            "error: task.complete: The store did not record this call, so it took no effect; the server's log "
            "says why (-32603). The next pass makes it again"]
    finally:
        server.shutdown()
        server.server_close()


def test_where_every_call_is_signed_an_agent_with_another_key_is_told_why_it_cannot_join(tmp_path):
    server, desk = start_desk(**MODES["every call signed"])
    try:
        url = desk["base"] + "/chap"
        drafter.connect(desk["config"], url, keys=tmp_path / "keys")
        with pytest.raises(SystemExit) as refused:
            drafter.connect(desk["config"], url, keys=tmp_path / "other-keys")
        message = str(refused.value)
        assert "agent:drafter is already a member, with other keys" in message
        assert (f"The workspace holds the key the agent first joined with, and "
                f"{tmp_path / 'other-keys' / 'agent_drafter.jwk.json'} holds another.") in message
    finally:
        server.shutdown()
        server.server_close()


def test_the_readmes_bridge_example_runs_as_written(tmp_path, capsys):
    pytest.importorskip("chap_langgraph", reason="the README's example runs chap-langgraph, which is not installed")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    [example] = [block for block in re.findall(r"```python\n(.*?)```", readme, re.S) if "hil_review" in block]
    shipped = json.loads((ROOT / "chap.config.json").read_text(encoding="utf-8"))
    server, desk = start_desk()
    try:
        uri = desk["config"]["humans"][0]["uri"]
        approver = Participant(HttpCoordinator(desk["base"] + "/chap"), desk["config"]["workspace"], uri,
                               Signer.load_or_create(uri, tmp_path / "desk"), enrolment=enrolment_code(desk["config"]))
        approver.join("human", "approver")

        def decide():
            review = wait_for(lambda: next(iter(open_reviews(desk["base"], uri)), None))
            approver.call("decide.approve", task_id=review["task_id"])

        threading.Thread(target=decide, daemon=True).start()
        # As written, with this desk's address and a quicker poll.
        quick = 'wait_for_decision(state["chap_task_id"], poll_seconds=0.05)'
        runnable = (example.replace(f"http://127.0.0.1:{shipped['port']}/chap", desk["base"] + "/chap")
                    .replace('wait_for_decision(state["chap_task_id"])', quick))
        exec(runnable, {"__name__": "readme"})
        assert capsys.readouterr().out.splitlines()[-1].startswith("decided: {'to': 'kim.lau@example.com'")
    finally:
        server.shutdown()
        server.server_close()


def test_the_project_ships_with_people_signing_and_the_chain_on():
    shipped = json.loads((ROOT / "chap.config.json").read_text(encoding="utf-8"))
    assert shipped["require_signatures"] == "humans" and shipped["chain"] is True
    # The generator stamps the release that made the project.
    assert shipped["template"]["name"] == "outbound-approval"
    assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?", shipped["template"]["version"])
    assert "keys/" in (ROOT / ".gitignore").read_text(encoding="utf-8").split()


def test_the_tests_name_what_they_need():
    assert (ROOT / "requirements-dev.txt").read_text(encoding="utf-8").split() == ["-r", "requirements.txt", "pytest>=7"]
