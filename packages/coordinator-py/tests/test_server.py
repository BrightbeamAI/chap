"""
The desk server in the package: chap_coordinator.server.

Generated projects carried a copy of the server, so a fix never reached a
project already generated, and a Python user with more than one process
copied it from a clone. The copy answered under its own names only and took
JSON only, and sent no Content-Security-Policy, no X-Frame-Options and no
nosniff, so any page could frame the desk, and a page dropped in analytics/
ran in the desk's own origin, where the reviewer's key is kept. Its store
path was read against the directory the server started in, and a ``~`` in
CHAP_DB_PATH made a directory named ``~``.

These tests run the server as the package ships it: on the standard
library's HTTP server, and as an ASGI app under Starlette and FastAPI.
"""
from __future__ import annotations

import asyncio
import http.client
import json
import logging
import os
import re
import socket
import sys
import threading
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import pytest
from fastapi import FastAPI
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route
from starlette.testclient import TestClient

from chap_coordinator.client import HttpCoordinator, Participant, Signer
from chap_coordinator.server import (
    ANALYTICS_POLICY, MAX_BODY_BYTES, SECURITY_HEADERS, ChapDesk, desk_url, load_config, make_coordinator,
    make_server, public_config, read_desk, serve,
)

HUMAN = "human:you@local"
AGENT = "agent:drafter"
DESK_POLICY = SECURITY_HEADERS["content-security-policy"]
#: The Node server, whose headers the desk is built for.
NODE_SERVER = Path(__file__).resolve().parents[2] / "chap-server" / "lib" / "server.mjs"


def write_config(directory: Path, **fields) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    config = {"workspace": "wsp_test", "profiles": ["core/1.0", "review/1.0"], "store": ":memory:",
              "port": 0, "humans": [{"uri": HUMAN, "display_name": "You", "role": "reviewer"}],
              "agent": {"uri": AGENT, "display_name": "The drafter"}, **fields}
    path = directory / "chap.config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


@pytest.fixture
def desk_of(tmp_path, monkeypatch):
    """Start the standard library server on a free port, with the store in
    memory and these fields in chap.config.json; returns its port."""
    for name in ("PORT", "CHAP_HOST", "CHAP_DB_PATH", "CHAP_REQUIRE_SIGNATURES", "CHAP_CHAIN", "CHAP_ALLOWED_HOSTS"):
        monkeypatch.delenv(name, raising=False)
    servers = []

    def start(**fields) -> int:
        config = load_config(write_config(tmp_path / f"project{len(servers)}", **fields))
        server = make_server(config, make_coordinator(config))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server.server_port

    try:
        yield start
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


@pytest.fixture
def desk(tmp_path, monkeypatch):
    """The standard library server on a free port, with the store in memory."""
    for name in ("PORT", "CHAP_HOST", "CHAP_DB_PATH", "CHAP_REQUIRE_SIGNATURES", "CHAP_CHAIN", "CHAP_ALLOWED_HOSTS"):
        monkeypatch.delenv(name, raising=False)
    config = load_config(write_config(tmp_path / "project", chain=True))
    coord = make_coordinator(config)
    server = make_server(config, coord)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield {"config": config, "coord": coord, "port": server.server_port, "ipv6": server.other is not None}
    finally:
        server.shutdown()
        server.server_close()


