"""Behaviour tests for the starter, against a real coordinator and no mocks.

Every test here is written so that removing the guard it covers turns the suite
red. Nothing asserts on an event count: a count changes whenever the flow
changes for a good reason, which turns the suite into an argument against
fixing things. Assert on the state the guard exists to protect instead.
"""
import json
import os
import stat
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chap_starter import (  # noqa: E402
    ChapError, ReviewGate, ReviewPending, ReviewRejected, StorageError,
    ask_in_terminal, json_diff,
)
from server import (  # noqa: E402
    AGENTS, DEMO_AGENT, PASTE_AGENT, ReviewServer, SCENARIOS, write_connection,
)

DRAFT = {"text": "Your order is guaranteed to arrive tomorrow.", "confident": True}


class GateTests(unittest.TestCase):
    def setUp(self):
        self.gate = ReviewGate(agents=AGENTS)
        self.addCleanup(self.gate.close)
        self.task = self.gate.propose(DRAFT, kind="draft_response",
                                      context={"tracking": "no date"})

    def raw(self, task_id=None):
        """The coordinator's own task record, not the helper's view of it."""
        return self.gate.coordinator.get_workspace(self.gate.workspace).tasks[task_id or self.task]

    # -- the central promise ----------------------------------------------

    def test_nothing_is_completed_before_a_human_decides(self):
        # The task must carry no output and must not be in a completed state
        # while it waits. A flow that calls task.complete to stash the draft
        # records a completion for content nobody has seen; this is the test
        # that refuses it.
        task = self.raw()
        self.assertIsNone(task.output)
        self.assertEqual(task.state, "review_requested")
        methods = [entry["envelope"]["method"] for entry in self.gate.audit()]
        self.assertNotIn("task.complete", methods)

    def test_pending_and_rejected_never_release_a_result(self):
        with self.assertRaises(ReviewPending):
            self.gate.result(self.task)
        self.gate.decide(self.task, "reject", rationale="cannot promise a date")
        with self.assertRaises(ReviewRejected):
            self.gate.result(self.task)
        self.assertIsNone(self.raw().output)

    def test_approve_returns_the_exact_object(self):
        self.gate.decide(self.task, "approve")
        self.assertEqual(self.gate.result(self.task), DRAFT)

    def test_edit_returns_the_edited_object_and_never_the_draft(self):
        edited = {"text": "Your order is on its way. I do not have a date yet."}
        self.gate.decide(self.task, "edit", edited=edited, rationale="no date in tracking")
        self.assertEqual(self.gate.result(self.task), edited)

    def test_an_empty_object_is_a_valid_reviewed_result(self):
        # The obvious bug is `edited or draft`, which silently reinstates the
        # agent's text when a human deletes everything.
        self.gate.decide(self.task, "edit", edited={}, rationale="say nothing")
        self.assertEqual(self.gate.result(self.task), {})

    def test_the_result_is_detached_from_coordinator_state(self):
        self.gate.decide(self.task, "approve")
        result = self.gate.result(self.task)
        result["text"] = "mutated by the caller"
        self.assertEqual(self.gate.result(self.task), DRAFT)

    # -- decisions must be explicit ---------------------------------------

    def test_reject_requires_a_reason(self):
        # Deliberately no `edited`, so the only guard that can fire is the
        # rationale check. A loop that passes edited content for both actions
        # tests the edit guard twice and the reject guard never.
        with self.assertRaises(ValueError):
            self.gate.decide(self.task, "reject", rationale="   ")
        self.assertEqual(self.raw().state, "review_requested")

    def test_edit_requires_a_reason(self):
        with self.assertRaises(ValueError):
            self.gate.decide(self.task, "edit", edited={"text": "x"}, rationale="")
        self.assertEqual(self.raw().state, "review_requested")

    def test_an_unknown_or_blank_action_is_refused(self):
        for action in ("", "yes", "APPROVE", None):
            with self.assertRaises(ValueError):
                self.gate.decide(self.task, action)
        self.assertEqual(self.raw().state, "review_requested")

    def test_approving_with_changed_content_is_refused(self):
        with self.assertRaises(ValueError):
            self.gate.decide(self.task, "approve", edited={"text": "quietly different"})
        self.assertEqual(self.raw().state, "review_requested")

    def test_an_edit_that_changes_nothing_is_refused(self):
        with self.assertRaises(ValueError):
            self.gate.decide(self.task, "edit", edited=dict(DRAFT), rationale="no change")
        self.assertEqual(self.raw().state, "review_requested")

    def test_a_stale_digest_is_refused(self):
        with self.assertRaises(ValueError):
            self.gate.decide(self.task, "approve", expected_digest="sha256:" + "0" * 64)
        self.assertEqual(self.raw().state, "review_requested")

    def test_a_second_decision_is_refused(self):
        self.gate.decide(self.task, "approve")
        with self.assertRaises(ValueError):
            self.gate.decide(self.task, "reject", rationale="changed my mind")
        self.assertEqual(self.gate.result(self.task), DRAFT)

    def test_an_agent_cannot_decide_on_its_own_work(self):
        # The reviewer set is exactly the human, so the coordinator refuses.
        with self.assertRaises(ChapError) as caught:
            self.gate._send("decide.approve", actor=DEMO_AGENT, task_id=self.task,
                            comment="looks fine to me", rationale="looks fine to me",
                            tags=[], approved_artefact_digest=self.gate.inspect(self.task)["digest"])
        self.assertIn("-32011", str(caught.exception))

    def test_blank_terminal_input_is_not_consent(self):
        replies = iter(["", "anything"])
        ask_in_terminal(self.gate, self.task, read=lambda _: next(replies), write=lambda *_: None)
        self.assertEqual(self.raw().state, "review_requested")

    def test_end_of_input_is_not_consent(self):
        def eof(_):
            raise EOFError
        ask_in_terminal(self.gate, self.task, read=eof, write=lambda *_: None)
        self.assertEqual(self.raw().state, "review_requested")

    # -- what may enter the log -------------------------------------------

    def test_an_unregistered_agent_cannot_propose(self):
        with self.assertRaises(ValueError):
            self.gate.propose({"text": "hi"}, agent="agent:not-joined")

    def test_the_pasting_agent_is_a_separate_identity(self):
        other = self.gate.propose({"text": "pasted"}, agent=PASTE_AGENT)
        self.assertEqual(self.gate.inspect(other)["author"], PASTE_AGENT)
        self.assertNotEqual(self.gate.inspect(other)["author"],
                            self.gate.inspect(self.task)["author"])

    def test_an_unsafe_number_is_refused_before_the_task_exists(self):
        before = len(self.gate.tasks())
        with self.assertRaises(Exception):
            self.gate.propose({"amount": 12.5})
        self.assertEqual(len(self.gate.tasks()), before)

    def test_a_runaway_kind_is_refused(self):
        with self.assertRaises(ValueError):
            self.gate.propose({"text": "hi"}, kind="x" * 500)

    def test_a_non_object_draft_is_refused(self):
        for draft in ("just text", ["a"], 3, None):
            with self.assertRaises(ValueError):
                self.gate.propose(draft)

    def test_booleans_and_numbers_are_not_treated_as_equal(self):
        # Python says True == 1 and 1.0 == 1; JSON does not. A diff built on
        # Python equality silently drops these edits.
        self.assertTrue(json_diff({"flag": True}, {"flag": 1}))
        self.assertTrue(json_diff({"n": [1, True]}, {"n": [1, 1]}))
        self.assertEqual(json_diff({"flag": True}, {"flag": True}), [])

    def test_a_key_containing_a_slash_is_escaped_in_the_patch(self):
        patch = json_diff({"a/b": 1}, {"a/b": 2})
        self.assertEqual(patch, [{"op": "replace", "path": "/a~1b", "value": 2}])

    # -- the chain ---------------------------------------------------------

    def test_tampering_blocks_the_result(self):
        self.gate.decide(self.task, "approve")
        entries = self.gate.coordinator.get_workspace(self.gate.workspace).audit
        entries[-1].prev_hash = "sha256:" + "0" * 64
        with self.assertRaises(ChapError):
            self.gate.result(self.task)

    def test_verdict_reports_a_failure_instead_of_raising(self):
        # A UI cannot render a raised exception as a verdict, so it renders the
        # last good one, and a stale green tick is worse than no tick. verdict()
        # must always answer, and must never answer "verified" when it is not.
        self.assertEqual(self.gate.verdict()["status"], "verified")
        entries = self.gate.coordinator.get_workspace(self.gate.workspace).audit
        entries[-1].prev_hash = "sha256:" + "0" * 64
        verdict = self.gate.verdict()
        self.assertIsInstance(verdict, dict)
        self.assertNotEqual(verdict.get("status"), "verified")
        self.assertIsNot(verdict.get("ok"), True)

    def test_export_carries_the_envelopes_and_the_verdict(self):
        self.gate.decide(self.task, "edit", edited={"text": "safer"}, rationale="no date")
        with TemporaryDirectory() as folder:
            path = self.gate.export(Path(folder) / "evidence.json")
            body = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(body["verification"]["status"], "verified")
        self.assertIn("decide.override", [e["envelope"]["method"] for e in body["entries"]])


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.gate = ReviewGate(agents=AGENTS)
        self.addCleanup(self.gate.close)
        self.server = ReviewServer(("127.0.0.1", 0), self.gate)
        self.addCleanup(self.server.server_close)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.shutdown)
        self.base = self.server.base_url

    def request(self, path, *, method="GET", body=None, headers=None, host=None, origin=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("Host", host or f"127.0.0.1:{self.server.server_port}")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if origin:
            request.add_header("Origin", origin)
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        def decode(payload):
            try:
                return json.loads(payload or b"{}")
            except json.JSONDecodeError:
                return {"raw": payload.decode(errors="replace")}

        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, decode(response.read()), dict(response.headers)
        except urllib.error.HTTPError as error:
            return error.code, decode(error.read()), dict(error.headers)

    def reviewer(self):
        return {"X-CHAP-Reviewer": self.server.reviewer_token}

    def agent(self):
        return {"X-CHAP-Agent": self.server.agent_token}

    def propose(self):
        status, body, _ = self.request("/api/proposals", method="POST", headers=self.agent(),
                                       body={"kind": "draft_response", "draft": dict(DRAFT)})
        self.assertEqual(status, 201, body)
        return body["task_id"]

    # -- capabilities ------------------------------------------------------

    def test_the_desk_needs_the_reviewer_capability(self):
        self.assertEqual(self.request("/api/desk")[0], 403)
        self.assertEqual(self.request("/api/desk", headers=self.agent())[0], 403)
        self.assertEqual(self.request("/api/desk", headers=self.reviewer())[0], 200)

    def test_the_agent_capability_cannot_decide(self):
        task_id = self.propose()
        status, _, _ = self.request(f"/api/reviews/{task_id}/decision", method="POST",
                                    headers=self.agent(),
                                    body={"action": "approve", "expected_digest": "x"})
        self.assertEqual(status, 403)
        self.assertEqual(self.gate.inspect(task_id)["state"], "review_requested")

    def test_a_wrong_host_header_is_refused(self):
        self.assertEqual(self.request("/api/desk", headers=self.reviewer(),
                                      host="attacker.example")[0], 403)

    def test_a_cross_origin_request_is_refused(self):
        self.assertEqual(self.request("/api/desk", headers=self.reviewer(),
                                      origin="https://attacker.example")[0], 403)

    def test_every_response_carries_the_hardening_headers(self):
        for path, headers in (("/", {}), ("/api/desk", self.reviewer())):
            _, _, received = self.request(path, headers=headers)
            policy = received.get("Content-Security-Policy", "")
            for directive in ("script-src 'self'", "object-src 'none'",
                              "frame-ancestors 'none'", "base-uri 'none'"):
                self.assertIn(directive, policy)
            self.assertEqual(received.get("X-Content-Type-Options"), "nosniff")
            self.assertEqual(received.get("Referrer-Policy"), "no-referrer")
            self.assertEqual(received.get("Cache-Control"), "no-store")

    @unittest.skipIf(os.name == "nt", "POSIX file modes")
    def test_the_connection_file_is_readable_only_by_its_owner(self):
        path = write_connection(self.server, Path(self.folder.name) / "agent.json")
        mode = stat.S_IMODE(path.stat().st_mode)
        self.assertEqual(mode & 0o077, 0, f"mode is {oct(mode)}; group or others can read the token")

    def test_the_connection_file_never_carries_the_reviewer_capability(self):
        path = write_connection(self.server, Path(self.folder.name) / "agent.json")
        self.assertNotIn(self.server.reviewer_token, path.read_text(encoding="utf-8"))

    # -- attribution and guidance -----------------------------------------

    def test_a_pasted_draft_is_recorded_under_its_own_agent(self):
        status, body, _ = self.request("/api/drafts", method="POST", headers=self.reviewer(),
                                       body={"kind": "your_workflow", "draft": {"text": "mine"}})
        self.assertEqual(status, 201, body)
        self.assertEqual(body["author"], PASTE_AGENT)
        self.assertEqual(self.gate.inspect(self.propose())["author"], DEMO_AGENT)

    def test_the_draft_author_cannot_choose_the_reviewers_guidance(self):
        # The hint tells the reviewer what to look for. If it were keyed off
        # anything in the draft, an agent could pick the guidance shown beside
        # its own work.
        borrowed = SCENARIOS["support"]["kind"]
        status, body, _ = self.request("/api/proposals", method="POST", headers=self.agent(),
                                       body={"kind": borrowed, "draft": {"to": "attacker@example.com"}})
        self.assertEqual(status, 201, body)
        _, desk, _ = self.request(f"/api/desk?task={body['task_id']}", headers=self.reviewer())
        self.assertIsNone(desk["hint"])

    def test_the_server_assigns_the_actor(self):
        status, body, _ = self.request("/api/proposals", method="POST", headers=self.agent(),
                                       body={"draft": {"text": "x"}, "from": "human:you@local"})
        self.assertEqual(status, 400, body)

    # -- the desk payload --------------------------------------------------

    def test_the_desk_reports_a_broken_chain_rather_than_failing(self):
        self.propose()
        entries = self.gate.coordinator.get_workspace(self.gate.workspace).audit
        entries[-1].prev_hash = "sha256:" + "0" * 64
        status, body, _ = self.request("/api/desk", headers=self.reviewer())
        self.assertEqual(status, 200, body)
        self.assertIn("verification", body)
        self.assertNotEqual(body["verification"].get("status"), "verified")

    def test_the_desk_payload_does_not_grow_with_the_log(self):
        self.propose()
        _, small, _ = self.request("/api/desk", headers=self.reviewer())
        for _ in range(25):
            self.propose()
        _, large, _ = self.request("/api/desk", headers=self.reviewer())
        self.assertLessEqual(len(large["entries"]), len(small["entries"]) + 2)
        self.assertNotIn("draft", large["tasks"][0])

    def test_a_pending_result_is_a_conflict_not_a_draft(self):
        task_id = self.propose()
        status, body, _ = self.request(f"/api/tasks/{task_id}/result", headers=self.agent())
        self.assertEqual(status, 409)
        self.assertNotIn("output", body)

    def test_a_rejected_result_is_a_conflict(self):
        task_id = self.propose()
        self.gate.decide(task_id, "reject", rationale="no")
        status, body, _ = self.request(f"/api/tasks/{task_id}/result", headers=self.agent())
        self.assertEqual(status, 409)
        self.assertIn("reject", body["error"].lower())

    def test_reading_the_desk_does_not_grow_the_log(self):
        before = len(self.gate.audit())
        for _ in range(5):
            self.request("/api/desk", headers=self.reviewer())
        self.assertEqual(len(self.gate.audit()), before)

    def test_an_unexpected_failure_still_answers_with_a_status(self):
        # A demo that drops the connection teaches nothing, and a client that
        # sees a closed socket cannot tell a bug from a crash.
        original = self.gate.tasks
        self.gate.tasks = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        self.addCleanup(setattr, self.gate, "tasks", original)
        with open(os.devnull, "w", encoding="utf-8") as quiet:
            saved, sys.stderr = sys.stderr, quiet      # the handler prints the traceback
            try:
                status, body, _ = self.request("/api/desk", headers=self.reviewer())
            finally:
                sys.stderr = saved
        self.assertEqual(status, 500)
        self.assertNotIn("boom", json.dumps(body))

    def test_an_unknown_decision_field_is_refused(self):
        task_id = self.propose()
        digest = self.gate.inspect(task_id)["digest"]
        status, body, _ = self.request(f"/api/reviews/{task_id}/decision", method="POST",
                                       headers=self.reviewer(),
                                       body={"action": "approve", "expected_digest": digest,
                                             "reviewer": "human:someone-else"})
        self.assertEqual(status, 400)
        # Python's own signature check would also refuse this today, which is
        # why the assertion names the allowlist: the guard has to keep working
        # if decide() ever grows a parameter with one of these names.
        self.assertIn("Unknown decision field", body.get("error", ""))
        self.assertEqual(self.gate.inspect(task_id)["state"], "review_requested")

    def test_a_decision_without_a_digest_is_refused(self):
        task_id = self.propose()
        status, _, _ = self.request(f"/api/reviews/{task_id}/decision", method="POST",
                                    headers=self.reviewer(), body={"action": "approve"})
        self.assertEqual(status, 400)
        self.assertEqual(self.gate.inspect(task_id)["state"], "review_requested")

    def test_the_end_to_end_browser_flow(self):
        task_id = self.propose()
        digest = self.gate.inspect(task_id)["digest"]
        edited = {"text": "Your order is on its way. No date yet.", "confident": False}
        status, _, _ = self.request(f"/api/reviews/{task_id}/decision", method="POST",
                                    headers=self.reviewer(),
                                    body={"action": "edit", "edited": edited,
                                          "rationale": "tracking has no date",
                                          "tags": ["unsupported-claim"],
                                          "expected_digest": digest})
        self.assertEqual(status, 200)
        status, body, _ = self.request(f"/api/tasks/{task_id}/result", headers=self.agent())
        self.assertEqual(status, 200)
        self.assertEqual(body["output"], edited)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "chap.db"

    def test_a_pending_review_and_its_decision_survive_new_processes(self):
        with ReviewGate(db=self.path) as first:
            task_id = first.propose(DRAFT)
        with ReviewGate(db=self.path) as second:
            self.assertEqual(second.inspect(task_id)["state"], "review_requested")
            second.decide(task_id, "edit", edited={"text": "safer"}, rationale="no date")
        with ReviewGate(db=self.path) as third:
            self.assertEqual(third.result(task_id), {"text": "safer"})
            self.assertEqual(third.verdict()["status"], "verified")

    def test_a_second_writer_is_refused(self):
        with ReviewGate(db=self.path):
            with self.assertRaises(StorageError):
                ReviewGate(db=self.path)

    def test_a_file_that_is_not_a_database_says_so(self):
        # A newcomer who points --db at the wrong file, or whose store was
        # truncated, should read a sentence rather than a stack trace.
        self.path.write_text("this is not a database", encoding="utf-8")
        with self.assertRaises(StorageError) as caught:
            ReviewGate(db=self.path)
        # The gate reports the resolved path; Windows hands out an 8.3 short
        # form for the temporary directory, so resolve both sides.
        self.assertIn(str(self.path.resolve()), str(caught.exception))

    def test_a_database_from_a_different_setup_says_what_to_do(self):
        with ReviewGate(db=self.path, agents=("agent:demo",)):
            pass
        with self.assertRaises(StorageError) as caught:
            ReviewGate(db=self.path, agents=("agent:something-else",))
        self.assertIn(str(self.path.resolve()), str(caught.exception))

    def test_a_failed_save_blocks_the_result(self):
        with ReviewGate(db=self.path) as gate:
            task_id = gate.propose(DRAFT)
            gate.decide(task_id, "approve")
            gate._store.failure = OSError("disk full")
            with self.assertRaises(StorageError):
                gate.result(task_id)


if __name__ == "__main__":
    unittest.main()
