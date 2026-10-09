"""The support desk, end to end, with no model and no network.

The desk process runs in this process on a free port with the store in
memory. The scripted agent drafts the sample tickets, the human decides
through POST /chap as the desk would, and replies/ holds only what the
human approved. The decisions here are scripted because this is a test; in
the project the decision is made in the desk.
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


def test_replies_hold_only_what_the_human_approved(desk, tmp_path):
    config, base = desk["config"], desk["base"]
    human_uri, agent_uri = config["humans"][0]["uri"], config["agent"]["uri"]
    client = HttpCoordinator(base + "/chap")
    agent = Participant(client, config["workspace"], agent_uri)
    human = Participant(client, config["workspace"], human_uri)
    provider = make_provider(drafter.scripted_reply)
    assert provider.name == "scripted"

    tickets = drafter.read_tickets(ROOT / "tickets.csv")
    assert [t["id"] for t in tickets] == ["T-1001", "T-1002", "T-1003"]
    drafts = {t["id"]: drafter.draft(provider, t) for t in tickets}
    tasks = {t["id"]: drafter.submit(agent, t, drafts[t["id"]]) for t in tickets}

    # Every draft waits in the desk, addressed to the human, with the draft as the artefact.
    reviews = fetch(f"{base}/api/reviews?reviewer={human_uri}")["reviews"]
    assert {r["task_id"] for r in reviews} == set(tasks.values())
    first = next(r for r in reviews if r["task_id"] == tasks["T-1001"])
    assert first["artefact"] == drafts["T-1001"] and first["input"] == tickets[0]
    assert first["reviewers"] == [human_uri] and first["state"] == "review_requested"

    # The agent cannot approve its own work.
    own = agent.send("decide.approve", task_id=tasks["T-1001"])
    assert own["error"]["code"] == -32011

    # The human approves one, overrides one with an RFC 6902 patch, rejects one.
    assert human.call("decide.approve", task_id=tasks["T-1001"], comment="fine")["state"] == "completed"
    overridden = human.call("decide.override", task_id=tasks["T-1002"], rationale="no promise of a date",
                            diff=[{"op": "replace", "path": "/body", "value": "Edited reply"}],
                            intent_preserved=True)
    assert overridden["state"] == "completed" and overridden["applied"]["body"] == "Edited reply"
    assert human.call("decide.reject", task_id=tasks["T-1003"], comment="not ours")["state"] == "declined"

    replies_dir = tmp_path / "replies"
    outcomes = {tid: drafter.settle(agent, task_id, tid, replies_dir, poll_seconds=0.05, timeout=5, log=lambda _: None)
                for tid, task_id in tasks.items()}
    assert outcomes == {"T-1001": "approve", "T-1002": "override", "T-1003": "reject"}

    assert sorted(p.name for p in replies_dir.iterdir()) == ["T-1001.json", "T-1002.json"]
    approved = json.loads((replies_dir / "T-1001.json").read_text(encoding="utf-8"))
    assert approved["reply"] == drafts["T-1001"] and approved["decision"] == "approve"
    assert approved["decided_by"] == human_uri and approved["task_id"] == tasks["T-1001"]
    edited = json.loads((replies_dir / "T-1002.json").read_text(encoding="utf-8"))
    assert edited["reply"] == {**drafts["T-1002"], "body": "Edited reply"} and edited["decision"] == "override"

    # The refused approval is on the chain as a refusal; the accepted decision as a call.
    entries = human.call("audit.read", filter={"task_id": tasks["T-1001"]})["entries"]
    assert any(e.get("outcome") == {"status": "refused", "code": -32011}
               and e["request"]["method"] == "decide.approve" and e["request"]["params"]["from"] == agent_uri
               for e in entries)
    assert any(e.get("envelope", {}).get("method") == "decide.approve" for e in entries)
    assert fetch(f"{base}/api/tasks/{tasks['T-1003']}")["state"] == "declined"
    assert fetch(f"{base}/api/reviews?reviewer={human_uri}")["reviews"] == []


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
    health = fetch(f"{base}/api/health")
    assert health["ok"] is True and health["members"] == 2
    with pytest.raises(urllib.error.HTTPError) as refused:
        fetch(f"{base}/api/tasks/tsk_missing")
    assert refused.value.code == 404
