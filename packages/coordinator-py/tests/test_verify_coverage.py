"""
Coverage-reporting tests for audit.verify_chain (issue #76).

A chain replay proves the entries it covers were not altered. It proves
nothing about entries written before chaining was switched on, because no
stored hash reaches them. Before this fix the verdict for a log with an
unchained prefix was ok=True with a smaller entries_checked beside it, so
a caller reading ok alone took a pass over entries nothing had checked.

The three verdicts are terminal and mutually exclusive: a broken chain is
a JSON-RPC error, an unevaluated range is "not_evaluated" with ok=False,
and "verified" requires complete coverage. These cases are mirrored one
for one in tests/verify_coverage.test.ts.
"""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.canonical import ZERO_HASH

CHAINED = ["core/1.0", "review/1.0", "audit-scitt/1.0"]


def _make(profiles):
    c = Coordinator(CoordinatorOptions(default_profiles=profiles))

    def s(method, **params):
        return c.dispatch({"jsonrpc": "2.0", "id": method, "method": method, "params": params})

    s("workspace.create", workspace="w", profiles=profiles)
    s("participant.join", workspace="w", **{"from": "human:a"}, type="human", role="admin")
    s("participant.join", workspace="w", **{"from": "agent:bot"}, type="agent")
    return c, s


def _task(s, name):
    s("task.create", workspace="w", **{"from": "agent:bot"}, task=name, intent=name)


def _enable_chain_mid_life(s):
    """Enables chaining part-way through a workspace's life.

    This is the supported route, not a poke at internals:
    workspace.set_profiles turns the chain on when audit-scitt/1.0 is
    added, which is exactly how a real deployment reaches this state.
    """
    r = s("workspace.set_profiles", workspace="w", **{"from": "human:a"},
          profiles=["core/1.0", "review/1.0", "audit-scitt/1.0"])
    assert "result" in r, r


def _verdict(s):
    r = s("audit.verify_chain", workspace="w")
    assert "result" in r, f"expected a result, got {r}"
    return r["result"]


def test_a_fully_covered_chain_verifies_and_says_so():
    c, s = _make(CHAINED)
    _task(s, "t1")
    _task(s, "t2")
    v = _verdict(s)
    assert v["status"] == "verified"
    assert v["ok"] is True
    assert v["entries_unchecked"] == 0
    assert v["entries_checked"] == v["entries_total"]
    assert "reason" not in v, "a pass carries no reason"


def test_an_unchained_prefix_is_not_evaluated_never_a_pass():
    c, s = _make(["core/1.0", "review/1.0"])
    _task(s, "t1")
    _task(s, "t2")
    before = len(c.workspaces["w"].audit)
    _enable_chain_mid_life(s)
    _task(s, "t3")

    v = _verdict(s)
    assert v["status"] == "not_evaluated"
    assert v["ok"] is False, "the whole point: ok must not be True here"
    assert v["reason"] == "unchained_prefix"
    assert v["entries_unchecked"] == before
    assert v["entries_checked"] < v["entries_total"]


def test_the_enabling_call_is_itself_the_first_checked_entry():
    # set_profiles is an audited call, so switching the chain on writes an
    # entry that the chain then covers. Coverage therefore always begins at
    # the enabling call, and everything before it stays outside.
    c, s = _make(["core/1.0", "review/1.0"])
    _task(s, "t1")
    _task(s, "t2")
    before = len(c.workspaces["w"].audit)
    _enable_chain_mid_life(s)

    v = _verdict(s)
    assert v["status"] == "not_evaluated"
    assert v["ok"] is False
    assert v["entries_checked"] == 1, "only the enabling call is covered"
    assert v["entries_unchecked"] == before
    assert v["checked_from_seq"] == c.workspaces["w"].audit[before].seq


def test_a_log_with_no_chained_entries_checks_nothing():
    # Not reachable through the public API, since the enabling call is
    # itself chained. It is reachable by restoring a store whose chain flag
    # outlived its chained entries, so the zero-coverage verdict is pinned
    # here rather than left to chance.
    c, s = _make(["core/1.0", "review/1.0"])
    _task(s, "t1")
    ws = c.workspaces["w"]
    ws.chain_enabled = True
    ws.chain_head = ZERO_HASH

    v = _verdict(s)
    assert v["status"] == "not_evaluated"
    assert v["ok"] is False
    assert v["entries_checked"] == 0
    assert v["checked_from_seq"] is None, "nothing was checked, so no start seq"
    assert v["entries_unchecked"] == v["entries_total"]


def test_an_empty_chained_log_verifies_vacuously():
    # Constructed boundary: a chained workspace whose log holds nothing.
    # Normal use never reaches it, because workspace.create is itself an
    # audited call, so the emptiness has to be made deliberately. It is
    # worth pinning: an empty log has nothing to contradict its ZERO_HASH
    # head, so a pass is correct, and the counts must still add up.
    c, s = _make(CHAINED)
    c.workspaces["w"].audit.clear()
    c.workspaces["w"].chain_head = ZERO_HASH
    v = _verdict(s)
    assert v["status"] == "verified"
    assert v["ok"] is True
    assert v["entries_total"] == 0
    assert v["entries_checked"] == 0
    assert v["entries_unchecked"] == 0
    assert v["checked_from_seq"] is None


def test_a_broken_chain_is_still_an_error_not_a_verdict():
    c, s = _make(CHAINED)
    _task(s, "t1")
    _task(s, "t2")
    c.workspaces["w"].audit[1].envelope["params"]["intent"] = "TAMPERED"
    r = s("audit.verify_chain", workspace="w")
    assert "error" in r, "tampering must not be downgraded to not_evaluated"


def test_coverage_counts_are_internally_consistent_in_every_verdict():
    def full():
        c, s = _make(CHAINED)
        _task(s, "a")
        return c, s

    def prefix():
        c, s = _make(["core/1.0"])
        _task(s, "a")
        _enable_chain_mid_life(s)
        _task(s, "b")
        return c, s

    def nothing():
        c, s = _make(["core/1.0"])
        _task(s, "a")
        _enable_chain_mid_life(s)
        return c, s

    for build in (full, prefix, nothing):
        c, s = build()
        v = _verdict(s)
        assert v["entries_checked"] + v["entries_unchecked"] == v["entries_total"], \
            "checked plus unchecked must account for the whole log"
        assert v["ok"] is (v["status"] == "verified"), "ok and status must never disagree"
