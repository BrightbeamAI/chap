# Scenario 1 as a working system

This is scenario 1 wired into a **real agent framework** ([Pydantic
AI](https://ai.pydantic.dev)), to show CHAP mediating a genuine
human-in-the-loop approval flow rather than a staged one. It is the
"completed system example" companion to the zero-dependency
[`../scenario.py`](../scenario.py).

## Run it

```bash
pip install "pydantic-ai-slim>=1.0"
python3 system.py
```

One dependency, no API key, no network. It prints the decisions CHAP
recorded and the override learning report, exactly like the core scenario,
but this time every decision was produced by driving a real agent loop
through its approval gate.

## What is real, and what is stubbed

This is the important part, and the boundary is deliberate.

**Real:**

- **The agent framework loop.** A real Pydantic AI `Agent` with a tool
  marked `requires_approval=True`. The framework genuinely runs the agent,
  suspends at the tool call, and resumes once the decision is supplied.
  This is Pydantic AI's own deferred-tool mechanism, used unchanged, the
  same way you would use it in production.
- **CHAP.** Each approval, edit, or denial is translated by the
  [`chap-pydantic-ai`](../../../packages/chap-pydantic-ai/) bridge into a
  real `decide.approve` / `decide.override` / `decide.reject` on a
  hash-linked audit chain in the reference coordinator. Nothing about the
  recording is faked.

**Stubbed, so the demo is reproducible and runs offline:**

- **The model.** A deterministic `FunctionModel` plays the review bot and
  proposes realistic findings. It is the one thing standing in for
  intelligence, and replacing it is a one-line change (see below).
- **The developer's decisions.** A person would decide at the approval
  prompt; here a fixed `DECISIONS` table decides, keyed by PR, so every run
  is identical and CI can check it.

The claim this makes, then, is honest and specific: **CHAP slots into a
real agent framework's native approval path.** The only difference between
this and a live system is the model behind the agent and the human behind
the decision, and neither of those touches the CHAP integration.

## From this to a live system

The header of `system.py` shows the change. In short:

```python
# offline (this file):
agent = Agent(FunctionModel(review_bot), output_type=[str, DeferredToolRequests])

# live:
agent = Agent("anthropic:claude-sonnet-4-5", output_type=[str, DeferredToolRequests])
```

Point the agent at a real model, drive the approval prompt from real
developer input instead of the `DECISIONS` script, and the bridge and audit
chain are unchanged. That is the whole point of keeping the boundary clean:
the same integration carries you from an offline demo to production.

## Contributing your own system implementation

This is the shape to copy for a "completed system example":

1. A real agent framework driving the workflow (any of the four adapters:
   [`chap-langgraph`](../../../packages/chap-langgraph/),
   [`chap-pydantic-ai`](../../../packages/chap-pydantic-ai/),
   [`chap-ag2`](../../../packages/chap-ag2/),
   [`chap-llama-index`](../../../packages/chap-llama-index/)).
2. CHAP mediating the human-in-the-loop decision, recorded on the chain.
3. An offline, reproducible default (a stub model and scripted decisions)
   so it runs in CI and for a first-time reader, with a documented one-line
   path to a live model.
4. A README that states plainly what is real and what is stubbed.

If you build a system implementation for another scenario, or another
framework's take on this one, add it under that scenario's folder and note
it in [`../../README.md`](../../README.md).
