"""Outbound approval, end to end, with no model and no network.

The desk process runs in this process on a free port with the store in
memory. The scripted agent runs its loop in a thread, the approver decides
through POST /chap as the desk would, and outbox/ holds only what was
approved. The decisions here are scripted because this is a test; in the
project the decision is made in the desk.
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


@pytest.fixture(scope="module")
def desk():
    config = load_config(ROOT / "chap.config.json")
    config.update(store=":memory:", port=0, host="127.0.0.1")
    coord = make_coordinator(config)
    server = make_server(config, coord)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield {"config": config, "coord": coord, "base": f"http://127.0.0.1:{server.server_port}"}
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
    config = desk["config"]
    client = HttpCoordinator(desk["base"] + "/chap")
    return (Participant(client, config["workspace"], config["agent"]["uri"]),
            Participant(client, config["workspace"], config["humans"][0]["uri"]))


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

    # The agent cannot approve its own work.
    assert agent.send("decide.approve", task_id=first["task_id"])["error"]["code"] == -32011

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
    assert revised["decisions"][-1]["kind"] == "reject"
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

    # The refused approval is on the chain as a refusal; the decisions as calls.
    entries = approver.call("audit.read", filter={"task_id": first["task_id"]})["entries"]
    assert any(e.get("outcome") == {"status": "refused", "code": -32011}
               and e["request"]["method"] == "decide.approve" and e["request"]["params"]["from"] == agent.uri
               for e in entries)
    assert any(e.get("envelope", {}).get("method") == "decide.approve" for e in entries)


def test_a_paused_agent_opens_nothing_until_resumed(desk, tmp_path):
    base = desk["base"]
    agent, approver = participants(desk)
    provider = make_provider(drafter.scripted_body)
    source = tmp_path / "one.csv"
    source.write_text("to,subject,brief\nkim.lau@example.com,Invoice 77 is paid,Confirm that invoice 77 was received and is paid.\n",
                      encoding="utf-8")
    [mid] = [m["message_id"] for m in drafter.read_messages(source)]
    outbox = tmp_path / "outbox"

    paused = approver.call("control.pause", scope="participant", participant_uri=agent.uri, reason="hold")
    assert paused["paused"] is True

    # The first task.create is refused with -32063 and recorded; after that
    # the agent waits on workspace.describe, so the refusal stays the only one.
    run = AgentRun(agent, provider, source, outbox)
    wait_for(lambda: any("refused -32063" in line and "waiting for resume.py" in line for line in run.lines))
    time.sleep(0.3)
    refusals = [e for e in approver.call("audit.read", filter={"outcome": "refused"})["entries"]
                if e["request"]["method"] == "task.create" and e["request"]["params"]["from"] == agent.uri]
    assert len(refusals) == 1 and refusals[0]["outcome"]["code"] == -32063
    assert review_of(base, approver.uri, mid) is None

    # Resumed, the task goes through on the next pass and waits in the desk.
    approver.call("control.resume", scope="participant", participant_uri=agent.uri)
    review = wait_for(lambda: review_of(base, approver.uri, mid))
    assert any("the agent is resumed, task.create accepted" in line for line in run.lines)
    approver.call("decide.approve", task_id=review["task_id"])
    assert run.result() == {mid: "approve"}
    assert (outbox / f"{mid}.json").exists()


def test_the_desk_is_served(desk):
    config, base = desk["config"], desk["base"]
    assert "CHAP review desk" in fetch(f"{base}/")
    assert "export function makeClient" in fetch(f"{base}/chap-client.mjs")
    public = fetch(f"{base}/api/config")
    assert public["workspace"] == config["workspace"] and public["mcp"] is False
    assert {"modes/1.0", "control/1.0"} <= set(public["profiles"])
    described = HttpCoordinator(base + "/chap").describe(config["workspace"])
    assert described["mode"] == "trial" and described["profiles"] == config["profiles"]
    with pytest.raises(urllib.error.HTTPError) as refused:
        fetch(f"{base}/api/tasks/tsk_missing")
    assert refused.value.code == 404