def request(port: int, method: str, path: str, headers=(), body: bytes = b"", host: str | None = None):
    """One request with exactly these headers; the status, the headers and the body."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    conn.putheader("Host", host if host is not None else f"127.0.0.1:{port}")
    for name, value in headers:
        conn.putheader(name, value)
    if body:
        conn.putheader("Content-Length", str(len(body)))
    conn.endheaders(body or None)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response.status, {k.lower(): v for k, v in response.getheaders()}, data


def call(port: int, method: str, **params):
    envelope = {"jsonrpc": "2.0", "id": method, "method": method, "params": {"workspace": "wsp_test", **params}}
    status, _, data = request(port, "POST", "/chap", [("Content-Type", "application/json")],
                              json.dumps(envelope).encode())
    assert status == 200, data
    return json.loads(data)


def assert_secured(headers: dict, csp: str = DESK_POLICY) -> None:
    assert {k: headers.get(k) for k in SECURITY_HEADERS} == {**SECURITY_HEADERS, "content-security-policy": csp}


# -- configuration --------------------------------------------------------------

def test_the_store_is_found_from_the_config_files_directory_wherever_the_server_starts(tmp_path, monkeypatch):
    monkeypatch.delenv("CHAP_DB_PATH", raising=False)
    project = tmp_path / "project"
    path = write_config(project, store="./data/chap.db", template={"name": "support-desk", "version": "0.3.4"})
    monkeypatch.chdir(tmp_path)
    config = load_config(path)
    assert config["store"] == str(project / "data" / "chap.db")
    assert config["analytics"] == str(project / "analytics")
    assert config["template"] == {"name": "support-desk", "version": "0.3.4"}

    # CHAP_DB_PATH follows the same rule, and the config file comes from
    # CHAP_CONFIG when no path is given.
    monkeypatch.setenv("CHAP_DB_PATH", "other.db")
    monkeypatch.setenv("CHAP_CONFIG", str(path))
    assert load_config()["store"] == str(project / "other.db")
    monkeypatch.setenv("CHAP_DB_PATH", ":memory:")
    assert load_config(path)["store"] == ":memory:"


def test_port_0_in_the_file_picks_a_free_port(tmp_path, monkeypatch):
    monkeypatch.delenv("PORT", raising=False)
    config = load_config(write_config(tmp_path, port=0))
    assert config["port"] == 0
    server = make_server(config, make_coordinator(config))
    try:
        assert server.server_port not in (0, 8787)
    finally:
        server.server_close()
    assert load_config(write_config(tmp_path, port=None))["port"] == 8787
    monkeypatch.setenv("PORT", "8790")
    assert load_config(write_config(tmp_path, port=0))["port"] == 8790


def test_a_leading_tilde_is_the_home_directory(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CHAP_DB_PATH", raising=False)
    path = write_config(tmp_path / "project", store="~/chap.db", analytics="~/pages")
    config = load_config(path)
    assert (config["store"], config["analytics"]) == (str(home / "chap.db"), str(home / "pages"))
    monkeypatch.setenv("CHAP_DB_PATH", "~/chap-review.db")
    monkeypatch.chdir(tmp_path)
    config = load_config(path)
    assert config["store"] == str(home / "chap-review.db")
    make_coordinator(config).options.store.close()
    assert (home / "chap-review.db").exists()
    assert not any(p.name == "~" for p in tmp_path.rglob("*"))


@pytest.mark.parametrize("in_file, in_env, profiles, expected", [
    (True, None, [], (True, "all")),
    ("humans", None, [], (True, "humans")),
    (False, None, [], (False, None)),
    (False, "humans", [], (True, "humans")),
    (True, "0", [], (False, None)),
    ("humans", "true", [], (True, "all")),
    (False, None, ["security-signed/1.0"], (True, "all")),
])
def test_require_signatures_is_true_false_or_humans(tmp_path, monkeypatch, in_file, in_env, profiles, expected):
    if in_env is None:
        monkeypatch.delenv("CHAP_REQUIRE_SIGNATURES", raising=False)
    else:
        monkeypatch.setenv("CHAP_REQUIRE_SIGNATURES", in_env)
    path = write_config(tmp_path, require_signatures=in_file, profiles=["core/1.0", *profiles])
    config = load_config(path)
    assert (config["require_signatures"], config["signature_policy"]) == expected


def test_a_signature_setting_that_says_neither_is_refused(tmp_path, monkeypatch):
    monkeypatch.delenv("CHAP_REQUIRE_SIGNATURES", raising=False)
    with pytest.raises(ValueError, match='is "people"; it takes true, false or "humans"'):
        load_config(write_config(tmp_path, require_signatures="people"))
    monkeypatch.setenv("CHAP_REQUIRE_SIGNATURES", "yes")
    with pytest.raises(ValueError, match="CHAP_REQUIRE_SIGNATURES is 'yes'; it takes 1, true, humans, 0 or false"):
        load_config(write_config(tmp_path, require_signatures=True))


@pytest.mark.parametrize("setting, joined, policy", [
    ("humans", {AGENT}, "humans"),
    (True, set(), "all"),
    (False, {HUMAN, AGENT}, None),
])
def test_the_signature_policy_reaches_the_coordinator_and_decides_who_is_joined(tmp_path, monkeypatch, setting,
                                                                                 joined, policy):
    monkeypatch.delenv("CHAP_REQUIRE_SIGNATURES", raising=False)
    config = load_config(write_config(tmp_path, require_signatures=setting))
    coord = make_coordinator(config)
    ws = coord.get_workspace("wsp_test")
    # The agent calls unsigned under "humans", as a member that is not a
    # person, so it is joined at start; a person joins from the desk with
    # the key the browser makes, and where every call is signed nobody is
    # joined for them.
    assert set(ws.members) == joined
    assert ws.signature_policy == (policy or "all")
    assert ("security-signed/1.0" in ws.profiles) is (policy is not None)
    public = public_config(config)
    assert (public["require_signatures"], public["signature_policy"]) == (policy is not None, policy)


def test_under_humans_the_agent_calls_unsigned_and_a_person_does_not(desk_of):
    port = desk_of(require_signatures="humans")
    # The agent, joined at start, calls unsigned.
    task = call(port, "task.create", **{"from": AGENT}, kind="draft", assignee=AGENT, input={},
                review_required=True)["result"]
    # A person's call is signed, whether or not they have joined.
    stranger = call(port, "task.create", **{"from": "human:stranger@local"}, kind="draft", input={})
    assert stranger["error"]["code"] == -32070
    assert call(port, "participant.join", **{"from": HUMAN}, type="human", role="reviewer")["result"]["joined"]
    assert call(port, "task.complete", **{"from": AGENT}, task_id=task["task_id"],
                output={"body": "hello"})["result"]["state"] == "review_requested"
    assert call(port, "decide.approve", **{"from": HUMAN}, task_id=task["task_id"])["error"]["code"] == -32070


def test_signed_the_persons_decision_is_accepted_under_humans(desk_of, tmp_path):
    port = desk_of(require_signatures="humans")
    person = Participant(HttpCoordinator(f"http://127.0.0.1:{port}/chap"), "wsp_test", HUMAN,
                         Signer.load_or_create(HUMAN, tmp_path / "desk-keys"))
    person.join("human", "reviewer")
    task = call(port, "task.create", **{"from": AGENT}, kind="draft", assignee=AGENT, input={},
                review_required=True)["result"]
    call(port, "task.complete", **{"from": AGENT}, task_id=task["task_id"], output={"body": "hello"})
    assert person.call("decide.approve", task_id=task["task_id"])["state"] == "completed"


def test_a_store_keeps_the_signature_settings_its_workspace_was_made_with(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CHAP_REQUIRE_SIGNATURES", raising=False)
    monkeypatch.delenv("CHAP_DB_PATH", raising=False)
    made = load_config(write_config(tmp_path, require_signatures="humans", store="./data/chap.db"))
    make_coordinator(made).options.store.close()
    capsys.readouterr()
    # Asked for every call signed, or for none, the workspace still takes
    # the agent's calls unsigned, so the configuration says what it does:
    # an agent that read "all" would sign, and its join with a new key
    # would be refused.
    for setting in (True, False):
        config = load_config(write_config(tmp_path, require_signatures=setting, store="./data/chap.db"))
        coord = make_coordinator(config)
        coord.options.store.close()
        assert (config["require_signatures"], config["signature_policy"]) == (True, "humans")
        assert public_config(config)["signature_policy"] == "humans"
        assert ("wsp_test was created under other settings, and runs with signatures required from people. "
                "require_signatures in chap.config.json applies to a new store.") in capsys.readouterr().out
    # As asked, nothing more is said.
    make_coordinator(load_config(write_config(tmp_path, require_signatures="humans",
                                              store="./data/chap.db"))).options.store.close()
    assert "other settings" not in capsys.readouterr().out


def test_a_config_without_a_workspace_or_profiles_is_refused(tmp_path):
    path = tmp_path / "chap.config.json"
    path.write_text(json.dumps({"profiles": ["core/1.0"]}), encoding="utf-8")
    with pytest.raises(ValueError, match="names no workspace"):
        load_config(path)
    path.write_text(json.dumps({"workspace": "wsp_x", "profiles": "core/1.0"}), encoding="utf-8")
    with pytest.raises(ValueError, match="needs profiles"):
        load_config(path)


# -- the standard library server ------------------------------------------------

def test_every_response_carries_the_security_headers(desk):
    port = desk["port"]
    json_post = [("Content-Type", "application/json")]
    for method, path, headers, body, status in [
        ("GET", "/", (), b"", 200),
        ("GET", "/desk.js", (), b"", 200),
        ("GET", "/api/config", (), b"", 200),
        ("GET", "/api/tasks/tsk_missing", (), b"", 404),
        ("POST", "/chap", json_post, b'{"jsonrpc": "2.0", "id": 1, "method": "no.such", "params": {}}', 200),
        ("POST", "/chap", [("Content-Type", "text/plain")], b"{}", 415),
        ("PUT", "/api/config", (), b"", 405),
        ("GET", "/", [("Origin", "http://evil.example")], b"", 403),
    ]:
        got, response_headers, _ = request(port, method, path, headers, body)
        assert got == status, (method, path)
        assert_secured(response_headers)
    got, response_headers, _ = request(port, "GET", "/", host="rebind.evil.example")
    assert got == 421
    assert_secured(response_headers)

    # A method the server has no handler for gets the standard library's
    # own error page, with the same headers.
    with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
        raw.sendall(f"BREW / HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
        head = raw.recv(65536).decode("latin-1").lower()
    assert head.startswith("http/1.0 501")
    for name, value in SECURITY_HEADERS.items():
        assert f"{name}: {value.lower()}" in head


@pytest.mark.skipif(not NODE_SERVER.exists(), reason="the Node server's source is not beside this package")
def test_the_headers_are_the_node_servers():
    # The desk is one set of files, built for the policy the Node server
    # sends, so both servers send the same headers.
    source = NODE_SERVER.read_text(encoding="utf-8")
    block = re.search(r"export const SECURITY_HEADERS = \{(.*?)\};", source, re.S).group(1)
    assert dict(re.findall(r'"([a-z-]+)": "([^"]*)"', block)) == SECURITY_HEADERS
    assert re.search(r'export const ANALYTICS_POLICY = "([^"]*)";', source).group(1) == ANALYTICS_POLICY


@pytest.mark.parametrize("host, opened", [
    ("127.0.0.1", "http://localhost:8788/"), ("localhost", "http://localhost:8788/"),
    ("::1", "http://localhost:8788/"), ("0.0.0.0", "http://localhost:8788/"), ("::", "http://localhost:8788/"),
    ("chap.internal", "http://chap.internal:8788/"), ("fd00::5", "http://[fd00::5]:8788/"),
])
def test_on_this_machine_the_desk_is_opened_at_localhost(host, opened):
    # The desk may sign with WebAuthn, whose relying party is a domain name.
    assert desk_url(host, 8788) == opened


def test_localhost_reaches_the_server_on_either_loopback_address(desk):
    port = desk["port"]
    for origin_host in (f"localhost:{port}", f"127.0.0.1:{port}", f"[::1]:{port}"):
        status, _, _ = request(port, "POST", "/chap", [("Content-Type", "application/json"),
                                                       ("Origin", f"http://{origin_host}")],
                               b'{"jsonrpc": "2.0", "id": 1, "method": "workspace.describe", '
                               b'"params": {"workspace": "wsp_test"}}', host=origin_host)
        assert status == 200, origin_host
    with urllib.request.urlopen(f"http://localhost:{port}/", timeout=10) as page:
        assert b"CHAP review desk" in page.read()
    if not desk["ipv6"]:
        pytest.skip("this machine has no IPv6 loopback address")
    conn = http.client.HTTPConnection("::1", port, timeout=10)
    conn.request("GET", "/api/health", headers={"Host": f"localhost:{port}"})
    response = conn.getresponse()
    assert response.status == 200 and json.loads(response.read())["workspace"] == "wsp_test"
    conn.close()


def test_a_port_taken_on_the_other_loopback_address_is_named(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("PORT", raising=False)
    try:
        taken = socket.socket(socket.AF_INET6)
        taken.bind(("::1", 0))
    except OSError:
        pytest.skip("this machine has no IPv6 loopback address")
    with taken:
        taken.listen()
        port = taken.getsockname()[1]
        assert serve(load_config(write_config(tmp_path, port=port))) == 1
    assert f"Port {port} on ::1 is in use" in capsys.readouterr().err


def test_a_page_under_analytics_runs_in_a_sandbox(desk):
    port, analytics = desk["port"], Path(desk["config"]["analytics"])
    analytics.mkdir()
    (analytics / "report.html").write_text("<script>localStorage.getItem('key')</script>", encoding="utf-8")
    (analytics / "summary.json").write_text('{"tasks": 1}', encoding="utf-8")
    status, headers, body = request(port, "GET", "/analytics/report.html")
    assert status == 200 and b"localStorage" in body
    assert headers["content-type"] == "text/html; charset=utf-8"
    assert_secured(headers, ANALYTICS_POLICY)
    status, headers, body = request(port, "GET", "/analytics/summary.json")
    assert (status, json.loads(body)) == (200, {"tasks": 1})
    assert_secured(headers, ANALYTICS_POLICY)
    # One file in the directory, by name: no separator, drive, escape or dot file.
    for path in ("/analytics/..%2Fchap.config.json", "/analytics/sub%5Cx.html", "/analytics/C:report.html",
                 "/analytics/.hidden.html", "/analytics/%2e%2e/chap.config.json", "/analytics/report.exe"):
        assert request(port, "GET", path)[0] == 404, path


def test_requests_from_elsewhere_are_refused_before_their_body_is_read(desk):
    port = desk["port"]
    json_post = [("Content-Type", "application/json")]
    assert request(port, "GET", "/api/tasks", host="rebind.evil.example:80")[0] == 421
    assert request(port, "GET", "/api/tasks", host="localhost")[0] == 421
    assert request(port, "GET", "/api/tasks", host=f"localhost:{port}")[0] == 200
    for origin in ("http://evil.example", "null", f"http://rebind.example:{port}"):
        assert request(port, "POST", "/chap", [*json_post, ("Origin", origin)], b"{}")[0] == 403, origin
    two_origins = [*json_post, ("Origin", f"http://127.0.0.1:{port}"), ("Origin", "http://evil.example")]
    assert request(port, "POST", "/chap", two_origins, b"{}")[0] == 403
    assert request(port, "GET", "/api/config", [("Origin", f"http://127.0.0.1:{port}")])[0] == 200
    for content_type in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data"):
        assert request(port, "POST", "/chap", [("Content-Type", content_type)], b"{}")[0] == 415
    status, _, body = request(port, "POST", "/chap", [*json_post, ("Content-Length", str(MAX_BODY_BYTES + 1))])
    assert status == 413 and b"over" in body
    assert request(port, "POST", "/chap", [*json_post, ("Content-Length", "ten")])[0] == 400
    status, _, body = request(port, "POST", "/chap", json_post, b"{not json")
    assert (status, json.loads(body)["error"]["code"]) == (400, -32700)
    status, headers, _ = request(port, "OPTIONS", "/chap", [("Access-Control-Request-Method", "POST")])
    assert (status, headers["allow"]) == (405, "GET, POST")


def test_a_fault_reaches_the_client_without_its_text(desk, monkeypatch, caplog):
    def broken(_envelope):
        raise RuntimeError("the store at /home/someone/secret.db is gone")

    monkeypatch.setattr(desk["coord"], "dispatch", broken)
    with caplog.at_level(logging.ERROR, logger="chap_coordinator.server"):
        status, headers, body = request(desk["port"], "POST", "/chap", [("Content-Type", "application/json")],
                                        b'{"jsonrpc": "2.0", "id": 1, "method": "workspace.describe"}')
    assert status == 500 and b"secret" not in body
    assert json.loads(body) == {"error": "internal error; the server's log has the detail"}
    assert_secured(headers)
    assert "secret.db is gone" in caplog.text


def test_calls_and_reads_share_one_lock_so_the_chain_stays_whole(desk):
    port = desk["port"]
    failures: list = []

    def create(n: int) -> None:
        for i in range(20):
            r = call(port, "task.create", **{"from": AGENT}, kind="draft", assignee=AGENT, input={"n": n, "i": i})
            if "error" in r:
                failures.append(r)

    def read() -> None:
        for _ in range(20):
            status, _, _ = request(port, "GET", "/api/tasks")
            if status != 200:
                failures.append(status)

    switch = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        threads = [threading.Thread(target=create, args=(n,)) for n in range(6)]
        threads += [threading.Thread(target=read) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        sys.setswitchinterval(switch)
    assert failures == []
    entries = call(port, "audit.read", **{"from": HUMAN})["result"]["entries"]
    assert [e["seq"] for e in entries] == list(range(3 + 120))
    assert call(port, "audit.verify_chain", **{"from": HUMAN})["result"]["status"] == "verified"
    assert len(desk["coord"].get_workspace("wsp_test").tasks) == 120


class _Scripts(HTMLParser):
    """Each script element's attributes and text, and every event-handler attribute."""

    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[dict] = []
        self.handlers: list[str] = []
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        self.handlers += [name for name, _ in attrs if name.startswith("on")]
        if tag == "script":
            self.scripts.append({**dict(attrs), "text": ""})
            self._in_script = True

    def handle_endtag(self, tag):
        self._in_script = self._in_script and tag != "script"

    def handle_data(self, data):
        if self._in_script:
            self.scripts[-1]["text"] += data


