"""Run this twice. The first run leaves a review pending; the second resumes it."""
from pathlib import Path

from chap_starter import ReviewGate, ReviewPending, ReviewRejected, ask_in_terminal

path = Path(__file__).with_name(".data") / "resume.db"
with ReviewGate(db=path) as chap:
    pending = [task for task in chap.tasks() if task["state"] == "review_requested"]
    if not pending:
        task_id = chap.propose({"text": "Ready to release version 1.1."}, kind="release_note")
        print("Saved a pending review:", task_id)
        print("This process is about to exit. Run the same command again to review it.")
    else:
        task_id = pending[0]["task_id"]
        print("Restored the review:", task_id)
        ask_in_terminal(chap, task_id)
        try:
            print("Reviewed output:", chap.result(task_id))
        except (ReviewPending, ReviewRejected) as exc:
            print("Stopped:", exc)
        print("Evidence written to:", chap.export(path.with_suffix(".json")))
