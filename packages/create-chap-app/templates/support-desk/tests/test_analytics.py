"""analytics.py on the store this project's server writes.

The coordinator runs on a SQLite file in a temporary directory; a ticket is
approved, one is edited and one is rejected; the script then writes the
report, the evaluation cases and the refinement page. Skipped where
chap-analytics is not installed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("chap_analytics")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import analytics  # noqa: E402
from chap_server import load_config, make_coordinator  # noqa: E402


def call(coord, workspace, method, **params):
    r = coord.dispatch({"jsonrpc": "2.0", "id": method, "method": method, "params": {"workspace": workspace, **params}})
    assert "error" not in r, r
    return r["result"]


def test_the_pages_come_from_the_store(tmp_path):
    config = load_config(ROOT / "chap.config.json")
    config["store"] = str(tmp_path / "chap.db")
    coord = make_coordinator(config)
    ws, agent, human = config["workspace"], config["agent"]["uri"], config["humans"][0]["uri"]
    tasks = []
    for n, body in enumerate(["Hello, it ships Friday.", "Hello, refund on its way.", "Hello, reset it again."]):
        tid = call(coord, ws, "task.create", **{"from": agent}, kind="draft_reply", assignee=agent,
                   input={"id": f"T-{n}"}, review_required=True)["task_id"]
        call(coord, ws, "task.complete", **{"from": agent}, task_id=tid, output={"to": "Ann", "body": body})
        tasks.append(tid)
    call(coord, ws, "decide.approve", **{"from": human}, task_id=tasks[0])
    call(coord, ws, "decide.override", **{"from": human}, task_id=tasks[1], rationale="no date promised",
         tags=["promise"], intent_preserved=True, diff=[{"op": "replace", "path": "/body", "value": "Hello, we are on it."}])
    call(coord, ws, "decide.reject", **{"from": human}, task_id=tasks[2], comment="security queue", tags=["routing"])
    coord.options.store.close()

    out = tmp_path / "analytics"
    summary = analytics.write_pages(config["store"], ws, out)
    assert summary["tasks"] == 3 and summary["overrides"] == 1
    refine = (out / "refine.md").read_text(encoding="utf-8")
    assert "no date promised" in refine and "security queue" in refine
    assert "<html" in (out / "report.html").read_text(encoding="utf-8").lower()
    cases = [json.loads(line) for line in (out / "cases.jsonl").read_text(encoding="utf-8").splitlines()]
    corrected = next(c for c in cases if c["outcome"] == "overridden")
    assert corrected["corrected_output"]["body"] == "Hello, we are on it."
    assert json.loads((out / "summary.json").read_text(encoding="utf-8"))["workspace"] == ws
