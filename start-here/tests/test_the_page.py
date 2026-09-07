"""Run the code blocks printed in START_HERE.md.

A front door rots by drifting from what it tells people to type. Prose gets
edited, the snippet underneath does not, and the first thing a newcomer copies
raises before it prints anything. So the page's Python is extracted and
executed here rather than read and trusted.
"""
import re
import subprocess
import sys
import unittest
from pathlib import Path

STARTER = Path(__file__).resolve().parents[1]
PAGE = STARTER.parent / "START_HERE.md"


def python_blocks(markdown):
    return re.findall(r"```python\n(.*?)```", markdown, re.DOTALL)


class StartHerePageTests(unittest.TestCase):
    def setUp(self):
        if not PAGE.exists():                      # a copy of the folder alone
            self.skipTest(f"{PAGE.name} is not beside this folder")
        self.blocks = python_blocks(PAGE.read_text(encoding="utf-8"))

    def test_the_page_still_has_a_python_snippet(self):
        # Without this, every test below would pass by having nothing to run.
        self.assertTrue(self.blocks, "START_HERE.md has no python code block")

    def test_the_snippet_runs_where_the_page_says_to_put_it(self):
        # The page says to save it next to chap_starter.py. If that instruction
        # is ever dropped, the import in the snippet stops resolving.
        for index, block in enumerate(self.blocks):
            with self.subTest(block=index):
                finished = subprocess.run(
                    [sys.executable, "-c", block], cwd=STARTER, input="\n",
                    capture_output=True, text=True, timeout=120,
                )
                self.assertEqual(
                    finished.returncode, 0,
                    f"the snippet raised:\n{finished.stderr}",
                )

    def test_doing_nothing_does_not_traceback(self):
        # Pressing Enter is the safe choice the page recommends. It must print
        # a sentence, not a stack trace, or the recommendation reads as a trap.
        for index, block in enumerate(self.blocks):
            with self.subTest(block=index):
                finished = subprocess.run(
                    [sys.executable, "-c", block], cwd=STARTER, input="\n",
                    capture_output=True, text=True, timeout=120,
                )
                self.assertNotIn("Traceback", finished.stderr)
                self.assertNotIn("ReviewPending", finished.stderr)

    def test_rejecting_stops_without_a_traceback(self):
        for index, block in enumerate(self.blocks):
            with self.subTest(block=index):
                finished = subprocess.run(
                    [sys.executable, "-c", block], cwd=STARTER,
                    input="r\nnot supportable\n",
                    capture_output=True, text=True, timeout=120,
                )
                self.assertEqual(finished.returncode, 0, finished.stderr)
                self.assertNotIn("Traceback", finished.stderr)

    def test_approving_prints_the_reviewed_object(self):
        for index, block in enumerate(self.blocks):
            with self.subTest(block=index):
                finished = subprocess.run(
                    [sys.executable, "-c", block], cwd=STARTER, input="a\n",
                    capture_output=True, text=True, timeout=120,
                )
                self.assertEqual(finished.returncode, 0, finished.stderr)
                self.assertIn("whatever your agent produced", finished.stdout)

    def test_every_command_in_a_shell_block_names_a_real_file(self):
        # Shell blocks are the ready-to-run ones. A path mentioned in prose may
        # be a file the reader is being asked to create, so only the blocks
        # count here.
        page = PAGE.read_text(encoding="utf-8")
        shell = "\n".join(re.findall(r"```(?:sh|bash)\n(.*?)```", page, re.DOTALL))
        commands = re.findall(r"python3? (start-here/[\w./-]+\.py)", shell)
        self.assertTrue(commands, "no runnable command left in START_HERE.md")
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue((STARTER.parent / command).exists(),
                                f"START_HERE.md tells the reader to run {command}")


if __name__ == "__main__":
    unittest.main()
