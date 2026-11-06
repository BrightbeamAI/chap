"""The process that owns the store: the desk, POST /chap and the API.

Run it first, then the agent. Everything it needs is in chap.config.json
beside this file; the server is chap-coordinator's, the same as
`chap-coordinator serve --config chap.config.json`, and takes its options:
`python desk.py --port 9000`.
"""
import sys
from pathlib import Path

try:
    from chap_coordinator.cli import main
except ModuleNotFoundError as exc:  # no chap-coordinator, or one from before the server was in it
    if exc.name not in ("chap_coordinator", "chap_coordinator.cli"):
        raise
    raise SystemExit("This project needs chap-coordinator 0.3.5 or later. "
                     "Run: python -m pip install -r requirements.txt") from exc

if __name__ == "__main__":
    print("The desk runs until you stop it. In a second terminal: python agent.py tickets.csv")
    sys.exit(main(["serve", "--config", str(Path(__file__).resolve().with_name("chap.config.json")), *sys.argv[1:]]))
