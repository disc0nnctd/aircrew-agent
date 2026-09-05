# One question, end to end

What actually happens between a controller typing a sentence and a figure
appearing on screen, traced through five scenarios. Every quoted line below is
real output, captured by running the code.

[TOOLS.md](TOOLS.md) says what each tool is for.
[TOOL_DESIGN.md](TOOL_DESIGN.md) says why they are shaped that way.
This is the runtime path.

## The shape of a turn

```
controller question
      |
      v
  model picks a tool and arguments        <- the one genuinely open choice
      |
      v
  dispatch -> engine                       <- all arithmetic lives here
      |
      v
  envelope: summary, data, claims, missing
      |
      +--> back to the model, up to 8 rounds
      |
      v
  model writes an answer, citing {{claim:c3}}
      |
      v
  the claim gate                           <- four checks, below
      |
      +-- passes --> answer + workspace panels, both from the same result
      |
      +-- fails ---> one corrective turn --> still failing --> answer withheld
```

The gate runs four checks on every reply. Three of them exist because a
specific failure was observed in a live run.

| # | Check | Catches |
| --- | --- | --- |
| 1 | **Substitution** | the model citing a claim id: the text comes from the engine, so it cannot be wrong |
| 2 | **Ungrounded figures** | a number the model typed that no tool returned this turn |
| 3 | **Mislabelled figures** | a number that *is* real, attached to the wrong kind of thing |
| 4 | **Unknown or malformed claim id** | a citation this turn cannot satisfy, and raw placeholder text |

---

## Scenario A: the normal path

**Controller:** *"Captain C-1042 is out for pairing P-2291. What should I do?"*

**Round 1.** The model calls `trace_disruption(crew_id="C-1042",
pairing_id="P-2291")` to find out what is actually broken. The engine returns:

```
summary: Captain C-1042 off P-2291. Day-1 passenger count is per day,
         not the pairing total.

claims:
  c1  3 flights uncovered on day 1: DX412-2026-09-15, DX413-2026-09-15, DX588-2026-09-15
  c2  486 passengers on day 1
  c3  3 further flights at risk on day 2: DX589-2026-09-16, ...

missing:
  no candidates ranked and no costs computed here; call resolve_cover
  passenger figures are per day; cancellation cost is per leg
```

Two things in that envelope are doing work beyond the data.

The **summary** pre-empts a specific mistake. 486 is the day-1 count, and a
model summing days would say 972. Saying so in the summary is cheaper and more
reliable than hoping.

The **missing** list is the anti-hallucination field. This result looks like an
answer to "what should I do?" and is not one. Left unsaid, a model asked for a
recommendation will invent a cost to complete the shape of the answer. That is
not hypothetical: it is exactly how the invented INR 1,250,000 in Scenario B
first appeared.

**Round 2.** Steered by `missing`, the model calls `resolve_cover(pairing_id=
"P-2291", vacated_by="C-1042")`, which returns the ranked options, the pricing
and the 19 exclusions with the rule that stopped each.

**The answer.** The model writes:

```
Assign {{claim:c3}}. {{claim:c1}}, and {{claim:c2}}.
```

and the gate renders:

> Assign **the cheapest legal option is Assign Captain C-3310 (reserve callout)
> at INR 18,500**. **5 candidates are legal**, and **19 candidates are excluded
> (9 rest, 8 aircraft rating, 1 duty hours, 1 base / on-call window)**.

Not one digit on that screen was typed by the model. It chose which facts to
cite; the engine supplied every number and its wording.

**The workspace** draws from the same `data` the claims came from, which is why
the panel and the prose cannot disagree. It draws the *most decisive* result,
not the last one: the model often verifies its own answer with a final
`check_assignment`, and drawing that would replace the ranked plan with a
two-line confirmation.

---

## Scenario B: the model invents a figure

**The draft:** *"Cancelling would cost INR 1,250,000."*

1,250,000 appears nowhere in this turn's results. Check 2 fires:

```
ok: False | ungrounded: ['1,250,000']

Your reply was not sent. these figures are not in any tool result from
this turn: 1,250,000. Every figure and verdict must come from a tool
result. Call the tool that computes it -- check_assignment for legality,
resolve_cover for cost and ranking, validate for a statement you want
checked -- then answer using {{claim:ID}} placeholders.
```

The model gets **one** corrective turn. If it grounds the figure, the answer
goes out. If it repeats the claim, the answer is withheld and the controller is
told so:

> I could not ground every figure in that answer against a computed result, so
> I am not stating it. The workspace shows what the engine did compute.

**Why one retry and not three.** A model that cannot ground a figure on the
second attempt is not going to find it on the fourth, and each round costs a
controller seconds they do not have. Withholding is the correct failure: a
missing number is recoverable, a wrong one is not.

**Where this bug came from.** The steer "this result contains no costs" was
moved out of a tool's `summary` and into the system prompt, on the theory that
one clear instruction beats repetition. The model invented INR 1,250,000 on the
next run. The steer moved back into the tool result and the invention stopped.
Prompts advise; results constrain.

