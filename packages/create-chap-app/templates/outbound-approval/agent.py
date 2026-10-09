"""The agent: drafts each outbound message, then waits for the approver.

Run it beside desk.py:

    python agent.py messages.csv           draft each new row as it appears
    python agent.py messages.csv --once    draft the rows in the file, wait for
                                           their decisions, then exit

Each row becomes one task. The agent does not ask for review: the workspace
runs in trial mode under modes/1.0, so the coordinator requires it anyway,
and the draft waits in the desk. The message as decided, including an
override's patched result, is written to outbox/<message id>.json. A
rejected draft is written nowhere. A rejection that asks for a revision has
the agent draft again with the approver's note and submit the new draft.

A message's id is a hash of its row, and the task's idempotency key, so a
restarted agent finds the tasks it opened before and opens no duplicates,
and a row whose text changed is a new message. Before it submits a draft
the agent checks on workspace.describe, a read, that an approver has joined:
a completion with nobody to address the review to is refused and the refusal
recorded, so the agent waits instead and says so once.

Under control/1.0 the approver can pause the agent with pause.py. While it
is paused, task.create is refused with -32063; the agent says so and waits
until resume.py runs.

The model comes from the environment (see providers.py). With nothing set,
a scripted drafter writes the body, and the console says so.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
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
REVISION_LINE = "The approver asked for a revision"
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

def message_id(row: dict) -> str:
    """The id of a message: a hash of its row, so the same row is the same message on every run."""
    digest = hashlib.sha256(json.dumps([row.get(c, "") for c in COLUMNS]).encode("utf-8")).hexdigest()
    return f"m-{digest[:12]}"


def read_messages(path: str | os.PathLike) -> list[dict]:
    """The rows of a CSV with the columns to, subject and brief.

    Point this at your own export: keep those three column names, and every
    other column is ignored.
    """
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        missing = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise SystemExit(f"{path} needs the columns {', '.join(COLUMNS)}; missing: {', '.join(missing)}")
    messages = []
    for row in rows:
        fields = {c: (row.get(c) or "").strip() for c in COLUMNS}
        if fields["to"]:
            messages.append({"message_id": message_id(fields), **fields})
    return messages


# -- drafting -----------------------------------------------------------------

def prompt_for(message: dict, revision: str | None = None) -> str:
    lines = [
        "Write the body of a short, courteous business email from the brief below. Plain text, "
        "no subject line, no placeholders.",
        "",
        f"To: {message['to']}",
        f"Subject: {message['subject']}",
        f"Brief: {message['brief']}",
    ]
    if revision:
        lines += ["", f"{REVISION_LINE}: {revision}"]
    return "\n".join(lines) + "\n"


def scripted_body(prompt: str) -> str:
    """The drafter used when no model is named: a body built from the brief."""
    fields = {}
    for line in prompt.splitlines():
        for key in ("To", "Subject", "Brief", REVISION_LINE):
            if line.startswith(key + ":"):
                fields[key] = line.split(":", 1)[1].strip()
    name = fields.get("To", "").split("@")[0].split(".")[0].capitalize() or "there"
    brief = fields.get("Brief") or fields.get("Subject") or "the matter below"
    paragraphs = [f"Hello {name},", brief,
                  "If anything here is unclear, reply to this message and I will sort it out."]
    if fields.get(REVISION_LINE):
        paragraphs.append(f"Following your note: {fields[REVISION_LINE]}")
    paragraphs.append("Best regards")
    return "\n\n".join(paragraphs)


def draft(provider: Provider, message: dict, revision: str | None = None) -> dict:
    """The message the provider drafts, as the artefact the approver will see."""
    text, _latency_ms, model_id = provider.complete(prompt_for(message, revision))
    return {"message_id": message["message_id"], "to": message["to"], "subject": message["subject"],
            "body": text.strip(), "drafted_by": model_id}


# -- the agent ----------------------------------------------------------------

def open_task(agent: Participant, message: dict) -> dict:
    """task.create with the brief as input, the message id as the idempotency key, and no review_required.

    The workspace runs in trial mode, and under modes/1.0 a trial task
    requires review whatever the agent passes. A repeat with the same key
    answers with the task opened before and its current state, so a restart
    carries on where it stopped. While the agent is paused the call is
    refused with -32063, raised here as ChapError.
    """
    return agent.call("task.create", kind=TASK_KIND, input=message, assignee=agent.uri,
                      idempotency_key=message["message_id"])


def write_message(outbox: Path, message_id: str, task_id: str, message: dict, decision: dict) -> Path:
    outbox.mkdir(parents=True, exist_ok=True)
    path = outbox / f"{message_id}.json"
    record = {"message_id": message_id, "task_id": task_id, "decision": decision.get("kind"),
              "decided_by": decision.get("reviewer"), "message": message}
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def advance(agent: Participant, provider: Provider, item: dict, outbox: Path, log: Log = print) -> str | None:
    """One step for one message's task.

    Returns None while the task is open, and once it is settled the outcome:
    approve, override or reject, or the state the task reached another way.
    """
    message, task_id = item["message"], item["task_id"]
    mid = message["message_id"]
    view = agent.task(task_id)
    if view is None:
        raise KeyError(f"The desk process does not know task {task_id}")
    state = view["state"]
    decisions = (view.get("review") or {}).get("decisions") or []
    last = decisions[-1] if decisions else {}

    if state in ("created", "in_progress"):
        # in_progress after a review means a rejection asked for a revision;
        # the approver's comment goes into the next draft.
        revision = None
        if state == "in_progress" and last.get("kind") == "reject":
            revision = last.get("comment") or "no comment"
        if item["draft"] is None:
            item["draft"] = draft(provider, message, revision)
            log(f"{mid}: drafted{' again, with the note from the approver' if revision else ''} "
                f"by {item['draft']['drafted_by']}")
        if not agent.reviewer_present():
            if not item["told"]:
                log(f"{mid}: no approver has joined, so the review cannot open yet; "
                    "open the desk, and the draft is submitted on a later pass")
            item["told"] = True
            return None
        done = agent.call("task.complete", task_id=task_id, output=item["draft"])
        item["draft"], item["told"] = None, False
        if done.get("state") == "review_requested":
            log(f"{mid}: waiting in the desk as {task_id} (review opened without review_required: "
                "the workspace is in trial mode under modes/1.0)")
        else:
            log(f"{mid}: task {task_id} is {done.get('state')} with nobody asked")
        return None

    if state == "review_requested":
        return None

    if state == "completed":
        path = outbox / f"{mid}.json"
        if path.exists():
            log(f"{mid}: decided earlier; {path} is already written")
        else:
            write_message(outbox, mid, task_id, view["output"], last)
            log(f"{mid}: {last.get('kind')} by {last.get('reviewer')}, written to {path}")
        return last.get("kind") or "approve"

    if state == "declined":
        comment = f": {last['comment']}" if last.get("comment") else ""
        log(f"{mid}: rejected by {last.get('reviewer')}{comment}, nothing written")
        return "reject"

    log(f"{mid}: the task is {state}, nothing more to do")
    return state


def run(agent: Participant, provider: Provider, source: str | os.PathLike, outbox: Path,
        once: bool = False, poll_seconds: float = 2.0, log: Log = print) -> dict[str, str]:
    """Open a task for each new row in the file and advance every open task one step per pass.

    Returns each settled message's outcome. With ``once`` it returns when
    every row in the file is settled; otherwise it keeps reading the file.
    """
    pending: dict[str, dict] = {}
    seen: set[str] = set()
    outcomes: dict[str, str] = {}
    paused = False
    rows: list[dict] = []
    while True:
        try:
            rows = read_messages(source)
            for message in rows:
                mid = message["message_id"]
                if mid in seen:
                    continue
                # A task.create while the agent is paused is refused with
                # -32063 and the refusal recorded. The first refusal is kept,
                # so the console shows what the coordinator said; after it
                # the agent waits on workspace.describe, a read, until
                # resume.py has run.
                if paused and agent.is_paused():
                    break
                try:
                    created = open_task(agent, message)
                except ChapError as exc:
                    if exc.code != PAUSED:
                        raise
                    log(f"{mid}: {exc.message} (refused {exc.code}); waiting for resume.py")
                    paused = True
                    break
                if paused:
                    log(f"{mid}: the agent is resumed, task.create accepted")
                    paused = False
                seen.add(mid)
                log(f"{mid}: task {created['task_id']} {created['state']}")
                pending[created["task_id"]] = {"message": message, "task_id": created["task_id"],
                                               "draft": None, "told": False}
            for task_id, item in list(pending.items()):
                outcome = advance(agent, provider, item, outbox, log)
                if outcome is not None:
                    outcomes[item["message"]["message_id"]] = outcome
                    del pending[task_id]
        except (ChapError, urllib.error.URLError, OSError) as exc:
            log(f"error: {exc}")
        if once and not pending and all(m["message_id"] in seen for m in rows):
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


def approver(config: dict, url: str | None = None) -> Participant:
    """The first configured human, for pause.py and resume.py."""
    return Participant(HttpCoordinator(url or desk_url(config)), config["workspace"], config["humans"][0]["uri"])


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Draft each outbound message and wait for the approver.")
    parser.add_argument("csv", nargs="?", default="messages.csv", help="a CSV with to, subject, brief")
    parser.add_argument("--once", action="store_true", help="draft the rows in the file, wait for their decisions, then exit")
    parser.add_argument("--url", help="the desk process, default from chap.config.json")
    parser.add_argument("--outbox", default="outbox", help="where approved messages are written")
    parser.add_argument("--poll", type=float, default=2.0, help="seconds between passes")
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
    run(agent, provider, args.csv, Path(args.outbox), once=args.once, poll_seconds=args.poll)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
