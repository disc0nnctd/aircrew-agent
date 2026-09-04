# TASK

Build the Crew Ops Advisor described below. This branch contains only the
problem statement and the dataset; everything else is yours to write.

Runtime target: an OpenAI-compatible model with tool calling, `gpt-5.6-luna`
class down to roughly 105B. This task assumes the model is capable and adds
scaffolding only where measurement shows it is needed.

The generator that produced the dataset is deliberately absent. Derive the rules
from `problem_statement/data/rules.json` and the answer keys, not from how the
data was made.

---

You are building a **Crew Ops Advisor** for an airline crew-control desk, as
specified in `problem_statement/problem_explanation_k66g3nx88t.pdf`. Read that first: all eight
pages, and the weighted evaluation criteria on page 6 twice. `pdftoppm` may be
unavailable; extract the text with `pypdf`.

The dataset is in `problem_statement/data/`: `flights.json`, `crew.json`, `rosters.json`,
`duty_clocks.json`, `reserve_pool.json`, `certifications.json`, `rules.json`,
`costs.json`, `risk_signals.json`, plus `scenarios.json` (6 worked disruptions
with answer keys) and `questions.json` (38 questions across three tiers, with
expected answers). If either of those two is missing, stop and say so. They are
the grading surface and everything below assumes them.

## The one architectural decision

The problem statement says the real question is architectural: what should the
model do, what should deterministic code do. Your answer is:

> **Deterministic Python computes every figure. The model chooses which
> question to ask and explains the result. It never calculates, and it is never
> in a position where it could.**

That last clause is a design constraint, not a rule in a prompt. If a tool
returns a ranked, priced list, the model has no reason to derive a number. If a
tool returns impact with no costs, say so *in the tool's own result*, because an
impact result looks answer-shaped and a model asked "what should I do?" will
answer from it and fill the gap with a plausible figure. We measured this:
moving that sentence from the result's `summary` into its `data` made the model
invent INR 1,250,000.

Corollaries, all of which cost time to find:

- No tool takes a cost, a duration, a count or a verdict as a parameter. There
  is nowhere for a remembered figure to enter.
- Any follow-up that changes the candidate pool is an **engine parameter**, not
  model reasoning. "What if the reserve is sick too?" must re-rank, re-price and
  re-check legality, not read the next row off the previous ranking. Without
  that parameter our chat recommended one person while the panel showed another.
- Tools accept what a controller actually says: a pairing id, or the crew member
  who dropped out, or aircraft plus date. Requiring the internal id forced extra
  lookup rounds and was the larger half of a routing failure that went 9/12 to
  66/66 once fixed.
- There is no way to bypass a legality rule and no flag that skips one. QA once
  caught the model offering `check_rules_only=false`. Inventing a capability is
  as serious as inventing a number.

## Build in this order, and gate each stage

The rubric rewards a polished Tier 1 with a credible Tier 2 over a broken
Tier 3, and three of the deliverables are documents. This order means a partial
result is still a strong submission.

**1. Harness first.** Loaders, a typed query layer, and a scoreboard that
replays all 38 questions with `pass` / `fail` / `TODO` / `GEN` states. Entries
are thin adapters over the engine, never reimplementations. `TODO` for
unimplemented, `GEN` for the two questions whose keys are rubrics rather than
values. Never count those as passes; grading a rubric against itself is a fake
pass. Run it after every stage. It is the forcing function, and nearly every
lesson below was found by it. Start the README and the boundary diagram in the
same hour: the diagram is what a judge reads for the 20% criterion, and it is
easy to leave unbuilt while polishing panels.

**2. Rules engine.** A `Finding` dataclass carrying `{rule, limit, actual,
excess, context}` with rendering kept separate, the seven rules read from
`rules.json`, and a week simulation. Gate: the per-rule questions, then a
scenario's full exclusion list byte for byte, then the question that tests check
*ordering* (see traps).

**3. Impact tracing.** Uncovered legs, downstream risk, passengers, station
closure, delay. Gate: the Tier-2 questions. Tiers 1 and 2 are now complete with
no model attached.

**4. Resolver.** Candidate enumeration, legality simulation per candidate,
costing, ranking, cancellation always last. Joint plans by exhaustive search:
the brief explicitly says a full optimisation solver is *not* expected. Gate:
the Tier-3 questions and all six scenarios. The engine is now demonstrable from
a CLI with no LLM, which is also your demo's safety net.

