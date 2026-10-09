"""The agent: drafts a reply to each ticket, then waits for the decision.

Run it beside desk.py:

    python agent.py tickets.csv           draft each new ticket as it appears
    python agent.py tickets.csv --once    draft the tickets in the file, wait for
                                          their decisions, then exit

Each ticket becomes one task that requires review. The draft is the artefact
under review, the decision is made in the desk, and the reply as decided,
including an override's patched result, is written to replies/<ticket id>.json.
A rejected draft is written nowhere. A rejection that asks for a revision has
the agent draft again with the reviewer's note and submit the new draft.

The ticket id is the task's idempotency key, so a restarted agent finds the
tasks it opened before and opens no duplicates. Before it submits a draft
the agent checks on workspace.describe, a read, that a reviewer has joined:
a completion with nobody to address the review to is refused and the refusal
recorded, so the agent waits instead and says so once.

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
REVISION_LINE = "The reviewer asked for a revision"
PAUSED = -32063
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

def prompt_for(ticket: dict, revision: str | None = None) -> str:
    lines = [
        "You answer support tickets for a small online shop. Write a short, courteous reply "
        "to the customer below. Plain text, no subject line, no signature block.",
        "",
        f"Customer: {ticket['customer']}",
        f"Subject: {ticket['subject']}",
        "",
        ticket["body"],
    ]
    if revision:
        lines += ["", f"{REVISION_LINE}: {revision}"]
    return "\n".join(lines) + "\n"


def scripted_reply(prompt: str) -> str:
    """The drafter used when no model is named: a reply built from the ticket text."""
    fields = {}
    for line in prompt.splitlines():
        for key in ("Customer", "Subject", REVISION_LINE):
            if line.startswith(key + ":"):
                fields[key] = line.split(":", 1)[1].strip()
    customer = fields.get("Customer") or "there"
    subject = fields.get("Subject") or "your message"
    paragraphs = [f"Hello {customer},",
                  f"Thank you for writing to us about \"{subject}\". I have opened a case for it and "
                  "will come back to you within one working day with what we found and what happens next."]
    if fields.get(REVISION_LINE):
        paragraphs.append(f"Following your note: {fields[REVISION_LINE]}")
    paragraphs.append("Kind regards")
    return "\n\n".join(paragraphs)


def draft(provider: Provider, ticket: dict, revision: str | None = None) -> dict:
    """The reply the provider drafts, as the artefact the reviewer will see."""
    text, _latency_ms, model_id = provider.complete(prompt_for(ticket, revision))
    return {"ticket_id": ticket["id"], "to": ticket["customer"], "subject": "Re: " + ticket["subject"],
            "body": text.strip(), "drafted_by": model_id}


# -- the agent ----------------------------------------------------------------

def open_task(agent: Participant, ticket: dict) -> dict:
    """task.create with the ticket as input, the ticket id as the idempotency key.

    A repeat with the same key answers with the task opened before and its
    current state, so a restart carries on where it stopped.
    """
    return agent.call("task.create", kind=TASK_KIND, input=ticket, assignee=agent.uri,
                      review_required=True, idempotency_key=f"ticket-{ticket['id']}")


def write_reply(replies_dir: Path, ticket_id: str, task_id: str, reply: dict, decision: dict) -> Path:
    replies_dir.mkdir(parents=True, exist_ok=True)
    path = replies_dir / f"{ticket_id}.json"
    record = {"ticket_id": ticket_id, "task_id": task_id, "decision": decision.get("kind"),
              "decided_by": decision.get("reviewer"), "reply": reply}
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def advance(agent: Participant, provider: Provider, item: dict, replies_dir: Path, log: Log = print) -> str | None:
    """One step for one ticket's task.

    Returns None while the task is open, and once it is settled the outcome:
    approve, override or reject, or the state the task reached another way.
    """
    ticket, task_id = item["ticket"], item["task_id"]
    view = agent.task(task_id)
    if view is None:
        raise KeyError(f"The desk process does not know task {task_id}")
    state = view["state"]
    decisions = (view.get("review") or {}).get("decisions") or []
    last = decisions[-1] if decisions else {}

    if state in ("created", "in_progress"):
        # in_progress after a review means a rejection asked for a revision;
        # the reviewer's comment goes into the next draft.
        revision = None
        if state == "in_progress" and last.get("kind") == "reject":
            revision = last.get("comment") or "no comment"
        if item["draft"] is None:
            item["draft"] = draft(provider, ticket, revision)
            log(f"{ticket['id']}: drafted{' again, with the note from the reviewer' if revision else ''} "
                f"by {item['draft']['drafted_by']}")
        if not agent.reviewer_present():
            if not item["told"]:
                log(f"{ticket['id']}: no reviewer has joined, so the review cannot open yet; "
                    "open the desk, and the draft is submitted on a later pass")
            item["told"] = True
            return None
        done = agent.call("task.complete", task_id=task_id, output=item["draft"])
        item["draft"], item["told"] = None, False
        log(f"{ticket['id']}: waiting in the desk as {task_id} ({done.get('state')})")
        return None

    if state == "review_requested":
        return None

    if state == "completed":
        path = replies_dir / f"{ticket['id']}.json"
        if path.exists():
            log(f"{ticket['id']}: decided earlier; {path} is already written")
        else:
            write_reply(replies_dir, ticket["id"], task_id, view["output"], last)
            log(f"{ticket['id']}: {last.get('kind')} by {last.get('reviewer')}, written to {path}")
        return last.get("kind") or "approve"

    if state == "declined":
        comment = f": {last['comment']}" if last.get("comment") else ""
        log(f"{ticket['id']}: rejected by {last.get('reviewer')}{comment}, nothing written")
        return "reject"

    log(f"{ticket['id']}: the task is {state}, nothing more to do")
    return state


def run(agent: Participant, provider: Provider, source: str | os.PathLike, replies_dir: Path,
        once: bool = False, poll_seconds: float = 2.0, log: Log = print) -> dict[str, str]:
    """Open a task for each new ticket in the file and advance every open task one step per pass.

    Returns each settled ticket's outcome. With ``once`` it returns when every
    ticket in the file is settled; otherwise it keeps reading the file.
    """
    pending: dict[str, dict] = {}
    seen: set[str] = set()
    outcomes: dict[str, str] = {}
    paused = False
    rows: list[dict] = []
    while True:
        try:
            rows = read_tickets(source)
            for ticket in rows:
                if ticket["id"] in seen:
                    continue
                # Under control/1.0 a paused agent is assigned nothing: its
                # task.create is refused with -32063 and the refusal recorded.
                # The first refusal is kept, so the console shows what the
                # coordinator said; after it the agent waits on
                # workspace.describe, a read, until it is resumed.
                if paused and agent.is_paused():
                    break
                try:
                    created = open_task(agent, ticket)
                except ChapError as exc:
                    if exc.code != PAUSED:
                        raise
                    log(f"{ticket['id']}: {exc.message} (refused {exc.code}); waiting to be resumed")
                    paused = True
                    break
                if paused:
                    log(f"{ticket['id']}: the agent is resumed, task.create accepted")
                    paused = False
                seen.add(ticket["id"])
                log(f"{ticket['id']}: task {created['task_id']} {created['state']}")
                pending[created["task_id"]] = {"ticket": ticket, "task_id": created["task_id"],
                                               "draft": None, "told": False}
            for task_id, item in list(pending.items()):
                outcome = advance(agent, provider, item, replies_dir, log)
                if outcome is not None:
                    outcomes[item["ticket"]["id"]] = outcome
                    del pending[task_id]
        except (ChapError, urllib.error.URLError, OSError) as exc:
            log(f"error: {exc}")
        if once and not pending and all(t["id"] in seen for t in rows):
            return outcomes
        time.sleep(poll_seconds)


def connect(config: dict, url: str) -> Participant:
    """The agent's participant, joined with its own key when signatures are required."""
    client = HttpCoordinator(url)
    try:
        client.get("/api/health")
    except (urllib.error.URLError, OSError) as exc:
        raise SystemExit(f"The desk process at {url} is not answering ({exc}). Start it with: python desk.py")
    public = client.get("/api/config") or {}
    # A join names a workspace, and the coordinator creates one it does not
    # have, so a mismatch with the desk's configuration would leave the
    # agent's tasks in a workspace the desk never shows.
    if public.get("workspace") != config["workspace"]:
        raise SystemExit(f"{url} serves {public.get('workspace')}, and chap.config.json here names {config['workspace']}")
    signer = Signer.load_or_create(config["agent"]["uri"]) if public.get("require_signatures") else None
    agent = Participant(client, config["workspace"], config["agent"]["uri"], signer=signer)
    if signer:
        agent.join("agent", "drafter", config["agent"].get("display_name"))
    return agent


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Draft a reply to each ticket and wait for the decision.")
    parser.add_argument("csv", nargs="?", default="tickets.csv", help="a CSV with id, customer, subject, body")
    parser.add_argument("--once", action="store_true", help="draft the tickets in the file, wait for their decisions, then exit")
    parser.add_argument("--url", help="the desk process, default from chap.config.json")
    parser.add_argument("--replies", default="replies", help="where decided replies are written")
    parser.add_argument("--poll", type=float, default=2.0, help="seconds between passes")
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
    run(agent, provider, args.csv, Path(args.replies), once=args.once, poll_seconds=args.poll)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
