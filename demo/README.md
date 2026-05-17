# The HAP Demo

A single-page interactive demo that tells HAP's story end-to-end.
Self-contained: just `index.html`. No build, no server, no
dependencies. Open it in any modern browser.

## What's in it

The demo walks through six sections:

1. **The story.** Maya's afternoon at TechCorp Support — 47 customer
   responses drafted by an AI, waiting for review. The framing
   question: *what if every one of those edits became data?*

2. **The analogy.** HAP is track changes for AI. The same shape an
   editor uses when marking up a writer's draft, structured for
   machines as well as for humans.

3. **The protocol stack.** Three protocols, one stack. MCP for tools,
   A2A for agents, HAP for humans-with-agents. Plus the seven Core
   methods.

4. **A workspace at work.** An interactive walkthrough — click through
   six envelopes and watch the audit log build up, watch participants
   highlight as they exchange messages.

5. **The killer move.** The four fields of an override artefact: diff,
   rationale, tags, policy refs. Visually laid out so the structure is
   obvious at a glance.

6. **The dividend.** A week of overrides aggregated by tag, with the
   interpretation made concrete: *tone-softened* dominates, here's the
   one-line prompt change.

7. **Adoption.** Profile picker, pointer to Core spec, Handbook,
   reference implementation.

## How to view

Open `index.html` in any browser:

```bash
# macOS
open demo/index.html

# Linux
xdg-open demo/index.html

# Windows
start demo/index.html
```

Or just double-click the file. No server needed.

## What it's for

This demo is the answer to *"what does HAP actually do, in five
minutes, without reading a spec?"*

Use it for:

- **Sharing with non-technical stakeholders** who need to understand
  why your team is adopting HAP.
- **Onboarding engineers** before they read [`core/SPEC.md`](../core/SPEC.md).
- **Conference talks** — the sections can be walked through live as a
  visual story.
- **As a portable artefact** — single HTML file, opens anywhere,
  works offline, easy to attach to an email or paste in a slide.

For a working code demonstration of the same flow, see
[`../reference/core-plus-review/`](../reference/core-plus-review/) —
which produces the actual audit log entries the demo visualises.

## Browser compatibility

Tested in current Chrome, Safari, Firefox, and Edge. Uses no
JavaScript features newer than ES2020. Uses no external resources
(no CDN, no Google Fonts, no analytics) — it works fully offline.
