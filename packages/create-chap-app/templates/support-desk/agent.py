"""The agent: drafts a reply to each ticket, then waits for the decision.

Run it beside desk.py:

    python agent.py tickets.csv           draft each new ticket as it appears
    python agent.py tickets.csv --once    draft the tickets in the file, then exit

Each ticket becomes one task that requires review. The draft is the artefact
under review, the decision is made in the desk, and the reply as decided,
including an override's patched result, is written to replies/<id>.json. A
rejected draft is written nowhere.

The model comes from the environment (see providers.py). With nothing set,
a scripted drafter writes the reply, and the console says so.
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
TASK_KIND = "draft_reply"
COLUMNS = ("id", "customer", "subject", "body")
Log = Callable[[str], None]


def load_config() -> dict:
    return json.loads((HERE / "chap.config.json").read_text(encoding="utf-8"))


def desk_url(config: dict) -> str:
    """Where desk.py answers, honouring the same PORT and CHAP_HOST overrides."""
    host = os.environ.get("CHAP_HOST") or config.get("host") or "127.0.0.1"
    port = os.environ.get("PORT") or config.get("port") or 8787
    return f"http://{host}:{port}/chap"


# -- tickets ------------------------------------------------------------------

def read_tickets(path: str | os.PathLike) -> list[dict]:
    """The rows of a CSV with the columns id, customer, subject and body.

    Point this at your own export: keep those four column names, or rename
    the columns in your export's header, and every other column is ignored.
    """
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        missing = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise SystemExit(f"{path} needs the columns {', '.join(COLUMNS)}; missing: {', '.join(missing)}")
    return [{c: (row.get(c) or "").strip() for c in COLUMNS} for row in rows if (row.get("id") or "").strip()]


# -- drafting -----------------------------------------------------------------

def prompt_for(ticket: dict) -> str:
    return ("You answer support tickets for a small online shop. Write a short, courteous reply "
            "to the customer below. Plain text, no subject line, no signature block.\n\n"
            f"Customer: {ticket['customer']}\nSubject: {ticket['subject']}\n\n{ticket['body']}\n")


def scripted_reply(prompt: str) -> str:
    """The drafter used when no model is named: a reply built from the ticket text."""
    fields = {}
    for line in prompt.splitlines():
        for key in ("Customer", "Subject"):
            if line.startswith(key + ":"):
                fields[key] = line.split(":", 1)[1].strip()
    customer = fields.get("Customer") or "there"
    subject = fields.get("Subject") or "your message"
    return (f"Hello {customer},\n\nThank you for writing to us about \"{subject}\". I have opened "
            "a case for it and will come back to you within one working day with what we found "
            "and what happens next.\n\nKind regards")


def draft(provider: Provider, ticket: dict) -> dict:
    """The reply the provider drafts, as the artefact the reviewer will see."""
    text, _latency_ms, model_id = provider.complete(prompt_for(ticket))
    return {"ticket_id": ticket["id"], "to": ticket["customer"], "subject": "Re: " + ticket["subject"],
            "body": text.strip(), "drafted_by": model_id}


# -- the two CHAP calls, then the wait ----------------------------------------

def submit(agent: Participant, ticket: dict, reply: dict) -> str:
    """task.create with the ticket as input, then task.complete with the draft.

    Returns the task id. With review/1.0 the completion opens a review and the
    task waits in the desk; the agent cannot approve it itself.
    """
    created = agent.call("task.create", kind=TASK_KIND, input=ticket, assignee=agent.uri, review_required=True)
    agent.call("task.complete", task_id=created["task_id"], output=reply)
    return created["task_id"]


def settle(agent: Participant, task_id: str, ticket_id: str, replies_dir: Path,
           poll_seconds: float = 2.0, timeout: float | None = None, log: Log = print) -> str:
    """Wait for the decision, then write the reply as decided, or nothing.

    Returns the decision: approve, override, reject, or the task's state when
    the review ended another way (a rejection that asked for a revision
    leaves the task in_progress).
    """
    view = agent.wait_for_decision(task_id, poll_seconds=poll_seconds, timeout=timeout)
    decisions = (view.get("review") or {}).get("decisions") or []
    last = decisions[-1] if decisions else {}
    kind = last.get("kind") or view["state"]
    if view["state"] == "completed":
        path = write_reply(replies_dir, ticket_id, task_id, view["output"], last)
        log(f"{ticket_id}: {kind} by {last.get('reviewer')}, written to {path}")
        return kind
    if view["state"] == "declined":
        log(f"{ticket_id}: rejected by {last.get('reviewer')}, nothing written")
        return "reject"
    log(f"{ticket_id}: review ended with the task {view['state']}, nothing written")
    return view["state"]


def write_reply(replies_dir: Path, ticket_id: str, task_id: str, reply: dict, decision: dict) -> Path:
    replies_dir.mkdir(parents=True, exist_ok=True)
    path = replies_dir / f"{ticket_id}.json"
    record = {"ticket_id": ticket_id, "task_id": task_id, "decision": decision.get("kind"),
              "decided_by": decision.get("reviewer"), "reply": reply}
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def process(agent: Participant, provider: Provider, tickets: list[dict], replies_dir: Path,
            poll_seconds: float = 2.0, log: Log = print) -> dict[str, str]:
    """Draft and submit every ticket, then wait for each decision in turn."""
    submitted = []
    for ticket in tickets:
        reply = draft(provider, ticket)
        try:
            task_id = submit(agent, ticket, reply)
        except ChapError as exc:
            log(f"{ticket['id']}: not submitted, {exc}")
            continue
        log(f"{ticket['id']}: drafted by {reply['drafted_by']}, waiting in the desk as {task_id}")
        submitted.append((ticket["id"], task_id))
    outcomes = {}
    for ticket_id, task_id in submitted:
        outcomes[ticket_id] = settle(agent, task_id, ticket_id, replies_dir, poll_seconds, log=log)
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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Draft a reply to each ticket and wait for the decision.")
    parser.add_argument("csv", nargs="?", default="tickets.csv", help="a CSV with id, customer, subject, body")
    parser.add_argument("--once", action="store_true", help="draft the tickets in the file, then exit")
    parser.add_argument("--url", help="the desk process, default from chap.config.json")
    parser.add_argument("--replies", default="replies", help="where decided replies are written")
    parser.add_argument("--poll", type=float, default=2.0, help="seconds between checks for a decision")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)  # each line shows at once, even into a file

    config = load_config()
    provider = make_provider(scripted_reply)
    ok, detail = provider.probe()
    if not ok:
        raise SystemExit(detail)
    print(f"Drafting with {detail}")
    agent = connect(config, args.url or desk_url(config))
    print(f"Agent {agent.uri} on {config['workspace']} at {agent.client.url}")

    replies_dir = Path(args.replies)
    seen: set[str] = set()
    while True:
        batch = []
        for ticket in read_tickets(args.csv):
            if ticket["id"] in seen:
                continue
            seen.add(ticket["id"])
            if (replies_dir / f"{ticket['id']}.json").exists():
                print(f"{ticket['id']}: {replies_dir / (ticket['id'] + '.json')} exists, skipping")
                continue
            batch.append(ticket)
        if batch:
            process(agent, provider, batch, replies_dir, args.poll)
        if args.once:
            break
        time.sleep(args.poll)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