**5. Tool layer and agent loop.** Seventeen tools (below). A system prompt built
only from rules you have *earned*. Drive four flows live before opening a
browser: what should I do; what breaks; why was X ruled out; two captains sick
at once.

**6. Web UI, then the documents.** Standard library server, one page, no build
step.

## The tool surface: seventeen

**Tier 1 (6).** `list_flights(on_date?, dep?, arr?, flight_no?)`: the date is
optional so "longest block time in the schedule" is one call, not seven.
`get_pairing(pairing_id | aircraft + on_date | crew_id)`.
`crew_profile(crew_id, on_date?)`: one record with ratings, base, reserve
window, pairings this week, 7d duty and headroom, 28d block, risk score.
`list_crew(rank?, base?, rating?, on_date?, min_duty_hours_7d?)`: returns 7d
duty per row when dated, which answers "who is near the limit" in one call.
`list_reserves(on_date, base?)`. `certifications_expiring(from_date,
within_days)`.

**Tier 2 (7).** `trace_crew_unavailable`, `check_assignment`,
`station_closure_impact`, `delay_impact`, `cancellation_cost`,
`earliest_next_report`, `duty_timeline`.

**Tier 3 (4).** `resolve_cover`, `resolve_multiple`, `compare_candidates`,
`draft_notification`.

Three design notes that matter more than the count:

- **`check_assignment` returns both verdicts, not a flag.** "Does this breach a
  rule?" and "can we call this person out?" are different questions with
  different answers, and the dataset grades both. Return
  `{callable, reachability: [...], rules: {legal, breaches}}` and let the model
  report the half that was asked. A boolean parameter would be a false economy;
  two sections in one result is not.
- **`trace_crew_unavailable` stays separate from `resolve_cover`,** even though
  the latter contains the former's impact. "Which flights are uncrewed?" must be
  answerable without triggering a 150-candidate ranking and a recommendation
  nobody asked for.
- **Exclusions belong inside `resolve_cover`,** grouped by rule with an
  orientation line ("9 rest, 8 aircraft rating, 1 duty hours, 1 outside on-call")
  and visible rather than collapsed. We once built a separate tool for this. It
  existed only to fight a layout choice.

Do not add a tool that chooses what the UI displays. We built one. Two identical
recovery questions then rendered as two different screens depending on a model
decision the controller could not see, which under pressure is worse than any
particular layout.

## Scaffolding is earned, not assumed

The model is capable. Build the thin version first, measure, and add support
only where the measurement demands it. In this order, cheapest first:

1. **Steering in tool summaries.** Always build this. It is not a crutch; it is
   how the model learns what a result does not contain.
2. **Controller-facing identifiers on every tool.** Always build this. It
   removes whole rounds of lookup.
3. **Explicit routing lines in the system prompt**, one per question shape. Add
   when you see a tool chosen wrongly across phrasings.
4. **A push-back when the model announces a tool instead of calling it.** Only
   if measured. Our version ran 137 lines and fired zero times on a frontier
   model. If a smaller model needs it, the compact form is: after a turn with no
   tool calls, if the reply contains a registered tool name that was not called
   this turn *and* a deferral phrase within about 80 characters of it, send one
   corrective turn asking whether the original question still needs that tool.
   Normalise markdown out of the reply first, or `**Next step:** Call foo` will
   not match. Cap it at two per turn.
5. **Smaller payloads.** If routing degrades on a smaller model, cut what the
   model sees before adding rules telling it what to ignore.

Run the 38 questions through the agent after each addition. If a step does not
change the number, remove it.

## Traps that produce silently wrong numbers

Not things a careful reader catches. Each one cost real time.

**Check order is load-bearing:** base, then reserve window, then rating, then
per-day certification and FDP, then rest, then double-booking, then 7-day duty.
Report a rating failure alone. The flight-hour rule is declared in
`rules_checked` on every candidate but never eliminates anyone: the data maxes
at 79.28h against a 100h limit. The intuitive order reproduces a scenario's 19
exclusions perfectly and still fails one question where a candidate breaches
both the window and the rating and the key reports the window.

**Enumeration order** follows `duty_clocks.json` record order, not `crew.json`,
which is sorted on export. Exclusion lists in the keys follow the former.

