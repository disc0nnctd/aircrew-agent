# Crew Ops Advisor

A decision-support tool for an airline crew-control desk. A controller describes
a disruption in their own words; the system says who can legally cover it, what
each option costs, and why everyone else was ruled out.

**The architectural claim:** deterministic Python computes every figure. The
language model chooses which question to ask and explains the result. It may
propose anything, but it may only *state* what a validator returned — and that
is enforced by a gate, not by a line in a prompt. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

![Ranked cover for P-2291](screenshots/04-ranked-cover.png)

A captain is lost two hours before report. The recommendation, its cost, and
the seven rules that cleared it — every figure on that screen was computed by
Python, and the model could not have written one that was not.

![Why the others were ruled out](screenshots/05-exclusions.png)

All nineteen candidates that were ruled out, each carrying the rule that
stopped them. C-3305 is marked *not callable* rather than illegal: being
outside an on-call window breaks no rule, and a controller who confuses the two
makes the wrong call.

More in [screenshots/](screenshots/) — the boundary diagram, the duty-week
timeline with its rest gap, the joint plan for two simultaneous sick calls, and
the drill-down that answers "why not them?"

## Where it stands

| Measurement | Result | Reproduce |
| --- | --- | --- |
| The 38 questions, through the engine | **36 / 36 gradable pass** (2 are rubrics, not values, and are never counted as passes) | `python3 -m aircrew.scoreboard` |
| The 6 worked scenarios | **19 / 19 checks pass**, including S2's 19 exclusions byte for byte | same command |
| Agent loop and claim gate | **30 / 30 tests pass** | `python3 -m tests.test_agent_loop` |
| The workspace, in a headless browser | **127 / 127 DOM checks pass** | `npm i && node tests/ui_check.js` |
| Two outside reviews, their own regressions | **9 / 13 pass**; the other 4 are [findings we rejected](docs/REVIEW_DISPOSITION.md) because they contradict the published answer keys | `python3 -m tests.test_review_astra` |
| The 38 questions, *through the agent* (gpt-5.6-luna) | **35 / 36 gradable** on the last recorded run, re-scored offline against the calls the model actually made; the one genuine failure is a routing miss on Q27. Three fixes have landed since and are not live-verified — run 3 has not been run. [docs/ISSUES.md](docs/ISSUES.md) has the run table. | `python3 -m aircrew.replay` (needs a key) |

One caveat worth stating plainly: the engine score checks whether each expected
value appears in the tool results, which is retrieval and routing coverage. It
is not a judgement that the sentence the controller reads is the right one.

```bash
python3 -m aircrew.scoreboard         # the engine number, no model required
python3 -m tests.test_agent_loop      # the gate and the loop
npm i && node tests/ui_check.js       # the workspace and chat pane, under jsdom
node tests/review_astra_ui.js         # the outside review's UI regressions
```

Without jsdom the DOM check prints `SKIP` and runs nothing; a skip is not a
pass. `review_astra_ui.js` prints three `KNOWN FAILURE` lines by design — they
are the three UI findings still open, listed in
[docs/REVIEW_DISPOSITION.md](docs/REVIEW_DISPOSITION.md).

## Setup

Python 3.10+ and nothing else. The engine, the CLI, the workspace and the chat
loop are standard library only — there is no `requirements.txt`, because there
is nothing to put in one. The single third-party package in the repository is
`jsdom`, a devDependency of the DOM test; nothing that ships depends on it.

```bash
git clone https://github.com/disc0nnctd/aircrew-agent && cd aircrew-agent
python3 -m aircrew.scoreboard         # should print 36/36 and 19/19
python3 -m aircrew.server             # http://127.0.0.1:8765
```

For the chat pane, an OpenAI-compatible endpoint. Still no dependencies: the
loop posts to `/chat/completions` over the standard library.

```bash
export AIRCREW_API_KEY=...
export AIRCREW_MODEL=gpt-5.6-luna     # default
export AIRCREW_BASE_URL=...           # default https://api.openai.com/v1
python3 -m aircrew.server
```

