"""
The sample week and the export helpers.

The sample is what a new user tries first, so it has to carry every kind of
row the tables have, and the same seed has to give the same chain. The export
helpers are the last step of most analyses, so a round trip through them has
to keep every table and every row.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pandas")
pytest.importorskip("chap_coordinator")

import pandas as pd  # noqa: E402

from chap_analytics import TABLES, frames, sample  # noqa: E402


def test_the_sample_week_carries_every_kind_of_row():
    f = frames(sample.support_desk())
    for table in TABLES:
        if table.name == "routing":
            continue  # the desk has no routing profile, and says so in its profiles list
        assert len(f[table.name]) > 0, f"the sample should give the {table.name} table something to show"
    outcomes = set(f.tasks["outcome"])
    assert {"approved", "overridden", "escalated", "abstained", "open"} <= outcomes
    assert f.tasks["n_reviews"].max() == 2, "one task should have been sent back and reviewed again"
    assert int(f.whispers["lapsed"].sum()) == 1
    assert f.handoffs.iloc[0]["resolution"] == "accepted"


def test_the_same_seed_gives_the_same_chain():
    a = frames(sample.support_desk(3))
    b = frames(sample.support_desk(3))
    for name, table in a:
        drop = [c for c in ("task_id", "whisper_id", "handoff_id", "deliberation_id",
                            "task_ids", "arrived", "supersedes", "prev_hash") if c in table.columns]
        # Ids are minted per run; everything else about the week is fixed.
        pd.testing.assert_frame_equal(table.drop(columns=drop), b[name].drop(columns=drop))


def test_the_envelope_only_sample_is_the_same_week_read_without_state():
    with_state = frames(sample.support_desk())
    from_envelopes = frames(sample.support_desk(envelopes_only=True))
    assert not from_envelopes.chain.has_state and with_state.chain.has_state
    assert from_envelopes.tasks["outcome"].value_counts().to_dict() == \
           with_state.tasks["outcome"].value_counts().to_dict()


def test_iteration_and_as_dict_cover_every_table_in_schema_order():
    f = frames(sample.support_desk())
    names = [name for name, _ in f]
    assert names == [t.name for t in TABLES]
    assert set(f.as_dict()) == set(names)


def test_to_csv_writes_one_file_per_table_with_every_row(tmp_path):
    f = frames(sample.support_desk())
    paths = f.to_csv(str(tmp_path / "week"))
    assert len(paths) == len(TABLES)
    for name, table in f:
        back = pd.read_csv(tmp_path / "week" / f"{name}.csv")
        assert len(back) == len(table), name
        assert list(back.columns) == list(table.columns), name
