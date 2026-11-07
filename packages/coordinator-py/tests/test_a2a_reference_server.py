"""
reference/a2a-server-py/server.py as a user starts it: the address its card
names, and the skills it offers.

Mirrors reference/a2a-server-ts/tests/server.test.ts.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest

SERVER = Path(__file__).resolve().parents[3] / "reference" / "a2a-server-py" / "server.py"


def _load():
    spec = importlib.util.spec_from_file_location("chap_a2a_reference_server", SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _card(argv: list[str], monkeypatch) -> dict:
    """The card the server serves when started with ``argv``, read without listening."""
    server = _load()
    served = {}
    monkeypatch.setattr(sys, "argv", ["server.py", *argv])
    monkeypatch.setattr(server.uvicorn, "run", lambda app, **options: served.update(app=app, **options))
    assert server.main() == 0
    host = served["host"]
    name = f"[{host}]" if ":" in host else host
    if host in ("0.0.0.0", "::"):
        name = "127.0.0.1"
    base = f"http://{name}:{served['port']}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=served["app"], client=("127.0.0.1", 50000)),
                                 base_url=base) as client:
        return (await client.get("/.well-known/agent-card.json", headers={"A2A-Version": "1.0"})).json()


@pytest.mark.asyncio
@pytest.mark.parametrize("argv, env, url", [
    ([], {}, "http://localhost:9090/"),
    (["--host", "0.0.0.0"], {}, "http://localhost:9090/"),
    (["--host", "192.0.2.10", "--port", "9191"], {}, "http://192.0.2.10:9191/"),
    ([], {"CHAP_HOST": "192.0.2.11"}, "http://192.0.2.11:9090/"),
])
async def test_the_card_names_the_address_the_server_listens_on(argv, env, url, monkeypatch):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    card = await _card(argv, monkeypatch)
    assert {interface["url"] for interface in card["supportedInterfaces"]} == {url}


@pytest.mark.asyncio
async def test_it_offers_the_agents_skills_and_every_skill_with_skills_all(monkeypatch):
    agent = {skill["id"] for skill in (await _card([], monkeypatch))["skills"]}
    assert "chap.task.create" in agent
    for skill in ("chap.workspace.create", "chap.participant.join", "chap.control.pause"):
        assert skill not in agent
    every = {skill["id"] for skill in (await _card(["--skills", "all"], monkeypatch))["skills"]}
    for skill in ("chap.workspace.create", "chap.participant.join", "chap.control.pause"):
        assert skill in every
