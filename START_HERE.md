# Start here

One command, one real decision, about two minutes.

```sh
git clone https://github.com/BrightbeamAI/chap.git
cd chap
python3 start-here/start.py
```

Python 3.10 or newer, and nothing else. No install, no npm, no Docker, no model,
no API key. The coordinator runs from the checkout.

A browser window opens on a local review desk. An agent has drafted a reply to
a customer, promising a delivery date the tracking data does not support. Fix
the promise, give a reason, and approve your version. You will see the draft,
your patch, the reason, and the hash-linked events that record all three.

Then use **Start another review** on the right for a second draft, and reject
that one. The rejected task blocks the next step. Start a third and leave it
alone: it stays pending, and pending is not consent. Stop the process and start
it again; the decisions are still there.

If port 7331 is taken, add `--port 0` and the OS will pick one. If no browser
opens, the terminal prints the link.

## Then put your own output through it

Save this as `start-here/my_first_review.py`, next to `chap_starter.py`, and
run it with `python3 start-here/my_first_review.py`.

```python
from chap_starter import ReviewGate, ReviewPending, ReviewRejected, ask_in_terminal

with ReviewGate() as chap:
    task = chap.propose({"text": "whatever your agent produced"})
    ask_in_terminal(chap, task)
    try:
        print("Approved:", chap.result(task))
    except (ReviewPending, ReviewRejected) as stop:
        print("Not authorised to continue:", stop)
```

`result()` returns the exact reviewed object. It raises `ReviewPending` or
`ReviewRejected` when the next step must not run, and it never falls back to
the draft, which is why the two exceptions are caught rather than left to
crash. That is the whole integration surface: keep your model, your framework
and your tool code, and put this at the review boundary.

[`start-here/README.md`](./start-here/README.md) covers the rest: the two ways
to open a review, what the desk is and is not, and how to render an artefact so
a reviewer sees what they are signing.

## Where to go after that

| You want | Go to |
| --- | --- |
| To see the envelopes themselves | [`examples/00-five-minute-start.md`](./examples/00-five-minute-start.md) |
| CHAP as MCP tools in Claude Desktop or Cursor | [`examples/drive-chap-from-claude-desktop.md`](./examples/drive-chap-from-claude-desktop.md) |
| A bridge for the framework you already use | [`IMPLEMENTATIONS.md`](./IMPLEMENTATIONS.md), for LangGraph, Pydantic AI, LlamaIndex, AG2 and Google ADK |
| To drive CHAP from an agent-to-agent orchestrator | [`examples/drive-chap-from-an-a2a-orchestrator.md`](./examples/drive-chap-from-an-a2a-orchestrator.md) |
| Worked stories in a domain like yours | [`IN_PRACTICE.md`](./IN_PRACTICE.md) and [`scenarios/`](./scenarios/) |
| The methods, states and error codes | [`SPECIFICATION.md`](./SPECIFICATION.md) |
| What the optional profiles add | [`profiles/`](./profiles/) |
| To build a coordinator and prove it correct | [`conformance/`](./conformance/) |
| The reasoning behind the design | [the paper](https://arxiv.org/abs/2606.09751) and [`ARCHITECTURE.md`](./ARCHITECTURE.md) |

Questions and first-run problems: [open a
discussion](https://github.com/BrightbeamAI/chap/discussions).
