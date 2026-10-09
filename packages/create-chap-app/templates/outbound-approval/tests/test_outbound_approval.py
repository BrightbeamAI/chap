"""Outbound approval, end to end, with no model and no network.

The desk process runs in this process on a free port with the store in
memory. The scripted agent drafts the sample messages, the approver decides
through POST /chap as the desk would, and outbox/ holds only what was
approved. The decisions here are scripted because this is a test; in the
project the decision is made in the desk.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["CHAP_MODEL_PROVIDER"] = "scripted"

import agent as drafter  # noqa: E402
from chap_client import ChapError, HttpCoordinator, Participant  # noqa: E402
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


def participants(desk):
    config = desk["config"]
    client = HttpCoordinator(desk["base"] + "/chap")
    return (Participant(client, config["workspace"], config["agent"]["uri"]),
            Participant(client, config["workspace"], config["humans"][0]["uri"]))


def test_only_an_approved_message_reaches_the_outbox(desk, tmp_path):
    config, base = desk["config"], desk["base"]
    agent, approver = participants(desk)
    provider = make_provider(drafter.scripted_body)
    assert provider.name == "scripted"
    quiet = lambda _line: None

    messages = drafter.read_messages(ROOT / "messages.csv")
    assert [m["message_id"] for m in messages] == ["m1", "m2", "m3"]
    drafts = {m["message_id"]: drafter.draft(provider, m) for m in messages}
    tasks = {m["message_id"]: drafter.submit(agent, m, drafts[m["message_id"]], log=quiet) for m in messages}

    # A trial-mode task opens a review although the agent never asked for one.
    view = agent.task(tasks["m1"])
    assert view["state"] == "review_requested"
    assert view["review"]["requested_to"] == [approver.uri]
    reviews = fetch(f"{base}/api/reviews?reviewer={approver.uri}")["reviews"]
    assert {r["task_id"] for r in reviews} == set(tasks.values())
    assert next(r for r in reviews if r["task_id"] == tasks["m1"])["artefact"] == drafts["m1"]

    # The agent cannot approve its own work.
    assert agent.send("decide.approve", task_id=tasks["m1"])["error"]["code"] == -32011

    # The approver approves one, overrides one with an RFC 6902 patch, rejects one.
    assert approver.call("decide.approve", task_id=tasks["m1"], comment="send it")["state"] == "completed"
    overridden = approver.call("decide.override", task_id=tasks["m2"], rationale="shorter",
                               diff=[{"op": "replace", "path": "/body", "value": "Edited body"}],
                               intent_preserved=True)
    assert overridden["applied"]["body"] == "Edited body"
    assert approver.call("decide.reject", task_id=tasks["m3"], comment="not this week")["state"] == "declined"

    outbox = tmp_path / "outbox"
    outcomes = {mid: drafter.settle(agent, task_id, mid, outbox, poll_seconds=0.05, timeout=5, log=quiet)
                for mid, task_id in tasks.items()}
    assert outcomes == {"m1": "approve", "m2": "override", "m3": "reject"}
    assert sorted(p.name for p in outbox.iterdir()) == ["m1.json", "m2.json"]
    sent = json.loads((outbox / "m1.json").read_text(encoding="utf-8"))
    assert sent["message"] == drafts["m1"] and sent["decided_by"] == approver.uri
    edited = json.loads((outbox / "m2.json").read_text(encoding="utf-8"))
    assert edited["message"] == {**drafts["m2"], "body": "Edited body"} and edited["decision"] == "override"

    # The refused approval is on the chain as a refusal; the decisions as calls.
    entries = approver.call("audit.read", filter={"task_id": tasks["m1"]})["entries"]
    assert any(e.get("outcome") == {"status": "refused", "code": -32011}
               and e["request"]["method"] == "decide.approve" and e["request"]["params"]["from"] == agent.uri
               for e in entries)
    assert any(e.get("envelope", {}).get("method") == "decide.approve" for e in entries)


def test_a_paused_agent_is_assigned_nothing_until_resumed(desk):
    agent, approver = participants(desk)
    message = drafter.read_messages(ROOT / "messages.csv")[0]
    body = drafter.draft(make_provider(drafter.scripted_body), message)

    paused = approver.call("control.pause", scope="participant", participant_uri=agent.uri, reason="hold")
    assert paused["paused"] is True
    with pytest.raises(ChapError) as refused:
        drafter.create_task(agent, message)
    assert refused.value.code == -32063

    # The refusal is on the chain, with the agent as the sender.
    refusals = approver.call("audit.read", filter={"outcome": "refused"})["entries"]
    assert any(e["request"]["method"] == "task.create" and e["outcome"]["code"] == -32063
               and e["request"]["params"]["from"] == agent.uri for e in refusals)

    # The agent reports the pause and waits; once resumed, the task goes through.
    lines: list[str] = []
    threading.Timer(0.3, lambda: approver.call("control.resume", scope="participant",
                                               participant_uri=agent.uri)).start()
    task_id = drafter.submit(agent, message, body, poll_seconds=0.05, timeout=5, log=lines.append)
    assert any("refused -32063" in line and "waiting" in line for line in lines)
    assert agent.task(task_id)["state"] == "review_requested"
    assert drafter.create_task(agent, message)  # accepted again after the resume


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
