"""Put a human in front of a tool call. The only effect here is a local file."""
import json
from pathlib import Path

from chap_starter import ReviewGate, ReviewPending, ReviewRejected, ask_in_terminal


def prepare_email(arguments):
    """The guarded function. It only ever sees arguments a human approved."""
    if set(arguments) != {"to", "subject", "body"} or any(
        not isinstance(value, str) or not value.strip() for value in arguments.values()
    ):
        raise ValueError("Expected non-empty to, subject and body strings")
    path = Path(__file__).with_name(".data") / "reviewed-email.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(arguments, indent=2) + "\n", encoding="utf-8")
    print("Prepared locally:", path, "(this demo sends no email)")


with ReviewGate() as chap:
    task = chap.propose(
        {"to": "customer@example.com", "subject": "Your delivery",
         "body": "Your order is guaranteed to arrive tomorrow."},
        kind="prepare_email",
        context={"tracking_status": "in_transit", "arrival_date": "unknown"},
    )
    ask_in_terminal(chap, task)
    try:
        reviewed = chap.result(task)
    except (ReviewPending, ReviewRejected) as exc:
        print("The function was not called:", exc)
    else:
        prepare_email(reviewed)   # Always the reviewed arguments, never the draft.