def test_the_desk_is_served_from_the_package_and_runs_under_its_policy(desk):
    port, files = desk["port"], read_desk()
    assert {"index.html", "desk.js", "chap-client.mjs", "desk.css"} <= set(files)
    for name, (content, content_type) in files.items():
        status, headers, body = request(port, "GET", "/" + name)
        assert (status, headers["content-type"], body) == (200, content_type, content), name
    assert b"CHAP review desk" in request(port, "GET", "/")[2]
    # The policy allows no inline script and no eval, so the desk has none.
    page = _Scripts()
    page.feed(files["index.html"][0].decode("utf-8"))
    assert page.scripts and all(s.get("src") and not s["text"].strip() for s in page.scripts)
    assert page.handlers == []
    for name, (content, _) in files.items():
        if name.endswith((".js", ".mjs")):
            assert not re.search(rb"\beval\(|new Function\(|setTimeout\(\s*[\"'`]", content), name


def test_serve_says_so_when_the_port_is_taken(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("PORT", raising=False)
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        config = load_config(write_config(tmp_path, port=port))
        assert serve(config) == 1
    err = capsys.readouterr().err
    assert f"Port {port} on 127.0.0.1 is in use" in err and "set PORT to a free port" in err
    assert "Traceback" not in err


# -- the ASGI app -----------------------------------------------------------------

@pytest.fixture
def asgi(tmp_path, monkeypatch):
    for name in ("PORT", "CHAP_DB_PATH", "CHAP_REQUIRE_SIGNATURES", "CHAP_ALLOWED_HOSTS"):
        monkeypatch.delenv(name, raising=False)
    config = load_config(write_config(tmp_path / "project"))
    return config, make_coordinator(config)


def test_the_asgi_app_makes_the_same_checks_and_sends_the_same_headers(asgi):
    config, coord = asgi
    with TestClient(ChapDesk(config, coord), base_url="http://127.0.0.1:8788") as client:
        page = client.get("/")
        assert page.status_code == 200 and "CHAP review desk" in page.text
        assert_secured(page.headers)
        envelope = {"jsonrpc": "2.0", "id": 1, "method": "workspace.describe", "params": {"workspace": "wsp_test"}}
        described = client.post("/chap", json=envelope)
        assert described.status_code == 200 and described.json()["result"]["id"] == "wsp_test"
        assert_secured(described.headers)
        assert client.get("/api/config", headers={"host": "rebind.evil.example:8788"}).status_code == 421
        assert client.post("/chap", json=envelope, headers={"origin": "http://evil.example"}).status_code == 403
        assert client.post("/chap", content=b"{}", headers={"content-type": "text/plain"}).status_code == 415
        too_large = client.post("/chap", content=b"x" * (MAX_BODY_BYTES + 1), headers={"content-type": "application/json"})
        assert too_large.status_code == 413
        assert client.put("/api/config").status_code == 405
        assert client.get("/api/tasks/tsk_missing").status_code == 404
        Path(config["analytics"]).mkdir()
        Path(config["analytics"], "report.html").write_text("<p>report</p>", encoding="utf-8")
        report = client.get("/analytics/report.html")
        assert report.status_code == 200
        assert_secured(report.headers, ANALYTICS_POLICY)


def test_fastapi_mounts_the_desk_beside_its_own_routes(asgi):
    config, coord = asgi
    app = FastAPI()
    desk = ChapDesk(config, coord)

    @app.get("/healthz")
    def healthz() -> dict:
        with desk.lock:
            return {"tasks": len(coord.get_workspace(config["workspace"]).tasks)}

    app.mount("/", desk)
    with TestClient(app, base_url="http://127.0.0.1:8788") as client:
        made = client.post("/chap", json={"jsonrpc": "2.0", "id": 1, "method": "task.create", "params": {
            "workspace": "wsp_test", "from": AGENT, "kind": "draft", "assignee": AGENT, "input": {}}})
        assert made.json()["result"]["state"] == "created"
        assert client.get("/healthz").json() == {"tasks": 1}
        assert client.get("/api/tasks").json()["tasks"][0]["kind"] == "draft"
        assert "CHAP review desk" in client.get("/").text


class _References(HTMLParser):
    """The URLs a page loads: every src and href."""

    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.urls += [value for name, value in attrs if name in ("src", "href") and value]


def test_mounted_below_a_prefix_the_desk_works_there(asgi):
    config, coord = asgi

    async def own_config(_request):
        return PlainTextResponse("the application's own /api/config")

    desk = ChapDesk(config, coord)
    app = Starlette(routes=[Route("/api/config", own_config), Mount("/review", app=desk)])
    with TestClient(app, base_url="http://127.0.0.1:8788") as client:
        page = client.get("/review/")
        assert page.status_code == 200 and "CHAP review desk" in page.text
        assert_secured(page.headers)
        # Everything the page loads, and every path the desk's scripts call,
        # is relative to the page, and is served under the prefix.
        refs = _References()
        refs.feed(page.text)
        modules = re.findall(r'from "\./([^"]+)"', "".join(body.decode() for name, (body, _) in read_desk().items()
                                                          if name.endswith((".js", ".mjs"))))
        assert refs.urls and modules
        for ref in [*refs.urls, *modules, "api/config", "api/health?desk=1", "api/tasks?brief=1",
                    "api/reviews?reviewer=human%3Ayou%40local"]:
            assert not ref.startswith("/"), ref
            assert client.get(urljoin("/review/", ref)).status_code == 200, ref
        envelope = {"jsonrpc": "2.0", "id": 1, "method": "workspace.describe", "params": {"workspace": "wsp_test"}}
        assert client.post("/review/chap", json=envelope).json()["result"]["id"] == "wsp_test"
        # The application keeps its own routes, with nothing of the desk's.
        own = client.get("/api/config")
        assert own.text == "the application's own /api/config" and "content-security-policy" not in own.headers

    # An ASGI server can hand the app the mount point itself, which names no
    # page: it is sent to the page, with the query kept.
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "GET", "path": "/review", "root_path": "/review", "query_string": b"task=t1",
             "headers": [(b"host", b"127.0.0.1:8788")], "server": ("127.0.0.1", 8788)}
    asyncio.run(desk(scope, receive, send))
    start = sent[0]
    headers = {name.decode(): value.decode() for name, value in start["headers"]}
    assert (start["status"], headers["location"]) == (308, "/review/?task=t1")
    assert_secured(headers)