Without a key the server says so and the workspace runs on the engine alone —
every recovery the product can recommend is still reachable, from the buttons in
the UI or from the CLI.

```bash
python3 -m aircrew.cli resolve  --pairing P-2291 --vacated-by C-1042
python3 -m aircrew.cli resolve  --pairing P-2291 --vacated-by C-1042 --exclude C-3310
python3 -m aircrew.cli check    --crew C-5837 --pairing P-2291
python3 -m aircrew.cli delay    --aircraft VT-DXA --date 2026-09-16 --hours 1.5 --mode technical
python3 -m aircrew.cli closure  --station BLR --date 2026-09-17 --start 08:00 --end 14:00
python3 -m aircrew.cli timeline --crew C-3310 --pairing P-2291
```

## Live

**https://vista-crew.disc0nnctd1.workers.dev**

The same engine, the same tools, the same page — on Cloudflare Workers, with no
filesystem and no key of its own. Every panel is computed at the edge by the
Python in `aircrew/`.

The chat runs there too: open **settings** in the header and add a provider
(Sarvam, Gemini, Cloudflare Workers AI, NVIDIA NIM, or any OpenAI-compatible
endpoint). The key stays in your browser and rides with each question — a Worker
isolate does not outlive a request, so nothing is stored at the edge.

To deploy your own:

```bash
python3 worker/build.py
cd worker && npx wrangler deploy      # needs Node 22
```

[worker/README.md](worker/README.md) has the detail.

## Documentation