**The snapshot day is double-counted** in the published 7d and 28d fields, and
correctly so: recomputing without a dedupe matches the published field for all
150 crew, while deduping produces 11 mismatches. Keep a `dedupe_overlap_day`
flag and default it off.

**Passenger counts are per-day, cancellation is per-leg.** In the same answer
key: 486 passengers (day 1 of a two-day pairing) and 1,500,000 cancellation (all
six legs). Reading the first as the total is the easiest wrong number here.

**The dominant constraint is downstream.** A candidate can be qualified, based,
and free on both cover days and still be illegal, because the pairing releases
them down-route and they have their own duty two days later. A naive "who is
free?" filter returns 13 candidates; the correct answer is 5, and 9 of the 19
exclusions are rest, not qualification.

**Rest is checked on both edges of every inserted duty**, against the merged
week. Six of those nine rest exclusions are physical clashes, where the cover
reports before the existing pairing releases. The reference stores that as a
signed negative gap plus a separate double-booking finding joined with `"; "`.
Keep that wording byte-exact for grading. Never show a human "negative rest":
render the absolute value as a duty overlap.

**Two delay conventions.** A technical delay holds report and slips release, so
duty grows and can breach. Positioning shifts both, so FDP is unchanged and only
the departure moves. Make it an explicit argument at every call site.

**The reserve window is tested against the required report time after
positioning**, inclusive bounds, not the callout time, despite what the rule
prose says. One candidate is judged on 09:00Z, not the rostered 06:00Z. Base and
window are two gates and the second consumes the first.

**FDP limit is `13 - 0.5 * max(0, sectors - 2)`.** Compare with an epsilon, or
duties sitting exactly on the limit become phantom breaches.

**Closure intervals are half-open `[start, end)`**, arrivals count as well as
departures, and the minimum delay runs to reopen plus 30 minutes.

**Costing:** reserve callout is cheaper than day-off; role must equal rank
exactly (Senior Cabin Crew is not Cabin Crew); only `status == "active"` crew are
candidates; rank by `(cost, crew_id)`; cancellation is appended last with a null
crew id and an empty `rules_checked`.

**Ties are the finding.** In the two-vacancy scenario, 20 of 157 joint plans tie
at the optimum. Take the first strict minimum in rank order for grading, and say
the tie out loud: when cost cannot separate the options, the decision is the
controller's and it turns on reachability.

**Certificate validity** is tested per duty date against `valid_to` only.
`valid_from` is often in the future and testing it excludes almost everyone.

**Byte-exact output.** Breach strings, `"; "` joins, and the em dash in the delay
recommendation are graded literally. A plain hyphen failed six questions. On
Windows, call `sys.stdout.reconfigure(encoding="utf-8")` or non-ASCII output
crashes the console.

**The PDF's own example is wrong:** it calls C-2087 an FO; the dataset has them
as a Captain. Trust the data.

## The interface

Two panes: conversation left, an operational workspace right that the agent
populates as it works. Beyond what follows, design it as you see fit. You may
well improve on what we did.

### Direction, before you write any CSS

State this in one line before generating, and hold it from the first panel to
the last:

> Reading this as: a crew-control desk tool for a controller working at 6 a.m.
> under time pressure, dense and high-contrast, with dials ENERGY 1 / RHYTHM 2 /
> MOTION 1.

ENERGY 1 because the screen is a working instrument, not a pitch: it should not
say hello. RHYTHM 2 because a recovery plan, a duty timeline and a comparison
are genuinely different shapes and should look it, while staying one family.
MOTION 1 because nothing should move on its own while somebody is reading a
figure they are about to act on. If you deviate from these, write why.

For every visual decision, write a one-line reason: why this colour, why this
spacing, why a table here and cards there. If you cannot write the reason, the
decision is not made yet. A technique is allowed when its purpose is
articulable, and forbidden when the only answer is that it looked right.

### Invariants, which are correctness rather than taste

- Panels render **only** from the tool result's structured data. Nothing is
  parsed out of the model's prose, so chat and workspace cannot disagree about a
  figure.
- Layout is deterministic. The agent chooses which tool to call; the tool owns
  how its result is drawn. The same question type always produces the same
  screen shape.
- Panel drill-downs call the tool endpoint directly, so the workspace works with
  the language model switched off. This is your live-demo safety net.
- Chat is the decision and the reason; the workspace is the evidence. When a
  panel already shows the ranked options, the reply states the recommendation
  and its decisive reason, then stops.

### Every number on screen is real, or it is not there

