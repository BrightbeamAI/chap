"""The support desk, end to end, with no model and no network.

The desk process runs in this process on a free port with the store in
memory, configured as chap.config.json ships it, where people sign and the
agent calls unsigned; again with every call signed; and again with
signatures off. The scripted agent runs its loop in a thread and connects
as agent.py does, joining with its own key where every call is signed. The
human decides through POST /chap as the desk would, signing as the desk
does, and replies/ holds only what the human approved. One test runs the
desk's own client in Node, as the browser runs it. The decisions here are
scripted because this is a test; in the project the decision is made in
the desk.
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
from chap_coordinator.client import HttpCoordinator, Participant, Signer  # noqa: E402
from chap_coordinator.server import load_config, make_coordinator, make_server  # noqa: E402
from providers import make_provider  # noqa: E402

MODES = {
    "as shipped, people sign": {},
    "every call signed": {"require_signatures": True, "signature_policy": "all"},
    "signatures off": {"require_signatures": False, "signature_policy": None},
}


def node() -> str | None:
    """Node 20 or later, where the desk's client has Ed25519 in WebCrypto; None without it."""
    found = shutil.which("node")
    if found is None:
        return None
    version = subprocess.run([found, "--version"], capture_output=True, text=True, timeout=30).stdout
    return found if int(version.strip().lstrip("v").split(".")[0] or 0) >= 20 else None


NODE = node()

