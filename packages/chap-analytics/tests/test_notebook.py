"""
The walkthrough notebook runs, and its committed outputs came from a clean run.

Its code cells are executed here in one namespace, in order, with a plain
Python interpreter, so the check needs no kernel and runs in the ordinary
suite. The committed notebook is also required to have been executed without
an error cell, so the outputs a reader sees on GitHub are outputs the code
produced.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("pandas")
pytest.importorskip("chap_coordinator")

NOTEBOOK = Path(__file__).resolve().parents[1] / "examples" / "chap_analytics_walkthrough.ipynb"
BUILDER = Path(__file__).resolve().parents[1] / "examples" / "build_notebook.py"


def cells():
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))["cells"]


def test_every_code_cell_runs_in_order():
    namespace: dict = {"__name__": "__notebook__"}
    for i, cell in enumerate(cells()):
        if cell["cell_type"] != "code":
            continue
        source = "".join(cell["source"])
        try:
            exec(compile(source, f"<cell {i}>", "exec"), namespace)
        except Exception as exc:  # pragma: no cover - the message is the point
            raise AssertionError(f"notebook cell {i} failed: {exc}\n{source}") from exc


def test_the_committed_outputs_are_from_a_clean_run():
    code_cells = [c for c in cells() if c["cell_type"] == "code"]
    assert code_cells, "the notebook has code in it"
    for i, cell in enumerate(code_cells):
        assert cell.get("execution_count") is not None, f"code cell {i} was never executed"
        kinds = {o.get("output_type") for o in cell.get("outputs", [])}
        assert "error" not in kinds, f"code cell {i} carries an error output"


def test_the_notebook_is_the_one_its_builder_writes():
    # The notebook is generated from build_notebook.py, so the prose and code
    # are reviewed there. The committed copy has to match, cell for cell.
    pytest.importorskip("nbformat", reason="the builder writes the notebook with nbformat")
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_notebook", BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    built = module.build()
    committed = cells()
    assert len(built.cells) == len(committed)
    for a, b in zip(built.cells, committed):
        assert a["cell_type"] == b["cell_type"]
        assert a["source"] == "".join(b["source"])
