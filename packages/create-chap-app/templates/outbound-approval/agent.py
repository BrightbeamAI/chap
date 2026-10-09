"""The agent: drafts each outbound message, then waits for the approver.

Run it beside desk.py:

    python agent.py messages.csv           draft each new row as it appears
    python agent.py messages.csv --once    draft the rows in the file, then exit

Each row becomes one task. The agent does not ask for review: the workspace
runs in trial mode under modes/1.0, so the coordinator requires it anyway,
and the draft waits in the desk. The message as decided, including an
override's patched result, is written to outbox/<message id>.json. A
rejected draft is written nowhere.

Under control/1.0 the approver can pause the agent with pause.py. While it
is paused, task.create is refused with -32063; the agent says so and waits
until resume.py runs.

The model comes from the environment (see providers.py). With nothing set,
a scripted drafter writes the body, and the console says so.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
from pathlib import Path
from typing import Callable

from chap_client import ChapError, HttpCoordinator, Participant, Signer
from providers import Provider, make_provider

HERE = Path(__file__).resolve().parent
TASK_KIND = "outbound_message"
COLUMNS = ("to", "subject", "brief")
PAUSED = -32063
Log = Callable[[str], None]


def load_config() -> dict:
    return json.loads((HERE / "chap.config.json").read_text(encoding="utf-8"))


def desk_url(config: dict) -> str:
    """Where desk.py answers, honouring the same PORT and CHAP_HOST overrides."""
    host = os.environ.get("CHAP_HOST") or config.get("host") or "127.0.0.1"
    port = os.environ.get("PORT") or config.get("port") or 8787
    return f"http://{host}:{port}/chap"


# -- messages -----------------------------------------------------------------

def read_messages(path: str | os.PathLike) -> list[dict]:
    """The rows of a CSV with the columns to, subject and brief.

    The row number names the message: the first row is m1, and its approved
    message lands in outbox/m1.json. Point this at your own export: keep
    those three column names, and every other column is ignored.
    """
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        missing = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise SystemExit(f"{path} needs the columns {', '.join(COLUMNS)}; missing: {', '.join(missing)}")
    return [{"message_id": f"m{n}", **{c: (row.get(c) or "").strip() for c in COLUMNS}}
            for n, row in enumerate(rows, start=1) if (row.get("to") or "").strip()]


# -- drafting -----------------------------------------------------------------

def prompt_for(message: dict) -> str:
    return ("Write the body of a short, courteous business email from the brief below. Plain text, "
            "no subject line, no placeholders.\n\n"
            f"To: {message['to']}\nSubject: {message['subject']}\nBrief: {message['brief']}\n")


def scripted_body(prompt: str) -> str:
    """The drafter used when no model is named: a body built from the brief."""
    fields = {}
    for line in prompt.splitlines():
        for key in ("To", "Subject", "Brief"):
            if line.startswith(key + ":"):
                fields[key] = line.split(":", 1)[1].strip()
    name = fields.get("To", "").split("@")[0].split(".")[0].capitalize() or "there"
    brief = fields.get("Brief") or fields.get("Subject") or "the matter below"
    return (f"Hello {name},\n\n{brief}\n\nIf anything here is unclear, reply to this message "
            "and I will sort it out.\n\nBest regards")


def draft(provider: Provider, message: dict) -> dict:
    """The message the provider drafts, as the artefact the approver will see."""
    text, _latency_ms, model_id = provider.complete(prompt_for(message))
    return {"message_id": message["message_id"], "to": message["to"], "subject": message["subject"],
            "body": text.strip(), "drafted_by": model_id}


# -- the CHAP calls, then the wait --------------------------------------------

def create_task(agent: Participant, message: dict) -> str:
    """task.create with the brief as input and no review_required.

    The workspace runs in trial mode, and under modes/1.0 a trial task
    requires review whatever the agent passes. While the agent is paused the
    call is refused with -32063, raised here as ChapError.
    """
    return agent.call("task.create", kind=TASK_KIND, input=message, assignee=agent.uri)["task_id"]


def submit(agent: Participant, message: dict, body: dict, poll_seconds: float = 2.0,
           timeout: float | None = None, log: Log = print) -> str:
    """Create the task, waiting while the agent is paused, then submit the draft.

    Returns the task id. Raises TimeoutError when the pause outlasts ``timeout``.
    """
    started = time.monotonic()
    paused = False
    while True:
        try:
            task_id = create_task(agent, message)
            break
        except ChapError as exc:
            if exc.code != PAUSED:
                raise
            if not paused:
                log(f"{message['message_id']}: {exc.message} (refused {exc.code}); waiting for resume.py")
                paused = True
            if timeout is not None and time.monotonic() - started >= timeout:
                raise TimeoutError(f"Still paused after {timeout} seconds") from exc
            time.sleep(poll_seconds)
    if paused:
        log(f"{message['message_id']}: the agent is resumed, task.create accepted")
    done = agent.call("task.complete", task_id=task_id, output=body)
    if done.get("state") == "review_requested":
        log(f"{message['message_id']}: drafted by {body['drafted_by']}, waiting in the desk as {task_id} "
            "(review opened without review_required: the workspace is in trial mode under modes/1.0)")
    else:
        log(f"{message['message_id']}: task {task_id} is {done.get('state')} with nobody asked")
    return task_id


def settle(agent: Participant, task_id: str, message_id: str, outbox: Path,
           poll_seconds: float = 2.0, timeout: float | None = None, log: Log = print) -> str:
    """Wait for the decision, then write the message as decided, or nothing.

    Returns the decision: approve, override, reject, or the task's state when
    the review ended another way (a rejection that asked for a revision
    leaves the task in_progress).
    """
    view = agent.wait_for_decision(task_id, poll_seconds=poll_seconds, timeout=timeout)
    decisions = (view.get("review") or {}).get("decisions") or []
    last = decisions[-1] if decisions else {}
    kind = last.get("kind") or view["state"]
    if view["state"] == "completed":
        path = write_message(outbox, message_id, task_id, view["output"], last)
        log(f"{message_id}: {kind} by {last.get('reviewer')}, written to {path}")
        return kind
    if view["state"] == "declined":
        log(f"{message_id}: rejected by {last.get('reviewer')}, nothing written")
        return "reject"
    log(f"{message_id}: review ended with the task {view['state']}, nothing written")
    return view["state"]


def write_message(outbox: Path, message_id: str, task_id: str, message: dict, decision: dict) -> Path:
    outbox.mkdir(parents=True, exist_ok=True)
    path = outbox / f"{message_id}.json"
    record = {"message_id": message_id, "task_id": task_id, "decision": decision.get("kind"),
              "decided_by": decision.get("reviewer"), "message": message}
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def process(agent: Participant, provider: Provider, messages: list[dict], outbox: Path,
            poll_seconds: float = 2.0, log: Log = print) -> dict[str, str]:
    """Draft and submit every message, then wait for each decision in turn."""
    submitted = []
    for message in messages:
        body = draft(provider, message)
        try:
            task_id = submit(agent, message, body, poll_seconds, log=log)
        except ChapError as exc:
            log(f"{message['message_id']}: not submitted, {exc}")
            continue
        submitted.append((message["message_id"], task_id))
    outcomes = {}
    for message_id, task_id in submitted:
        outcomes[message_id] = settle(agent, task_id, message_id, outbox, poll_seconds, log=log)
    return outcomes


def connect(config: dict, url: str) -> Participant:
    """The agent's participant, joined with its own key when signatures are required."""
    client = HttpCoordinator(url)
    try:
        client.get("/api/health")
    except (urllib.error.URLError, OSError) as exc:
        raise SystemExit(f"The desk process at {url} is not answering ({exc}). Start it with: python desk.py")
    signer = Signer(config["agent"]["uri"]) if config.get("require_signatures") else None
    agent = Participant(client, config["workspace"], config["agent"]["uri"], signer=signer)
    if signer:
        agent.join("agent", "drafter", config["agent"].get("display_name"))
    return agent