| Document | What it is for |
| --- | --- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | The boundary between the model and the deterministic engine, and the claim gate that enforces it |
| [docs/TOOLS.md](docs/TOOLS.md) | The ten tools, the claim envelope, and which data files each one measurably reads |
| [docs/TOOL_DESIGN.md](docs/TOOL_DESIGN.md) | Why the surface is ten joined tools rather than seventeen thin ones, priced in tokens |
| [docs/FLOW.md](docs/FLOW.md) | One question end to end, and the gate attacked with fifteen deliberately adversarial mislabels |
| [docs/THE_38_QUESTIONS.md](docs/THE_38_QUESTIONS.md) | All 38 graded questions, with the call that actually answers each one |
| [docs/DESCRIPTION.md](docs/DESCRIPTION.md) | The product in prose, and how the three question tiers are covered |
| [docs/SAMPLES.md](docs/SAMPLES.md) | Worked transcripts, including [the case it handles poorly](docs/SAMPLES.md#e-a-case-the-system-handles-poorly) |
| [docs/NOTES.md](docs/NOTES.md) | How each rule was recovered from the answer keys, which rules actually bind, and the dead ends |
| [docs/ISSUES.md](docs/ISSUES.md) | Open issues, the limits of the gate, and every bug found with its evidence |
| [docs/REVIEW_DISPOSITION.md](docs/REVIEW_DISPOSITION.md) | What we did with two outside reviews — including the four findings we rejected, and why |
| [docs/REVIEW_ASTRA_FINDINGS.md](docs/REVIEW_ASTRA_FINDINGS.md) | One of those reviews, unedited, with its thirteen regression tests in `tests/test_review_astra.py` |
| [docs/DECK.md](docs/DECK.md) | Ten slides written to be read; the presented deck is [docs/crew_ops_advisor_project_deck.pptx](docs/crew_ops_advisor_project_deck.pptx) |
| [worker/README.md](worker/README.md) | The Cloudflare Workers deployment behind the live URL above |
| [problem_statement/](problem_statement/) | The organisers' brief and dataset, reproduced verbatim. Their words and their internal notes, not ours |

Index also at [docs/README.md](docs/README.md).

## Layout

```
aircrew/
  data.py        loaders, indices, the Duty record
  rules.py       the seven rules; Finding carries numbers, render() carries words
  engine.py      impact, resolver, costing, ranking, joint search, recovery
  query.py       the typed Tier-1 lookups
  tools.py       10 tools; the claim envelope
  grounding.py   the claim gate
  agent.py       the loop, for an OpenAI-compatible model
  scoreboard.py  replays the 38 questions and 6 scenarios against the keys
  replay.py      replays the 38 questions THROUGH THE AGENT
  cli.py         the engine from a terminal, no model
  server.py      stdlib HTTP; /api/tool and /api/chat
web/index.html   the two-pane workspace, no build step
docs/            architecture, tools, samples, build notes, deck — index in docs/README.md
```

## Approach

**The harness came first.** `scoreboard.py` existed before the rules did, and
almost every finding below was found by running it. Its entries are thin
adapters over the engine; if one of them ever computes something itself, the
scoreboard has stopped testing the system.

**The rules were derived from the answer keys, not assumed.** The generator is
absent by design, so each of these was reverse-engineered and then verified
against all 150 crew or all six scenarios:

- **Accrual is additive.** `duty_hours_7d` and `flight_hours_28d` are
  `daily_history` *plus* the roster, on every day including the snapshot day —
  11 crew carry different nonzero values in each source on 2026-09-14 and the
  published field is their sum. Adding reproduces the published field for
  **150/150** crew; anything that de-duplicates does not. There is no dedupe
  flag, because there is nothing to de-duplicate.
- **The deadhead rule.** A positioned crew member is available from the next
  whole hour after the deadhead arrives, and report is one hour before
  departure. DX402 arriving 08:45Z therefore gives a 09:00Z report and a 10:00Z
  departure. That single rule produces the 3.0h delay in Q31/S2, the 7.0h in S5
  and both the 6.5h and 6.0h in S6, and it is also why the on-call window is
  tested against 09:00Z rather than the rostered 06:00Z.
- **Positioning is priced.** Callout + deadhead fee + the delay it causes.
  `18500 + 6500 + 3.0×5400 = 41200`, which is why an out-of-base reserve ranks
  below an in-base day-off callout.
- **Exclusion reporting stops at the first failing rule**, and reports every
  finding under it. A candidate who fails the rating *and* rest is excluded on
  the rating; a candidate with two duty-hour breaches gets both, joined `"; "`.
- **Rest findings are named after the duty that follows the gap** — `COVER
  (rest conflict)` when the cover is squeezed, the pairing id
  `(downstream conflict)` when the cover squeezes something else. A negative gap
  is stored signed and paired with a `double-booked:` finding. A human is never
  shown a negative rest figure; the timeline renders it as an overlap.
- **Check order is load-bearing:** base → rating → certification → FDP → rest →
  7-day duty → 28-day block, with the on-call window gating candidate selection
  ahead of all of them. The window is *not* a rule breach, which is how Q24 and
  S2 can key on different reasons for the same person.

**Scaffolding was earned, not assumed.** The runtime target is capable, so the
system prompt carries only rules that pay for themselves: what the model owns,
how to write a figure, and what it uniquely adds (ambiguity, which verdict was
asked, ties). There is no push-back heuristic for a model that announces a tool
instead of calling it — that belongs in the "add only if measured" pile, and it
has not been measured.

**Ten tools, not seventeen.** The seventeen in the brief are the question list
projected onto function names; most Tier-1 entries are one `SELECT … WHERE` and
several Tier-2/3 entries are the same engine primitive dressed differently.
Collapsing lookups into `lookup(entity, …)` and the what-ifs into
`simulate_disruption(kind, …)` gives the model fewer things to route between and
leaves the code as one engine with a few entry points. The design notes that
were load-bearing all survive: `check_assignment` still returns two verdicts,
`trace_disruption` is still separate from `resolve_cover` so "which flights are
uncrewed?" cannot trigger a 24-candidate ranking, and exclusions still live
inside the resolve result.

## Honest trade-offs

**The agent number is lower than the engine number, and it moved a lot.** The
first run through `gpt-5.6-luna` scored **21/38**. Fixing what that exposed — all
of it in this codebase, none of it in the model — took it to 32/38 as reported,
and to **35/36 gradable** once the scorer itself was fixed. The remaining failure
is a routing miss on Q27. Three fixes landed after the last full run and are not
live-verified; **run `python3 -m aircrew.replay` before quoting any number.**

The agent-level failures were almost all tool-surface defects invisible from the
engine: a model that fills every optional parameter with `""`, a hallucinated
year that made a closure return "0 flights affected" (wrong, but grounded), and
a tool that the 17→9 collapse had silently dropped. Two more were the *scorer*,
which escaped the answer keys' em dash and so could never match two correct
answers. Details in [docs/ISSUES.md](docs/ISSUES.md).

**The claim gate is eight checks, and one of them is a heuristic.** A reply is
refused if it carries a figure that appears in no tool result this turn, a
figure written out as a word, a verdict with nothing behind it, a verdict the
tool results contradict, a claim attached to the wrong subject, a claim id this
turn does not have, a tool name leaked into the prose, or a real figure written
under the wrong kind of label. The model is asked to write figures as
`{{claim:c7}}` placeholders, which the gate replaces with the engine's own
rendering, so a substituted figure arrives carrying the engine's words and
cannot land under the wrong label. A figure the model types itself gets the
label check instead: "the delay costs 486" is refused when 486 is that turn's
passenger count, because `mislabelled()` classifies both the field the figure
came from and the word it is written under, and rejects the mismatch.

That last check is a heuristic, not a proof, and it is the one place the gate
can be beaten by a correct-looking sentence. [docs/FLOW.md](docs/FLOW.md)
records what fifteen deliberately adversarial mislabels did to it — **9 caught,
6 missed** — and names the shape of every miss: a figure with no kind word near
it ("that comes to 486"), which leaves the check nothing to contradict. Closing
that gap would mean demanding a claim id for every figure in every sentence, and
a gate that fires on correct answers gets switched off.

**One case the system handles poorly** — a paid callout for someone already
working the pairing — is documented with analysis in
[docs/SAMPLES.md](docs/SAMPLES.md#e-a-case-the-system-handles-poorly). Two more
places where the reference contradicts itself, and everything that was tried and
thrown away, are in [docs/NOTES.md](docs/NOTES.md).

**Cross-checked against an earlier independent implementation** of the same
brief by the same author — a first pass, built and then replaced rather than
extended, sharing no code and no common ancestor with this one. Across all 156
(pairing, role) vacancies the **rank-1 recommendation is identical in 156/156**
and the full ranked list in 105/156; every remaining difference is the older
build offering the sole incumbent of a vacant role as a candidate to cover their
own vacancy. That implementation is not published here, so this figure is
reported rather than reproducible from this repository. Detail in
[docs/NOTES.md §8](docs/NOTES.md#8-compared-with-the-earlier-implementation).

**Two of the seven rules never eliminate anybody.** Across all 156 (pairing,
role) vacancies in the roster, RULE-FDP-01 and RULE-FLT-03 produce zero
exclusions — the maximum 28-day block in the whole dataset is 79.28h against a
100h limit, recomputed from `daily_history` plus the roster; the published
`flight_hours_28d` field peaks at 79.24h for the same crew member, C-2143. Both
are still reported in `rules_checked` because the keys require it, but "we
checked seven rules" would overstate what happened. Measurements in
[docs/NOTES.md §3](docs/NOTES.md#3-the-surprise-which-rules-actually-bind).

**The tenth tool was added back after measurement.** Collapsing seventeen to
nine dropped `earliest_next_report`, and the replay showed Q23 was unanswerable
without it. Nine was the right instinct; ten is the measured answer — the
earned-scaffolding rule working in reverse.

**Two questions have rubrics for answer keys** (Q36, Q38). They are marked
`GEN` and never counted as passes; grading a rubric against itself is a fake
pass. The honest denominator is 36, not 38.

**The half-open closure interval is an assumption.** No flight in this dataset
sits on a closure boundary, so inclusive and half-open bounds give identical
output on Q19, Q29 and S3. The half-open reading is implemented because it is
the conventional one, but the data does not settle it.

**Positioning considers same-day nonstop deadheads only.** No multi-hop routing
and no next-day positioning. Every case in the dataset is a single nonstop leg,
so this is untested beyond it.

**The workspace is one page and one theme.** Dark only, deliberately: this desk
is staffed overnight. A theme toggle that half-worked would be worse than one
committed theme, so there is no toggle.
