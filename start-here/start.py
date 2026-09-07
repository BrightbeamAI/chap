"""The one command: python examples/start-here/start.py"""
import argparse
import webbrowser
from pathlib import Path

from chap_starter import ReviewGate, StorageError
from server import AGENTS, ReviewServer, SCENARIOS, write_connection


def main():
    parser = argparse.ArgumentParser(
        description="CHAP: make your first human decision over an agent's draft")
    parser.add_argument("--port", type=int, default=7331,
                        help="Local port. Use 0 to let the OS pick a free one.")
    parser.add_argument("--db", type=Path,
                        default=Path(__file__).with_name(".data") / "browser.db")
    parser.add_argument("--connection", type=Path,
                        default=Path(__file__).with_name(".data") / "agent.json")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    try:
        with ReviewGate(db=args.db, agents=AGENTS) as gate:
            with ReviewServer(("127.0.0.1", args.port), gate) as server:
                if not gate.tasks():
                    first = SCENARIOS["support"]
                    task_id = gate.propose(first["draft"], kind=first["kind"],
                                           context=first["context"], agent=AGENTS[0])
                    server.hints[task_id] = first["hint"]
                write_connection(server, args.connection)
                url = server.base_url + "/#reviewer=" + server.reviewer_token
                print("\nCHAP is ready. Open your private local review link:\n" + url, flush=True)
                print("\nAgent connection:", args.connection.resolve(), flush=True)
                print("Decisions are saved locally. Ctrl-C stops the demo.\n", flush=True)
                if not args.no_browser:
                    webbrowser.open(url)
                try:
                    server.serve_forever(poll_interval=0.25)
                except KeyboardInterrupt:
                    print("\nStopped. Your decisions are still in", args.db.resolve())
    except OSError as exc:
        # Almost always the port. Say the thing that fixes it.
        parser.exit(1, f"Could not start: {exc}\nIf the port is in use, add --port 0.\n")
    except StorageError as exc:
        parser.exit(1, f"Could not start: {exc}\n")


if __name__ == "__main__":
    main()
