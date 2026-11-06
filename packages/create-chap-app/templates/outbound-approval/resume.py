"""Resume the agent after pause.py; its next task.create is accepted again.
The call is made as the operator's console, as pause.py's is.
"""
from agent import load_config, operator
from chap_coordinator.client import ChapEndpointError, ChapError


def main() -> None:
    config = load_config()
    try:
        r = operator(config).call("control.resume", scope="participant", participant_uri=config["agent"]["uri"])
    except (ChapError, ChapEndpointError) as exc:
        raise SystemExit(f"Not resumed: {exc}")
    print(f"Resumed {r['participant_uri']}. The agent's next task.create is accepted.")


if __name__ == "__main__":
    main()