#: The person at the desk, in Node: the desk's client module, as the server
#: serves it to the browser, makes a key that cannot be exported, joins with
#: it, waits for the review addressed to them, and approves it, naming the
#: artefact and the round as the desk does.
DESK_PERSON = """
import { pathToFileURL } from "node:url";
const [base, clientFile] = process.argv.slice(1);
const { contentHash, generateSigner, makeClient } = await import(pathToFileURL(clientFile).href);
const get = async (path) => (await fetch(base + path)).json();
const config = await get("/api/config");
const me = config.humans[0];
const signer = await generateSigner(me.uri);
const desk = makeClient({ url: base + "/chap", workspace: config.workspace, from: me.uri, signer });
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
    return server, {"config": config, "coord": coord, "base": f"http://127.0.0.1:{server.server_port}"}


@pytest.fixture(scope="module", params=list(MODES))
def desk(request, tmp_path_factory):
    server, handle = start_desk(**MODES[request.param])
    handle["keys"] = tmp_path_factory.mktemp("keys")
    try:
        yield handle
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="module", params=list(MODES))
def desk_without_a_reviewer(request, tmp_path_factory):
    """A desk whose configuration names no human, so nobody can review until someone joins."""
    config = load_config(ROOT / "chap.config.json")
    server, handle = start_desk(humans=[], workspace=config["workspace"] + "_alone", **MODES[request.param])
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


def reviews_numbering(base: str, reviewer: str, count: int):
    """The open reviews once there are ``count`` of them, else None."""
    reviews = open_reviews(base, reviewer)
    return reviews if len(reviews) == count else None


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

    def __init__(self, agent, provider, source, replies_dir):
        self.lines: list[str] = []
        self.outcomes = None
        self.thread = threading.Thread(target=self._run, args=(agent, provider, source, replies_dir), daemon=True)
        self.thread.start()

    def _run(self, agent, provider, source, replies_dir):
        self.outcomes = drafter.run(agent, provider, source, replies_dir, once=True, poll_seconds=0.05,
                                    log=self.lines.append)

    def result(self, timeout: float = 10.0):
        self.thread.join(timeout)
        assert not self.thread.is_alive(), f"the agent did not finish: {self.lines}"
        return self.outcomes


def person(desk, uri: str) -> Participant:
    """A person deciding in the desk: with a key the desk made for them, and
    joined with it, when signatures are required."""
    config = desk["config"]
    signer = Signer.load_or_create(uri, desk["keys"] / "desk") if config["require_signatures"] else None
    human = Participant(HttpCoordinator(desk["base"] + "/chap"), config["workspace"], uri, signer)
    if signer:
        human.join("human", "reviewer")
    return human


def participants(desk):
    """The agent as agent.py connects, and the configured reviewer as the desk joins them."""
    config = desk["config"]
    agent = drafter.connect(config, desk["base"] + "/chap", keys=desk["keys"])
    return agent, person(desk, config["humans"][0]["uri"])


def test_replies_hold_only_what_the_human_approved(desk, tmp_path):
    base = desk["base"]
    agent, human = participants(desk)
    provider = make_provider(drafter.scripted_reply)
    assert provider.name == "scripted"
    replies_dir = tmp_path / "replies"
    run = AgentRun(agent, provider, ROOT / "tickets.csv", replies_dir)

    tickets = drafter.read_tickets(ROOT / "tickets.csv")
    assert [t["id"] for t in tickets] == ["T-1001", "T-1002", "T-1003"]
    reviews = wait_for(lambda: reviews_numbering(base, human.uri, 3))
    by_ticket = {r["input"]["id"]: r for r in reviews}
    first = by_ticket["T-1001"]
    assert first["artefact"] == drafter.draft(provider, tickets[0]) and first["input"] == tickets[0]
    assert first["reviewers"] == [human.uri] and first["state"] == "review_requested"

    # The agent signs only where every call is signed; where people sign,
    # its calls go unsigned and are taken from it as a member.
    assert (agent.signer is not None) is (desk["config"]["signature_policy"] == "all")

    # The agent cannot approve its own work.
    own = agent.send("decide.approve", task_id=first["task_id"])
    assert own["error"]["code"] == -32011

    # Where people sign, a call in the reviewer's name that their key did not
    # sign is refused, so no other process decides as them.
    if desk["config"]["require_signatures"]:
        unsigned = HttpCoordinator(base + "/chap").dispatch({
            "jsonrpc": "2.0", "id": "x", "method": "decide.approve",
            "params": {"workspace": desk["config"]["workspace"], "from": human.uri, "task_id": first["task_id"]}})
        assert unsigned["error"]["code"] == -32070

    # The human approves one as written.
    assert human.call("decide.approve", task_id=first["task_id"], comment="fine")["state"] == "completed"

    # Rejects one asking for a revision: the agent drafts again with the note
    # and the review opens again on the new draft, which the human overrides.
    second = by_ticket["T-1002"]
    rejected = human.call("decide.reject", task_id=second["task_id"], comment="mention the order number",
                          request_revision=True)
    assert rejected["state"] == "in_progress"
    revised = wait_for(lambda: next((r for r in open_reviews(base, human.uri)
                                     if r["task_id"] == second["task_id"]
                                     and "mention the order number" in r["artefact"]["body"]), None))
    # The new draft opens a new round with no decisions, so the rejection
    # counts only for the draft it was made on; the task's log keeps it.
    assert revised["decisions"] == []
    assert fetch(f"{base}/api/tasks/{second['task_id']}")["decision_log"][-1]["kind"] == "reject"
    overridden = human.call("decide.override", task_id=second["task_id"], rationale="no promise of a date",
                            diff=[{"op": "replace", "path": "/body", "value": "Edited reply"}],
                            intent_preserved=True)
    assert overridden["state"] == "completed" and overridden["applied"]["body"] == "Edited reply"

    # And rejects one outright.
    assert human.call("decide.reject", task_id=by_ticket["T-1003"]["task_id"], comment="not ours")["state"] == "declined"

    assert run.result() == {"T-1001": "approve", "T-1002": "override", "T-1003": "reject"}
    assert any("drafted again, with the note from the reviewer" in line for line in run.lines)

    assert sorted(p.name for p in replies_dir.iterdir()) == ["T-1001.json", "T-1002.json"]
    approved = json.loads((replies_dir / "T-1001.json").read_text(encoding="utf-8"))
    assert approved["reply"] == first["artefact"] and approved["decision"] == "approve"
    assert approved["decided_by"] == human.uri and approved["task_id"] == first["task_id"]
    edited = json.loads((replies_dir / "T-1002.json").read_text(encoding="utf-8"))
    assert edited["reply"] == {**revised["artefact"], "body": "Edited reply"} and edited["decision"] == "override"

    # The refused approval is on the chain as a refusal; the accepted decision
    # as a call; and the chain is whole.
    entries = human.call("audit.read", filter={"task_id": first["task_id"]})["entries"]
    assert any(e.get("outcome") == {"status": "refused", "code": -32011}
               and e["request"]["method"] == "decide.approve" and e["request"]["params"]["from"] == agent.uri
               for e in entries)
    assert any(e.get("envelope", {}).get("method") == "decide.approve" for e in entries)
    assert human.call("audit.verify_chain")["status"] == "verified"
    assert fetch(f"{base}/api/tasks/{by_ticket['T-1003']['task_id']}")["state"] == "declined"
    assert open_reviews(base, human.uri) == []


def test_the_agent_waits_for_a_reviewer_and_a_restart_opens_no_duplicate(desk_without_a_reviewer, tmp_path):
    desk = desk_without_a_reviewer
    config, base = desk["config"], desk["base"]
    agent = drafter.connect(config, base + "/chap", keys=desk["keys"])
    provider = make_provider(drafter.scripted_reply)
    source = tmp_path / "one.csv"
    source.write_text("id,customer,subject,body\nT-9,Ada Byrne,Refund for order 9,Please refund order 9.\n",
                      encoding="utf-8")
    replies_dir = tmp_path / "replies"

    # With no human in the workspace the review cannot open. The agent says so
    # once and waits on a read, so nothing is refused and nothing recorded.
    run = AgentRun(agent, provider, source, replies_dir)
    wait_for(lambda: any("no reviewer has joined" in line for line in run.lines))
    time.sleep(0.3)
    assert agent.call("audit.read", filter={"outcome": "refused"})["entries"] == []
    assert not any(line.startswith("error:") for line in run.lines), run.lines

    # The operator names a reviewer in the configuration, which the server
    # needs before it admits their join; they join, and the draft is
    # submitted on the next pass and decided.
    config["humans"].append({"uri": "human:late@local", "display_name": "Late", "role": "reviewer"})
    human = person(desk, "human:late@local")
    if not config["require_signatures"]:
        human.join("human", "reviewer")
    review = wait_for(lambda: next(iter(open_reviews(base, human.uri)), None))
    assert review["input"]["id"] == "T-9"
    human.call("decide.approve", task_id=review["task_id"])
    assert run.result() == {"T-9": "approve"}
    assert (replies_dir / "T-9.json").exists()

    # A restart: the ticket id is the idempotency key, so task.create answers
    # with the task decided before, and nothing is opened or written again.
    tasks_before = fetch(f"{base}/api/health")["tasks"]
    again = AgentRun(drafter.connect(config, base + "/chap", keys=desk["keys"]), provider, source, replies_dir)
    assert again.result() == {"T-9": "approve"}
    assert any("decided earlier" in line for line in again.lines)
    assert fetch(f"{base}/api/health")["tasks"] == tasks_before


def test_a_bridge_sees_the_desk_as_a_coordinator_through_the_agent(desk):
    config = desk["config"]
    agent, _ = participants(desk)
    r = agent.dispatch({"jsonrpc": "2.0", "id": "1", "method": "workspace.describe",
                        "params": {"workspace": config["workspace"], "from": agent.uri}})
    assert r["result"]["profiles"] == config["profiles"]
    assert config["workspace"] in agent.workspaces and "wsp_other" not in agent.workspaces
    assert config["agent"]["uri"] in agent.workspaces.get(config["workspace"]).members
    bad = agent.dispatch({"jsonrpc": "2.0", "id": "2", "method": "no.such", "params": {}})
    assert bad["error"]["code"] == -32601


def test_the_desk_is_served(desk):
    config, base = desk["config"], desk["base"]
    participants(desk)
    with urllib.request.urlopen(f"{base}/", timeout=5) as response:
        assert "CHAP review desk" in response.read().decode("utf-8")
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert response.headers["x-frame-options"] == "DENY"
    assert "export function makeClient" in fetch(f"{base}/chap-client.mjs")
    public = fetch(f"{base}/api/config")
    assert public["workspace"] == config["workspace"] and public["mcp"] is False
    assert public["humans"][0]["uri"] == config["humans"][0]["uri"]
    assert public["persistent"] is False and public["chain_enabled"] is True
    assert public["require_signatures"] is config["require_signatures"]
    assert public["signature_policy"] == config["signature_policy"]
    health = fetch(f"{base}/api/health")
    assert health["ok"] is True and health["members"] == 2
    with pytest.raises(urllib.error.HTTPError) as refused:
        fetch(f"{base}/api/tasks/tsk_missing")
    assert refused.value.code == 404


@pytest.mark.skipif(NODE is None, reason="the desk's client runs in Node 20 or later, which is not installed")
def test_the_desks_own_client_signs_the_persons_decision(tmp_path):
    """As shipped, the agent calls unsigned, and the person's decision is
    signed in the desk, with the desk's own client and a key it made."""
    server, desk = start_desk()
    try:
        base, config = desk["base"], desk["config"]
        agent = drafter.connect(config, base + "/chap", keys=tmp_path / "keys")
        assert agent.signer is None and not (tmp_path / "keys").exists()
        client = tmp_path / "chap-client.mjs"
        client.write_text(fetch(f"{base}/chap-client.mjs"), encoding="utf-8")
        source = tmp_path / "one.csv"
        source.write_text("id,customer,subject,body\nT-7,Ada Byrne,Order 7,Where is order 7?\n", encoding="utf-8")
        replies_dir = tmp_path / "replies"
        run = AgentRun(agent, make_provider(drafter.scripted_reply), source, replies_dir)
        person = subprocess.run([NODE, "--input-type=module", "-e", DESK_PERSON, base, str(client)],
                                capture_output=True, text=True, timeout=60)
        assert person.returncode == 0, person.stderr
        decided = json.loads(person.stdout)
        assert decided["state"] == "completed"
        assert run.result() == {"T-7": "approve"}
        assert json.loads((replies_dir / "T-7.json").read_text(encoding="utf-8"))["decided_by"] == config["humans"][0]["uri"]

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
        agent, human = participants(desk)
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
        source.write_text("id,customer,subject,body\nT-5,Ada Byrne,Order 5,Where is order 5?\n", encoding="utf-8")
        run = AgentRun(agent, make_provider(drafter.scripted_reply), source, tmp_path / "replies")
        review = wait_for(lambda: next(iter(open_reviews(desk["base"], human.uri)), None))
        human.call("decide.approve", task_id=review["task_id"])
        assert run.result() == {"T-5": "approve"}
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
    desk["keys"] = tmp_path / "keys"
    try:
        human = person(desk, desk["config"]["humans"][0]["uri"])

        def decide():
            review = wait_for(lambda: next(iter(open_reviews(desk["base"], human.uri)), None))
            human.call("decide.reject", task_id=review["task_id"], comment="not this one")

        threading.Thread(target=decide, daemon=True).start()
        # As written, with this desk's address and a quicker poll.
        quick = 'wait_for_decision(state["chap_task_id"], poll_seconds=0.05)'
        runnable = (example.replace(f"http://127.0.0.1:{shipped['port']}/chap", desk["base"] + "/chap")
                    .replace('wait_for_decision(state["chap_task_id"])', quick))
        exec(runnable, {"__name__": "readme"})
        assert capsys.readouterr().out.splitlines()[-1] == "not approved: not this one"
    finally:
        server.shutdown()
        server.server_close()


def test_the_project_ships_with_people_signing_and_the_chain_on():
    shipped = json.loads((ROOT / "chap.config.json").read_text(encoding="utf-8"))
    assert shipped["require_signatures"] == "humans" and shipped["chain"] is True
    # The generator stamps the release that made the project.
    assert shipped["template"]["name"] == "support-desk"
    assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?", shipped["template"]["version"])
    assert "keys/" in (ROOT / ".gitignore").read_text(encoding="utf-8").split()


def test_the_tests_name_what_they_need():
    assert (ROOT / "requirements-dev.txt").read_text(encoding="utf-8").split() == ["-r", "requirements.txt", "pytest>=7"]