---

## Scenario C: a real number under the wrong label

This is the one the other checks cannot see.

**The draft:** *"The delay costs 486."*

486 is real. It is this turn's day-1 passenger count. Check 2 passes it happily,
because the figure genuinely came from a tool.

Check 3 asks a different question: not *is this number real* but *is it the
right kind of number for the sentence it is in.* Both halves are cheap:

- **The engine knows what the field holds.** 486 lives at `passengers_day1` and
  `by_day[].passengers`. Field names ending in `passengers` are a `people` kind.
- **The sentence says what it thinks it is quoting.** The words around the digits
  are "the delay costs", which is a `money` kind.

They conflict, so the figure is flagged:

```
ok: False
flagged: [{'figure': '486', 'is': 'people', 'written_as': ['money'],
           'fields': ['by_day[].passengers', 'passengers_day1']}]

Your reply was not sent. 486 is a people figure in this turn's results
(by_day[].passengers, passengers_day1), but you wrote it as money.
```

**It is deliberately conservative**, because a gate that fires on correct
answers gets switched off. It stays silent unless the figure lives in fields of
exactly one kind *and* the sentence clearly asserts a different one. Small
numbers on the existing allowlist are skipped entirely, since "day 1" would
otherwise flag forever.

Measured against every real reply captured in the live runs and every claim the
engine produces: **4 of 4 mislabels caught, 0 false positives.**

**Why this is not a second model.** An LLM validator would be a language model
checking a language model, at double the latency, with no guarantee the checker
is right. The label check is a dictionary lookup and a regex over 28 characters
of context. It is deterministic, it is testable, and it cannot itself
hallucinate. The rule the whole product rests on applies to its own safeguards:
if a mechanism can do the job, a model should not.

---

## Scenario D: the model breaks the citation

Three distinct failures live here, all caught by check 4.

**An id this turn does not have.** The model writes `{claim:c99}` when the turn
minted c1 to c9:

```
ok: False | rendered: Assign [unknown claim c99].
```

Failed, corrected, and the placeholder text never reaches a controller.

**The wrong brace count.** The documented form is `{{claim:c3}}`; luna writes
`{claim:c3}` often enough to matter. The gate used to match only the double
form, so in one live run an entire answer rendered as the literal text
`{claim:c1}.` Both forms now substitute, and any surviving placeholder-shaped
text fails the turn rather than printing.

**Ids that drift out of range.** Claim ids come from a process-global counter,
so by the twentieth question of a replay they read `c200+` while the prompt's
example says `{{claim:c7}}`, and the model copies the example. That cited a
non-existent id and burned a correction round on **13 of 38 questions**. Ids are
now renumbered `c1, c2, c3...` within every turn, so they are small, predictable
and cannot collide with the example.

---

## Scenario E: the question cannot be answered

**Controller:** *"What happened on 12 March 2025?"*

The model calls `simulate_disruption(kind="closure", station="BLR",
on_date="2025-03-12", ...)`. The tool refuses rather than returning nothing:

```
summary: 2025-03-12 is outside the schedule, which runs 2026-09-14 to
         2026-09-20. No result was computed -- check the year.

claims: 2025-03-12 is outside the schedule, which covers 2026-09-14 to
        2026-09-20, so nothing was computed

data:   {"error": "date outside schedule", "requested": "2025-03-12",
         "schedule_from": "2026-09-14", "schedule_to": "2026-09-20"}
```

**Why a refusal and not an empty list.** This came from a real failure. The model
hallucinated a 2025 date, the closure matched no flights, and it reported "0
flights affected" — wrong, but *grounded*, because zero is a real result of a
real query. The claim gate had nothing to catch. An empty result is
indistinguishable from an answer, so the tool now declines to produce one.

The refusal carries a **claim**, so the model can cite it and say what the window
is instead of shrugging. The workspace shows the refusal too, rather than
leaving the previous question's evidence on screen.

---

## Where an LLM validator would actually earn its place

Checks 2, 3 and 4 are mechanisms, and a mechanism beats a model every time: it
is faster, cheaper, testable, and it cannot be talked out of its answer. Adding
an agent to do their work would make the system slower and less trustworthy.

What none of them can judge is **prose that contains no figures at all**:

- *"C-3310 is the safest choice"* — safety is not a field in any payload.
- *"the deadhead is worth the delay"* — a judgement about a trade-off.
- *"you should probably cancel"* — a recommendation contradicting the ranking.

The gate reads those and finds nothing to check. A reviewer model could compare
the prose against the structured result and flag a recommendation the ranking
does not support. That is a real gap and a fair use of a second model.

It has not been built, and it should not be built before it is measured: the
right first step is to count how often the model actually says something like
that, from the recorded replay runs. Building a second model to catch a failure
nobody has counted is how a system acquires machinery it cannot justify.
