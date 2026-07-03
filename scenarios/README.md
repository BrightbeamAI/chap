# CHAP scenarios

Runnable, domain-flavoured stories that show what CHAP records in a real
situation. Each of the twelve narratives in
[`IN_PRACTICE.md`](../IN_PRACTICE.md) has (or wants) a working
implementation here, one self-contained folder per scenario.

These are community-contributed and a good way to start using CHAP.
[Scenario 1](./01-solo-dev-overrides/) is the worked example; copy its
shape.

## How this differs from `examples/` and adapter demos

Three distinct, bounded homes, so the set never explodes into a
scenario × framework × language cross-product:

| Home | Contents | Axis |
|---|---|---|
| [`examples/*.md`](../examples/) | Curated capability walkthroughs, one per CHAP verb, written as docs. | one per **capability** |
| `packages/chap-*/examples/` | Adapter demos: the same handshake shown once per framework (LangGraph, Pydantic AI, AG2, LlamaIndex). | one per **adapter** |
| `scenarios/NN-slug/` (here) | Domain narratives on CHAP **core**: what CHAP records in situation X. One canonical implementation each. | one per **scenario** |

The rule that keeps it bounded: a scenario is implemented once, on CHAP
core, in one language (Python, matching the adapters). Framework coverage
is shown once per adapter in that adapter's own `examples/`, never re-done
per scenario. A TypeScript variant of a scenario is welcome only if
someone contributes one, in a named subfolder. So there are ~12 scenarios
and the count grows slowly, not 12 × N.

## Layout

```
scenarios/
  README.md                     <- this catalog
  01-solo-dev-overrides/
    scenario.py                 <- runnable; prints the resulting chain
    README.md                   <- recap + CHAP methods used + how to run
  NN-slug/
    ...
```

## The catalog

Status: **done** has a runnable implementation; **open** is available to
claim. "Regulated" scenarios compose several profiles and are `help
wanted`, not beginner work.

| # | Scenario | Narrative | Status | Label |
|---|---|---|---|---|
| 1 | The solo dev who can't remember what they overrode | [§1](../IN_PRACTICE.md#1-the-solo-dev-who-cant-remember-what-they-overrode) | **done** ([code](./01-solo-dev-overrides/)) | good first issue |
| 2 | Marketing copy with one drafter and one editor | [§2](../IN_PRACTICE.md#2-marketing-copy-with-one-drafter-and-one-editor) | open | good first issue |
| 3 | The indie founder, the inbox, and the angry customer | [§3](../IN_PRACTICE.md#3-the-indie-founder-the-inbox-and-the-angry-customer) | open | good first issue |
| 4 | Support ops at 03:00 | [§4](../IN_PRACTICE.md#4-support-ops-at-0300) | open | |
| 5 | Sixty engineers and a code-review bot they don't trust the same way | [§5](../IN_PRACTICE.md#5-sixty-engineers-and-a-code-review-bot-they-dont-trust-the-same-way) | open | |
| 6 | A junior, a partner, and a contract that ships tonight | [§6](../IN_PRACTICE.md#6-a-junior-a-partner-and-a-contract-that-ships-tonight) | open | |
| 7 | Trust and Safety, three auditors, one queue | [§7](../IN_PRACTICE.md#7-trust-and-safety-three-auditors-one-queue) | open | |
| 8 | The creative shop and the client who "never approved that" | [§8](../IN_PRACTICE.md#8-the-creative-shop-and-the-client-who-never-approved-that) | open | |
| 9 | The internal Q&A bot that keeps getting the same thing wrong | [§9](../IN_PRACTICE.md#9-the-internal-qa-bot-that-keeps-getting-the-same-thing-wrong) | open | |
| 10 | A pressure drop on the fill-finish line | [§10](../IN_PRACTICE.md#10-a-pressure-drop-on-the-fill-finish-line) | open | help wanted, regulated |
| 11 | Motor claims and the bereavement that changes everything | [§11](../IN_PRACTICE.md#11-motor-claims-and-the-bereavement-that-changes-everything) | open | help wanted, regulated |
| 12 | Five years on, the regulator asks | [§12](../IN_PRACTICE.md#12-five-years-on-the-regulator-asks) | open | help wanted, regulated |

## Contributing a scenario

1. **Claim it.** Comment on the scenario's tracking issue so there's one
   owner. If no issue is open yet, open one.
2. **Copy the shape.** Start from
   [`01-solo-dev-overrides/`](./01-solo-dev-overrides/): a `scenario.py`
   that runs against `coordinator-py` and prints its result, plus a
   `README.md` that recaps the story, lists the CHAP methods used, and says
   how to run it.
3. **Keep it on core.** Implement the domain narrative on CHAP core (plus
   whatever profiles the story genuinely needs). A framework-specific
   version of the same story belongs in that adapter's own `examples/`,
   not as a separate scenario here.
4. **Add your row** to the catalog above.

### Definition of done

Every scenario:

- [ ] Runs end to end against `coordinator-py` (or names the adapter it
      needs).
- [ ] Produces a valid chain: the same envelopes the
      [conformance harness](../conformance/) validates against the
      reference coordinators.
- [ ] Reproduces the scenario's punchline (the report, the reconstructed
      chain, the specific thing the story is about).
- [ ] Has a `README.md` mapping the story's beats to CHAP methods.
- [ ] Respects authorisation: actors join before they act, and review
      decisions come from an addressed reviewer (see
      [`profiles/review.md`](../profiles/review.md) §3.2).

### A note on the regulated scenarios

The gentle ones (1, 2, 3) are genuine good first issues. The regulated
ones (10, 11, 12) compose several profiles: identity, signing, SCITT
anchoring, deliberation, handoff. They are `help wanted`, not beginner
work, and a first pass may stage the profiles (a core version first, the
fuller set as a stretch). Say which profiles are in versus stretch in the
scenario's README.
