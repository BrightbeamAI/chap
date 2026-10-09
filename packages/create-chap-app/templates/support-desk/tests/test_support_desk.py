"""The support desk, end to end, with no model and no network.

The desk process runs in this process on a free port with the store in
memory. The scripted agent runs its loop in a thread, the human decides
through POST /chap as the desk would, and replies/ holds only what the
human approved. The decisions here are scripted because this is a test; in
the project the decision is made in the desk.
"""
from __future__ import annotations

import json
import os
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
from chap_client import HttpCoordinator, Participant  # noqa: E402
from chap_server import load_config, make_coordinator, make_server  # noqa: E402
from providers import make_provider  # noqa: E402


def start_desk(**overrides):
    config = load_config(ROOT / "chap.config.json")
    config.update(store=":memory:", port=0, host="127.0.0.1", **overrides)
    coord = make_coordinator(config)
    server = make_server(config, coord)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, {"config": config, "coord": coord, "base": f"http://127.0.0.1:{server.server_port}"}


@pytest.fixture(scope="module")
def desk():
    server, handle = start_desk()
    try:
        yield handle
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="module")
def desk_without_a_reviewer():
    """A desk whose configuration names no human, so nobody can review until someone joins."""
    config = load_config(ROOT / "chap.config.json")
    server, handle = start_desk(humans=[], workspace=config["workspace"] + "_alone")
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


def participants(desk):
    config = desk["config"]
    client = HttpCoordinator(desk["base"] + "/chap")
    return (Participant(client, config["workspace"], config["agent"]["uri"]),
            Participant(client, config["workspace"], config["humans"][0]["uri"]))


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

    # The agent cannot approve its own work.
    own = agent.send("decide.approve", task_id=first["task_id"])
    assert own["error"]["code"] == -32011

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
    assert revised["decisions"][-1]["kind"] == "reject"
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

    # The refused approval is on the chain as a refusal; the accepted decision as a call.
    entries = human.call("audit.read", filter={"task_id": first["task_id"]})["entries"]
    assert any(e.get("outcome") == {"status": "refused", "code": -32011}
               and e["request"]["method"] == "decide.approve" and e["request"]["params"]["from"] == agent.uri
               for e in entries)
    assert any(e.get("envelope", {}).get("method") == "decide.approve" for e in entries)
    assert fetch(f"{base}/api/tasks/{by_ticket['T-1003']['task_id']}")["state"] == "declined"
    assert open_reviews(base, human.uri) == []


def test_the_agent_waits_for_a_reviewer_and_a_restart_opens_no_duplicate(desk_without_a_reviewer, tmp_path):
    config, base = desk_without_a_reviewer["config"], desk_without_a_reviewer["base"]
    client = HttpCoordinator(base + "/chap")
    agent = Participant(client, config["workspace"], config["agent"]["uri"])
    human = Participant(client, config["workspace"], "human:late@local")
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
    assert human.call("audit.read", filter={"outcome": "refused"})["entries"] == []

    # A reviewer joins; the draft is submitted on the next pass and decided.
    human.join("human", "reviewer")
    review = wait_for(lambda: next(iter(open_reviews(base, human.uri)), None))
    assert review["input"]["id"] == "T-9"
    human.call("decide.approve", task_id=review["task_id"])
    assert run.result() == {"T-9": "approve"}
    assert (replies_dir / "T-9.json").exists()

    # A restart: the ticket id is the idempotency key, so task.create answers
    # with the task decided before, and nothing is opened or written again.
    tasks_before = fetch(f"{base}/api/health")["tasks"]
    again = AgentRun(agent, provider, source, replies_dir)
    assert again.result() == {"T-9": "approve"}
    assert any("decided earlier" in line for line in again.lines)
    assert fetch(f"{base}/api/health")["tasks"] == tasks_before


def test_a_bridge_sees_the_desk_as_a_coordinator(desk):
    config, base = desk["config"], desk["base"]
    coord = HttpCoordinator(base + "/chap")
    r = coord.dispatch({"jsonrpc": "2.0", "id": "1", "method": "workspace.describe",
                        "params": {"workspace": config["workspace"]}})
    assert r["result"]["profiles"] == config["profiles"]
    assert config["workspace"] in coord.workspaces and "wsp_other" not in coord.workspaces
    assert config["agent"]["uri"] in coord.workspaces.get(config["workspace"]).members
    bad = coord.dispatch({"jsonrpc": "2.0", "id": "2", "method": "no.such", "params": {}})
    assert bad["error"]["code"] == -32601


def test_the_desk_is_served(desk):
    config, base = desk["config"], desk["base"]
    assert "CHAP review desk" in fetch(f"{base}/")
    assert "export function makeClient" in fetch(f"{base}/chap-client.mjs")
    public = fetch(f"{base}/api/config")
    assert public["workspace"] == config["workspace"] and public["mcp"] is False
    assert public["humans"][0]["uri"] == config["humans"][0]["uri"]
    assert public["persistent"] is False
    health = fetch(f"{base}/api/health")
    assert health["ok"] is True and health["members"] == 2
    with pytest.raises(urllib.error.HTTPError) as refused:
        fetch(f"{base}/api/tasks/tsk_missing")
    assert refused.value.code == 404
