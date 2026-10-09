"""Pause the agent. The approver runs this; the agent's next task.create is
refused with -32063 until resume.py runs, and the refusal is on the chain.

    python pause.py                     pause with the default reason
    python pause.py waiting for legal   pause with a reason
"""
import sys

from agent import approver, load_config
from chap_client import ChapError


def main(argv: list[str] | None = None) -> None:
    config = load_config()
    reason = " ".join(sys.argv[1:] if argv is None else argv) or "paused by the approver"
    me = approver(config)
    try:
        r = me.call("control.pause", scope="participant", participant_uri=config["agent"]["uri"], reason=reason)
    except ChapError as exc:
        raise SystemExit(f"Not paused: {exc}")
    print(f"Paused {r['participant_uri']} ({reason}). New tasks for it are refused with -32063 until resume.py runs.")


if __name__ == "__main__":
    main()
