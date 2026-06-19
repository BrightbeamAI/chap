"""
CHAP reference client demo (Python).

Runs the same scenario as reference/core-plus-review/client.ts:
two participants in a workspace, an agent's draft, a human override,
and a final audit read.

Usage:

    python server.py &       # in one terminal
    python client.py         # in another
"""
from __future__ import annotations

import argparse
import json
import sys
from urllib.request import Request, urlopen


def call(url: str, method: str, **params) -> dict:
    env = {
        "jsonrpc": "2.0",
        "id": f"client-{method}",
        "method": method,
        "params": params,
    }
    req = Request(url, data=json.dumps(env).encode("utf-8"),
                  headers={"Content-Type": "application/json"})
    with urlopen(req) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body


def emoji(ok: bool) -> str:
    return "ok " if ok else "ERR"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="CHAP demo client (Python)")
    p.add_argument("--url", default="http://127.0.0.1:8080/chap")
    p.add_argument("--workspace", default="wsp_support_triage")
    args = p.parse_args(argv)

    ws = args.workspace
    print(f"CHAP Core+Review demo against {args.url}")
    print("=" * 50)

    # 1. Create workspace
    r = call(args.url, "workspace.create", workspace=ws,
             profiles=["core/1.0", "review/1.0"])
    print(f"-> workspace.create   {emoji('result' in r)}  {ws}")

    # 2. Two participants join
    r = call(args.url, "participant.join", workspace=ws,
             **{"from": "human:alice@example.org", "type": "human", "role": "reviewer"})
    print(f"-> participant.join   {emoji('result' in r)}  human:alice@example.org")

    r = call(args.url, "participant.join", workspace=ws,
             **{"from": "agent:triage-bot", "type": "agent", "role": "drafter"})
    print(f"-> participant.join   {emoji('result' in r)}  agent:triage-bot")

    # 3. Create a task
    r = call(args.url, "task.create", workspace=ws,
             **{"from": "human:alice@example.org",
                "kind": "draft_response",
                "input": {"ticket_id": "INC-48219",
                          "query": "customer wants refund"},
                "assignee": "agent:triage-bot"})
    task_id = r["result"]["task_id"]
    print(f"-> task.create        {emoji(True)}  task_id={task_id}")

    # 4. Bot moves to in_progress
    call(args.url, "task.update", workspace=ws,
         task_id=task_id, state="in_progress",
         **{"from": "agent:triage-bot"})

    # 5. Bot drafts and requests review
    draft = {
        "severity": "warning",
        "text": "Refund processed under policy v3.",
    }
    r = call(args.url, "review.request", workspace=ws, task_id=task_id,
             **{"from": "agent:triage-bot",
                "to": "human:alice@example.org",
                "artefact": draft})
    print(f"-> review.request     {emoji('result' in r)}  draft with severity={draft['severity']!r}")

    # 6. Human overrides with diff + rationale + tags
    r = call(args.url, "decide.override", workspace=ws, task_id=task_id,
             **{"from": "human:alice@example.org"},
             diff=[{"op": "replace", "path": "/severity", "value": "info"}],
             rationale="False positive. Framework convention, not a bug.",
             tags=["false-positive", "framework-pattern-misread"],
             intent_preserved=True)
    print(f"-> decide.override    {emoji('result' in r)}  applied -> severity={r['result']['applied']['severity']!r}")

    # 7. Read the audit log
    r = call(args.url, "audit.read", workspace=ws)
    entries = r["result"]["entries"]
    print(f"-> audit.read         {emoji(True)}  {len(entries)} entries:")
    for e in entries:
        env = e["envelope"]
        print(f"     seq={e['seq']:2d}  {env['method']:25s}  from={env['params'].get('from','?')}")

    print()
    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
