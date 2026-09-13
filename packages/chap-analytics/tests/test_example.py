"""
The worked example runs, and what it prints is true of the chain it built.

An example that runs in the suite stays true. This drives it the way a
reader would, checks that every section reached the output, and reloads the
export it wrote through the public loader so the JSON path is exercised on a
realistic chain.
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

import pytest

pytest.importorskip("pandas")
pytest.importorskip("chap_coordinator")

from chap_analytics import frames, from_json  # noqa: E402

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "support_desk.py"
SECTIONS = [
    "1. How the week ended", "2. What reviewers keep correcting", "3. Why they corrected it",
    "4. Refining the draft, or reversing it", "5. Does the agent's confidence mean anything?",
    "6. The reviewers", "7. Time to a decision", "8. Questions and handoffs",
    "9. The same tables from audit.read alone", "10. With the content redacted",
]


def run_example(capsys, tmp_path, *args: str):
    export = tmp_path / "week.json"
    argv = sys.argv
    sys.argv = [str(EXAMPLE), "--export", str(export), *args]
    try:
        with pytest.raises(SystemExit) as stop:
            runpy.run_path(str(EXAMPLE), run_name="__main__")
    finally:
        sys.argv = argv
    assert stop.value.code == 0
    return capsys.readouterr().out, export


def test_the_example_runs_to_the_end_and_prints_every_section(capsys, tmp_path):
    out, _ = run_example(capsys, tmp_path)
    for heading in SECTIONS:
        assert heading in out, f"section missing from the output: {heading}"
    assert "Outcome counts identical across the two reads: True" in out
    assert "based_on on the first override: None" in out


def test_the_export_reloads_through_from_json_and_says_the_same(capsys, tmp_path):
    out, export = run_example(capsys, tmp_path)
    f = frames(from_json(str(export)))
    assert not f.chain.has_state
    assert f.chain.workspace == "wsp_support"
    counts = f.tasks["outcome"].value_counts()
    for outcome in ("approved", "overridden", "escalated", "abstained", "open"):
        assert counts.get(outcome, 0) > 0, f"the week should contain an {outcome} task"
    assert len(f.handoffs) == 1 and f.handoffs.iloc[0]["resolution"] == "accepted"
    assert int(f.whispers["lapsed"].sum()) == 1
    assert (f.decisions["latency_s"].dropna() >= 0).all()


def test_the_example_is_deterministic_for_a_seed(capsys, tmp_path):
    first, _ = run_example(capsys, tmp_path, "--seed", "3")
    second, _ = run_example(capsys, tmp_path, "--seed", "3")
    assert first == second