def approver(config: dict, url: str | None = None) -> Participant:
    """The first configured human, for pause.py and resume.py."""
    return Participant(HttpCoordinator(url or desk_url(config)), config["workspace"], config["humans"][0]["uri"])


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Draft each outbound message and wait for the approver.")
    parser.add_argument("csv", nargs="?", default="messages.csv", help="a CSV with to, subject, brief")
    parser.add_argument("--once", action="store_true", help="draft the rows in the file, then exit")
    parser.add_argument("--url", help="the desk process, default from chap.config.json")
    parser.add_argument("--outbox", default="outbox", help="where approved messages are written")
    parser.add_argument("--poll", type=float, default=2.0, help="seconds between checks for a decision")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)  # each line shows at once, even into a file

    config = load_config()
    provider = make_provider(scripted_body)
    ok, detail = provider.probe()
    if not ok:
        raise SystemExit(detail)
    print(f"Drafting with {detail}")
    agent = connect(config, args.url or desk_url(config))
    print(f"Agent {agent.uri} on {config['workspace']} at {agent.client.url}")

    outbox = Path(args.outbox)
    seen: set[str] = set()
    while True:
        batch = []
        for message in read_messages(args.csv):
            if message["message_id"] in seen:
                continue
            seen.add(message["message_id"])
            if (outbox / f"{message['message_id']}.json").exists():
                print(f"{message['message_id']}: {outbox / (message['message_id'] + '.json')} exists, skipping")
                continue
            batch.append(message)
        if batch:
            process(agent, provider, batch, outbox, args.poll)
        if args.once:
            break
        time.sleep(args.poll)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