This is the same rule as the architecture, applied to pixels, and on this
product it is the whole game. A crew controller acting on an invented figure is
the failure the system exists to prevent.

- No placeholder statistics, no invented deltas, no "12,483 / 94.2% / +12% this
  week" stat rows. Show the figures the engine produced or show none.
- No fabricated activity feeds, no sample crew with invented names, no filler
  rows like `John Doe / johndoe@example.com`. Empty cells stay empty, or carry
  an honest label.
- Every chart answers a question you can write in its title. "Failed checks per
  candidate" is a chart; "Overview" is decoration. If a sentence answers it
  better, write the sentence.
- Table columns come from the decision the user makes in that table, with the
  deciding field early, not from `Name / Status / Date / Actions`.
- Do not draw a dashboard shell out of habit. Name the one decision each screen
  supports and build the hierarchy around it. If the job is "who can cover this
  and what does it cost", the ranked options are the page and everything else is
  a footnote.

### States, contrast, keyboard

- Empty, loading and error states are part of the design, not extras. Each says
  what happened and what to do next. "No data" tells the controller nothing:
  "No candidates match. Widen the base filter or check the on-call window" does.
  First run, filtered-to-nothing, and engine-unavailable are different screens.
- Every interactive element has real behaviour or does not exist. No dead
  buttons, no nav items pointing at sections that were never built.
- Text meets WCAG AA: 4.5:1 normal, 3:1 for large. Check it across the whole
  area the text sits on, not one pixel. Rule tags and status colours are
  information, so they must clear the bar too, including against their
  neighbours.
- Everything is reachable by Tab in visual order, activatable by Enter or Space,
  dismissable by Escape, with a visible focus indicator. Never remove the focus
  outline without replacing it. A controller at 6 a.m. is faster on the keyboard.
- No horizontal overflow at any width. Tap targets at least 44px.

### What not to reach for

These are the defaults a model produces when it is filling space rather than
solving a problem. None of them are banned techniques; they are banned as
*defaults*, and each may stay if you can write what it serves:

blue-to-purple or blue-to-cyan gradients as the primary treatment; a full-page
coloured glow; background grids, blueprint lines or dot patterns; glass blur on
more than one or two surfaces; a shadow on every component; glow on cards and
buttons and badges at once; every element pill-shaped; a palette past 2 to 3
core colours plus one accent, with the accent used at one key moment rather than
everywhere; sparkle, lightning, robot or orb icons; emoji in headings, bullets
or buttons; an arrow on every button; capsule badges reading "AI Powered" or
"Beta"; large monospace headings and wide-tracked uppercase labels as an
aesthetic; a bento mosaic; a fake terminal window; identical feature cards;
Undraw or 3D-blob illustrations; endless pulse or float animations.

Dark is a legitimate choice for an operations tool used overnight, so make it
deliberately and say why, and if you ship a theme toggle then both themes must
work completely.

Two questions to answer before you call the interface done. If the product name
were swapped out, would this still look like a specific tool for a specific job?
And would a controller find it faster than the spreadsheet they have now? If
either answer is no, it is not finished.

### The defects we shipped, so you can skip them

- A turn that draws no panel must **leave the existing workspace alone**. Ours
  cleared the canvas before knowing whether it had anything to replace it with,
  so a clarification blanked the plan the controller was reading.
- An agent gathers context first and commits last, so drawing panels in call
  order buries the answer under the lookups that produced it.
- Exclusions are what a controller most wants to challenge, and we put them
  collapsed at the bottom.
- Rule ids mean nothing alone. Carry the plain-English constraint with them.

### The panel that earns the workspace

The duty timeline: the merged week with the proposed cover inserted, the rest
gap between each pair of duties, and the breach marked. It is what makes
"qualified, based, free, and still illegal" legible at a glance. Build it as a
tool as well as a panel, so the agent can reach for it when asked why someone
cannot cover.

## Deliverables

The checklist is in the problem statement and three of the seven are documents.
Build them alongside the code, not after: the architecture diagram showing the
LLM and deterministic boundary, a README with setup, approach and honest
trade-offs, sample inputs and outputs **including one case the system handles
poorly with your analysis**, and a short deck. Overstating capability scores
badly; documented failure scores well.

One measurement to make before presenting: replay all 38 questions **through the
agent**, not only through the engine, and report that number. They are different,
and only one of them is what a judge will test.
