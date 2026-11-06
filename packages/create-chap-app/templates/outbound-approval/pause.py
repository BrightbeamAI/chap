"""Pause the agent. Its next task.create is refused with -32063 until
resume.py runs, and the refusal is on the chain.

    python pause.py                     pause with the default reason
    python pause.py waiting for legal   pause with a reason

The call is made as the operator's console, service:operator@local: the
approver's key is in the desk's browser, so the log records the pause as
the console's.
"""
import sys

from agent import load_config, operator
from chap_coordinator.client import ChapEndpointError, ChapError


def main(argv: list[str] | None = None) -> None:
    config = load_config()
    reason = " ".join(sys.argv[1:] if argv is None else argv) or "paused from the operator's console"
    try:
        r = operator(config).call("control.pause", scope="participant", participant_uri=config["agent"]["uri"],
                                  reason=reason)
    except (ChapError, ChapEndpointError) as exc:
        raise SystemExit(f"Not paused: {exc}")
    print(f"Paused {r['participant_uri']} ({reason}). New tasks for it are refused with -32063 until resume.py runs.")


if __name__ == "__main__":
    main()
