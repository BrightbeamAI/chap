"""
The chap-coordinator command.

The package installed no command and had no ``python -m chap_coordinator``,
so a Python user ran the desk server, the MCP server and the A2A server
from copies in a clone. ``chap-coordinator`` runs all three: ``serve`` from
a chap.config.json, and ``mcp`` and ``a2a`` with the options of
chap_coordinator.transports.mcp_cli and a2a_cli.
"""
from __future__ import annotations

import json
import re
import signal
import subprocess
import sys
import time
import urllib.request
from importlib.metadata import entry_points
from pathlib import Path

import pytest

from chap_coordinator import __version__
from chap_coordinator.cli import main


def write_config(directory: Path, **fields) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "chap.config.json"
    path.write_text(json.dumps({"workspace": "wsp_cli", "profiles": ["core/1.0", "review/1.0"],
                                "humans": [{"uri": "human:you@local", "role": "reviewer"}],
                                "agent": {"uri": "agent:drafter"}, **fields}), encoding="utf-8")
    return path


def test_the_package_installs_the_command():
    [script] = entry_points(group="console_scripts", name="chap-coordinator")
    assert script.value == "chap_coordinator.cli:main" and script.load() is main


def test_python_dash_m_is_the_same_command():
    r = subprocess.run([sys.executable, "-m", "chap_coordinator", "--version"], capture_output=True, text=True,
                       timeout=60)
    assert (r.returncode, r.stdout.strip()) == (0, f"chap-coordinator {__version__}")
    r = subprocess.run([sys.executable, "-m", "chap_coordinator"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 2
    assert all(command in r.stderr for command in ("serve", "mcp", "a2a"))


def test_serve_runs_the_desk_from_a_config_file_wherever_it_starts(tmp_path):
    project = tmp_path / "project"
    config = write_config(project, store="./data/chap.db")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    env = {"PATH": "", "HOME": str(tmp_path)}
    process = subprocess.Popen([sys.executable, "-m", "chap_coordinator", "serve", "--config", str(config),
                                "--port", "0"], cwd=elsewhere, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    try:
        banner = [process.stdout.readline() for _ in range(5)]
        base = re.search(r"on (http://127\.0\.0\.1:\d+)", banner[0]).group(1)
        assert banner[4].strip() == f"store    {project / 'data' / 'chap.db'}"
        with urllib.request.urlopen(base + "/api/health", timeout=5) as response:
            assert json.loads(response.read())["workspace"] == "wsp_cli"
    finally:
        process.send_signal(signal.SIGINT)
        process.wait(timeout=30)
    assert process.returncode == 0
    assert (project / "data" / "chap.db").exists() and not (elsewhere / "data").exists()


def test_serve_explains_a_config_it_cannot_use(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CHAP_CONFIG", raising=False)
    monkeypatch.delenv("CHAP_REQUIRE_SIGNATURES", raising=False)
    monkeypatch.chdir(tmp_path)
    assert main(["serve"]) == 2
    assert f"No config file at {tmp_path / 'chap.config.json'}" in capsys.readouterr().err
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    assert main(["serve", "--config", "broken.json"]) == 2
    assert "broken.json is not JSON" in capsys.readouterr().err
    assert main(["serve", "--config", str(write_config(tmp_path, require_signatures="yes"))]) == 2
    assert 'takes true, false or "humans"' in capsys.readouterr().err
    assert main(["serve", "--config", str(write_config(tmp_path)), "--port", "70000"]) == 2
    assert "--port takes a port number" in capsys.readouterr().err


def test_serve_says_so_when_the_store_cannot_be_opened(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CHAP_DB_PATH", raising=False)
    (tmp_path / "a-file").write_text("", encoding="utf-8")
    config = write_config(tmp_path, store="./a-file/chap.db", port=0)
    assert main(["serve", "--config", str(config)]) == 1
    err = capsys.readouterr().err
    assert f"The store at {tmp_path / 'a-file' / 'chap.db'} could not be opened" in err
    assert "Traceback" not in err


def test_mcp_and_a2a_take_their_own_options(capsys):
    assert main(["mcp", "--help"]) == 0
    assert "--as URI" in capsys.readouterr().out
    assert main(["mcp", "--as", "human:you@local"]) == 2
    assert "names a person" in capsys.readouterr().err
    assert main(["a2a", "--help"]) == 0
    assert "--allowed-host NAME" in capsys.readouterr().out


@pytest.mark.parametrize("command, module, missing", [
    ("mcp", "mcp.server.stdio", "mcp"),
    ("a2a", "uvicorn", "uvicorn"),
])
def test_a_command_without_its_extra_names_it(monkeypatch, capsys, command, module, missing):
    monkeypatch.delitem(sys.modules, f"chap_coordinator.transports.{command}_cli", raising=False)
    monkeypatch.setitem(sys.modules, module, None)
    assert main([command]) == 1
    assert capsys.readouterr().err.strip() == (
        f"chap-coordinator {command} needs the {command} extra, and {missing} is not installed. "
        f'Install it with: pip install "chap-coordinator[{command}]"')
