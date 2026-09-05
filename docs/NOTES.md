# Build notes and observations

Working notes from building the Crew Ops Advisor: what the dataset turned out to
be, how each rule was recovered, which of them actually matter, where the
reference contradicts itself, and what was tried and thrown away.

Everything numeric here was measured on the shipped data and can be reproduced.

---

## 1. What the dataset actually is

```
150 crew        28 Captain · 29 First Officer · 26 Senior Cabin Crew · 67 Cabin Crew
                142 active · 6 leave · 2 training
                138 based BLR · 12 based DEL
                96 A320-only · 27 ATR72-only · 27 dual-rated
147 flights     105 A320 legs (162 seats) · 42 ATR72 legs (72 seats)
                7 days, 2026-09-14 to 2026-09-20
 39 pairings    16 reserves on the pool (12 BLR, 4 DEL)
600 certs       4 per crew member: licence, medical_class1, recurrent_training,
                dangerous_goods
```

Two facts about this shape drive almost everything downstream:

- **The network is a BLR hub with a 12-person DEL outstation.** Every
  out-of-base cover is a DEL→BLR deadhead, and there is exactly one such flight
  most days (DX402, arriving 08:45Z), sometimes two (DX589, 07:45Z). That is why
  positioning delays cluster on a handful of values.
- **Ratings split the fleet cleanly.** 96 crew cannot touch an ATR72 and 27
  cannot touch an A320. Rating is therefore the single largest source of
  exclusions, by a wide margin — see §3.

---

## 2. How each rule was recovered

The generator is absent by design, so every rule below was inferred from the
answer keys and then verified against the whole dataset rather than the one case
that suggested it.

### Accrual: additive, no de-duplication

`duty_hours_7d` and `flight_hours_28d` are `daily_history` **plus** the roster,
counted on every day in the window including the snapshot day.

| Model | 7d mismatches | 28d mismatches |
| --- | --- | --- |
| `daily_history` only | 32 / 150 | 32 / 150 |
| `daily_history + roster` | **0 / 150** | **0 / 150** |

11 crew carry different nonzero values in the two sources on 2026-09-14, and the
published field is their sum. This is worth stating carefully because it is easy
to describe as a "double count to be de-duplicated": there is nothing to
de-duplicate, the two sources are different records of different things and the
published field adds them. `RulesEngine.accrued` therefore has no dedupe flag,
because a flag implies a defensible other setting and there isn't one.

