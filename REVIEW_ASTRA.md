# Review task — Crew Ops Advisor (dCortex / vista-crew)

You are reviewing a finished hackathon build end to end: the engine, the agent
loop, the grounding gate, the tool surface, the tests and the browser UI. The
build is on branch `rebuild`. Read the code, not only the docs — the docs are
part of what you are checking.

## What the product is

An airline crew-control desk assistant. A controller loses a captain two hours
before report time and asks, in plain English, what to do. The answer has to be
one a duty manager could act on without checking it: a named crew member, the
rule that cleared them, the cost, and what it rules out.

The design commitment the whole build rests on:

> **Deterministic Python computes every figure, verdict and cost. The language
> model chooses which question to ask, resolves what the controller meant, and
> explains the result. It never calculates.**

Everything below exists to make that claim true rather than aspirational.

## Where things are

| Path | What it is |
| --- | --- |
| `aircrew/data.py` | Loads the nine published JSON files; no logic beyond indexing |
| `aircrew/rules.py` | The seven rostering rules, one function each |
| `aircrew/engine.py` | Legality, ranking, cost, disruption, joint plans |
| `aircrew/query.py` | Read-only lookups over the dataset |
| `aircrew/tools.py` | The model-facing tool surface; every result is a claim envelope |
| `aircrew/grounding.py` | The claim gate: substitution and five checks on the final reply |
| `aircrew/agent.py` | The loop, the system prompt, the one corrective round |
| `aircrew/server.py` | Stdlib HTTP server: `/api/tool`, `/api/chat`, `/api/reset` |
| `web/index.html` | The whole UI, one file, no build step |
| `tests/test_agent_loop.py` | 21 tests over the loop and the gate |
| `tests/ui_check.js` | 68 DOM checks over `web/index.html` under jsdom |
| `problem_statement/data/` | The published dataset and the 38 questions |
| `docs/` | ARCHITECTURE, TOOLS, TOOL_DESIGN, FLOW, THE_38_QUESTIONS |

## The three ideas worth attacking

**1. The claim envelope.** Every tool returns `{summary, claims, missing, data}`.
`claims` are the figures and verdicts that result establishes, each with an id.
The model may only state a figure by citing one — `{{claim:c7}}` — and
`grounding.py` substitutes the engine's own text for the placeholder. `missing`
names what the result does *not* establish, so an impact result cannot be read
as a recommendation.

**2. Substitution versus checking.** Checking a number the model typed catches
an invented figure. It does not catch a *real* figure attached to the wrong
thing — "assign C-3310 at INR 24,000" where 24,000 was C-2210's cost passes
every arithmetic check and is still wrong. Only substitution prevents that.
Claims carry both a full `text` ("3 flights uncovered on day 1: DX412, DX413,
DX588") and a bare `short` ("DX412, DX413, DX588"); the gate picks between them
from what surrounds the placeholder.

**3. `blocking` versus `ok`.** Five checks run on the final reply: leftover
placeholders, ungrounded figures, figures under the wrong label, unknown or
malformed claim ids, and figures spelled as words ("thousands"). Only untruths
suppress an answer. Style faults — naming a tool in the prose — trigger one
rewrite but never withhold a true answer, because withholding a correct
recovery plan over wording is the worse failure.

## What to review

Go where you think the risk is. These are the questions we could not settle
ourselves:

1. **Is the gate sound, or does it only look sound?** `grounding.py` is
   regex-driven. Find a true-looking reply that carries a wrong figure past it.
   Find a correct reply it wrongly suppresses. Both are real failures; the
   second one has bitten us twice already.
2. **Is the `text`/`short` choice safe?** `_pick()` decides from surrounding
   punctuation and capitalisation. Where does it read badly, and can it ever
   substitute a figure the engine did not produce?
3. **Is the tool surface the right shape?** Tools query several JSON files at
   once and return more than a single fact — deliberately, so the model asks one
   question instead of five and cannot join the files itself. `docs/TOOL_DESIGN.md`
   argues this. Argue back if the context cost does not pay for itself.
4. **Does the UI say the true thing?** `web/index.html` draws the ranked
   options, the timeline, the exclusions and the boundary diagram from tool
   results only. Look for anywhere the screen implies more certainty than the
   engine computed, or buries the one thing a controller needs.
5. **The 38 questions.** `problem_statement/data/questions.json` is the
   published set with expected answers. Screenshots of all 38 run through the
   UI are in `screenshots/`. Tell us which answers a crew controller would
   reject, and why.
6. **What is missing entirely.** Not features — a missing check, an untested
   failure mode, a claim in the docs the code does not support.

## Ground rules

- Formatting, naming and file layout are settled; do not spend the review on them.
- Assume the dataset is fixed and correct. Findings about the data belong to the
  organisers, not to us.
- Prefer one demonstrated failure — inputs, the reply, why it is wrong — over
  five suspicions. If you can write the test, write it.
- Say plainly where the design is right. We need to know what not to touch as
  much as what to fix.

## Output

Ordered by severity, most serious first. For each: what breaks, how to
reproduce it, and the smallest change that fixes it. End with the three things
you would do next if this were your build and you had one day.
