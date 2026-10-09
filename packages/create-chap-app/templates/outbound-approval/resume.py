"""Resume the agent after pause.py. The approver runs this; the agent's
next task.create is accepted again.
"""
from agent import approver, load_config
from chap_client import ChapError


def main() -> None:
    config = load_config()
    me = approver(config)
    try:
        r = me.call("control.resume", scope="participant", participant_uri=config["agent"]["uri"])
    except ChapError as exc:
        raise SystemExit(f"Not resumed: {exc}")
    print(f"Resumed {r['participant_uri']}. The agent's next task.create is accepted.")


if __name__ == "__main__":
    main()