Cross-checked independently on Q26 ("45+ duty hours in the 7 days ending
15 Sep"), which needs the window to run one day past `daily_history` and pick up
rostered duty. Reproduces `C-2087 51.83` and `C-3305 50.0` exactly.

### The deadhead rule

A positioned crew member is available from **the next whole hour after the
deadhead arrives**, and report is one hour before departure. One rule, four
independent confirmations:

| Case | Deadhead arrives | Report | New departure | Original | Delay | Key |
| --- | --- | --- | --- | --- | --- | --- |
| Q31 / S2 | 08:45Z | 09:00Z | 10:00Z | 07:00Z | 3.0h | 3.0h ✓ |
| S5 | 08:45Z | 09:00Z | 10:00Z | 03:00Z | 7.0h | 7.0h ✓ |
| S6 DXA | 07:45Z | 08:00Z | 09:00Z | 02:30Z | 6.5h | 6.5h ✓ |
| S6 DXB | 07:45Z | 08:00Z | 09:00Z | 03:00Z | 6.0h | 6.0h ✓ |

The same rule explains a detail that looks arbitrary in isolation: the reserve
on-call window is tested against **09:00Z, not the rostered 06:00Z**, because
09:00Z is the report time the candidate actually has to make once positioned.
Base and window are two gates and the second consumes the first.

Cost then follows: `callout + deadhead fee + delay × delay_cost_per_duty_hour`.
`18500 + 6500 + 3.0×5400 = 41200` ✓, and the same arithmetic reproduces 53800,
56800, 60100 and 57400 across S5 and S6.

### Exclusion reporting stops at the first failing rule

A candidate is excluded on the **first** rule that stops them, and every finding
under *that* rule is reported, joined `"; "`.

- S1 C-1042 fails both the rating and rest; the key reports the rating alone.
- S2 C-2087 has two RULE-DUTY-02 findings; the key reports both, joined.

Running all seven checks and concatenating everything would tell a controller
that a rating problem is also a rest problem, which is not a fact about the
candidate — it is an artefact of having checked anyway.

### Rest findings are named after the duty that *follows* the gap

Two forms, both graded literally, and the distinction is which duty the gap
protects:

```
RULE-REST-04: only 10.75h rest before P-2204 on 2026-09-17 (downstream conflict)
RULE-REST-04: only -10.75h rest before COVER on 2026-09-19 (rest conflict)
double-booked: P-2206 overlaps COVER on 2026-09-19
```

`COVER` when the cover is the one being squeezed, the pairing id when the cover
is doing the squeezing. Negative gaps are stored signed and paired with a
`double-booked:` finding. The grammar was recovered by extracting every distinct
`reason` string in the keys and normalising the numbers out of them — 8 distinct
templates in total, which is a small enough surface to match exactly.

A human is never shown a negative rest figure: `duty_timeline` renders it as
`overlaps the previous duty by 10.75h — cannot be worked`.

### A cover replaces, it does not stack

When simulating a cover, the candidate's own rostered duty **on that pairing and
date** is removed from the merged week. Without this, anyone already rostered on
the pairing collides with themselves. This is correct in general — a person
cannot be double-booked against themselves — and it is what makes S5's inclusion
of C-2840 and C-4588 reachable at all. See §4.

---

## 3. The surprise: which rules actually bind

Resolving **every** (pairing, role) vacancy in the roster — 156 of them — and
counting why candidates were excluded:

| Rule | Exclusions | |
| --- | ---: | --- |
| RULE-QUAL-05 aircraft rating | 1879 | 66% |
| RULE-REST-04 rest / double-booking | 664 | 23% |
| RULE-BASE-07 base or on-call window | 256 | 9% |
| RULE-CERT-06 certification | 17 | 0.6% |
| RULE-DUTY-02 60h in 7 days | 13 | 0.5% |
| **RULE-FDP-01 duty period** | **0** | never |
| **RULE-FLT-03 100h in 28 days** | **0** | never |

**Two of the seven rules never eliminate anybody.** The maximum 28-day block
across every crew member and every roster day is **79.28h against a 100h limit**
(C-2143 on 2026-09-20) — 21 hours of headroom that nothing in this week comes
close to consuming. RULE-FDP-01 never fires as an *exclusion* either, though it
is the rule that breaks under a delay (Q20, S4), which is a different question.

Both must still be listed in `rules_checked` on every candidate — the keys
require it — but a system that presented "we checked seven rules" as
reassurance would be overstating what happened. Five rules do the work and two
are load-bearing only in the delay path.

The pool numbers, same 156 vacancies:

```
candidate pool     min 24   max 63   mean 34.5
legal survivors    min  5   max 45   mean 16.4
vacancies with no legal cover at all: 0
```

Every vacancy in this dataset is coverable. There is no scenario where the
honest answer is "cancel", which is worth knowing before demoing: cancellation
is always last in the ranking and always loses.

### Downstream rest is the constraint people miss

For P-2291 (the flagship scenario), a naive "rated A320, based BLR, active, and
not already working on either cover day" filter returns **9** captains. The
correct answer is **5**. The four that drop out are all downstream: the pairing
releases them at DEL on the 16th and they have their own duty on the 17th.

*(TASK.md quotes 13 for the naive figure; I get 9. The difference is which
filter you call naive — mine already excludes crew rostered on the cover days.
The finding is the same shape either way: roughly half the plausible-looking
candidates are eliminated by duties that happen after the cover ends.)*

### Ties are common, not exotic

S6 enumerates **157 legal joint plans**, of which **20 tie at the optimum of
INR 42,500**. Cost separates nothing at the top of that list. The system says so
out loud rather than presenting the first row as if it were uniquely correct —
when cost cannot decide, the decision is the controller's, and it turns on
reachability.

---

## 4. Where the reference contradicts itself

Three places where the answer keys are not internally consistent. All three are
reproduced rather than corrected, because the keys are the grading surface, but
each is a decision someone should make deliberately rather than inherit.

**Crew already on the pairing are offered as paid callouts.** S5 ranks C-2840
(rank 15) and C-4588 (rank 29) as INR 12,500 day-off callouts to cover P-2213 —
and both are already rostered as Cabin Crew on P-2213 that day. A controller
taking rank 15 would end up one cabin crew short and one callout fee poorer.
Full analysis in [SAMPLES.md §E](SAMPLES.md#e-a-case-the-system-handles-poorly).

**Q33 mixes delay conventions within one answer.** The breach that forces a leg
off the duty is computed with report *held* (technical delay, 12.75h vs 12.0h).
The FDP quoted for the retained three legs is computed with report *re-timed*
(9.5h, not 11.0h). Both figures are defensible — if you know about the delay
before report, you can hold the crew back, and then the retained legs run at
their nominal FDP — but they are not the same convention. The engine computes
and exposes both (`kept_fdp_report_held`, `kept_fdp_report_retimed`) and quotes
the re-timed one, because the recommended action *is* to re-time the report.

**Q34 is S5 truncated to three rows.** Same resolve, same ranking; the question
key stops at rank 3 while the scenario key runs to 43. This is presentation, not
disagreement, but it forced a `limit` parameter on `resolve_cover` that turns out
to be a real product need anyway: 43 options is not a decision aid.

---

## 5. Dead ends, and what each cost

| Tried | Why it broke | Fix |
| --- | --- | --- |
| Skip crew already on the pairing | Reference lists two of them as candidates | Reverted; documented as a known defect |
| Tie-break joint plans by crew id | Picked C-1017 where the key picks C-3305 | Break on rank position within each vacancy |
| Sort expiring certs by date | Key follows `certifications.json` record order | Preserve file order |
| Schedule order for the longest-block flight list | Key is alphabetical | `sorted()` |
| `{gap:g}` formatting for rest hours | Key writes `10.0h`, `:g` writes `10h` | Plain `str(float)` |
| Enumerate candidates in `crew.json` order | Exclusion lists follow `duty_clocks.json` | `enumeration_order` from duty clocks |
| Reserve window as a rule breach | Q24 keys on the duty breach; S2 excludes the same person on the window | Window gates *selection*; never enters `breaches` |
| `\d[\d,]*\.?\d*` for the grounding regex | Swallowed the sentence's full stop into the figure | Trailing guard rejecting `[\d,:/-]` and `\.\d` |

The reserve-window one is the interesting entry. It is the concrete evidence for
`check_assignment` returning two verdicts instead of a boolean: the dataset
genuinely asks two different questions about the same person and expects two
different answers. A single `legal` flag makes one of them unreachable.

The regex one is small but instructive: the first version flagged
`INR 1,250,000.` as the figure `1,250` because backtracking found a shorter
match that satisfied the lookahead. A gate that reports the wrong figure in its
own error message is a gate people learn to ignore.

---

## 6. What I could not settle

- **Half-open vs inclusive closure intervals.** No flight in this dataset sits
  on a closure boundary, so `[start, end)` and `[start, end]` produce identical
  output on Q19, Q29 and S3. Half-open is implemented because it is conventional.
  The data does not decide it.
- **The FDP epsilon.** Zero rostered duties sit exactly on their FDP limit, so
  the float-comparison guard is untestable here. It stays in, because the failure
  it prevents (a phantom breach on a duty that is exactly legal) is silent.
- **Multi-hop and next-day positioning.** Every positioning case in the data is
  a single same-day nonstop. `Engine.positioning` handles only that. Untested
  beyond it, and it would return "no deadhead available" rather than a wrong
  answer if asked for more.
- **Whether `valid_from` is ever meant to matter.** RULE-CERT-06 says "valid on
  the duty date", but many `valid_from` dates are years in the future (C-1042's
  licence starts 2030). Testing both bounds excludes almost everyone, so only
  `valid_to` is tested. This matches every key, but it means the rule as
  implemented is narrower than the rule as written.

---

## 7. Observations on the claim gate

Notes from building the thing that enforces the boundary, since this is the part
that was designed rather than recovered.

**Substitution beats checking, and both are needed.** `{{claim:c7}}` is
categorically safer than any check — the model never types the figure, so it
cannot mistype it. But a system that *only* allowed placeholders would be
unusable: the model legitimately needs to say "486 passengers" while quoting a
field the tool returned, and forcing a claim id for every such quote makes the
prose unreadable. So placeholders are preferred and free-typed figures are
checked.

**The gate matches figures, not meaning — and that is a real limit.** A number
passes if it appears anywhere in the turn's tool results. "The delay costs 486"
would pass when 486 is that turn's passenger count. The gate catches *invention*,
which is the dangerous failure; it does not catch a real figure attached to the
wrong label. Tightening it means demanding a claim id for every figure, and a
gate that fires on correct answers is a gate that gets switched off.

**What counts as a figure needed care.** Identifiers (`C-1042`, `DX401`,
`P-2291`), dates (`2026-09-15`) and clock times (`09:00Z`) are not figures a
controller acts on, and flagging them would make the gate useless. Rule limits
(12, 13, 28, 30, 60, 100) are vocabulary rather than results. Everything else
must be accounted for.

**Refusing is a feature.** If the model cannot ground a figure on the second
attempt, the answer is withheld. On a crew desk no answer is recoverable — the
controller reaches for the spreadsheet — and a confident wrong number is not.

**The `missing` list has to live in the result, not the prompt.** This is the
single most transferable observation from the original brief and it held up: an
impact result *looks* answer-shaped, and a model asked "what should I do?" will
answer from it. `trace_disruption` returning `"no candidates ranked and no costs
computed here"` inside its own payload is what stops that, because it arrives
attached to the thing that would otherwise be over-read.

---

## 8. If there were another day

In the order I would actually do them:

1. **Run `replay.py`.** The through-the-agent number is the one a judge tests
   and it is currently unknown. Everything below is speculation until it exists.
2. **Measure whether nine tools route better or worse than a flatter surface.**
   The argument for collapsing lookups into `lookup(entity, …)` is readability;
   the argument against is that a union argument is harder to route. Only the
   replay can settle it, and the wrappers are thin either way.
3. **A "disagree with the reference" layer.** The S5 defect is not really about
   S5 — it is that the dataset is the specification, and where the dataset is
   wrong the system is wrong with it. A production build needs to be able to
   flag "the reference says X, I compute Y" rather than silently pick one. That
   is the same problem this product already solves for the language model,
   applied one level up.
4. **Per-claim citation in the UI.** Every figure on screen already traces to a
   claim id internally; surfacing that as a hover would let a controller see
   which rule and which record produced a number without leaving the panel.
5. **Delete the `validate` tool if the replay says it is unused.** It exists
   because the brief asked for an explicit route from hypothesis to verdict, and
   it duplicates no logic. But `check_assignment` is already that route, and a
   tool that never gets called is a tool the model has to read past.
