# Documents

Fifteen files besides this index. What each one is, and who it is for.

| File | For | What it holds |
| --- | --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | a judge | The model/engine boundary, the claim gate, and the authority table. Start here. |
| [TOOLS.md](TOOLS.md) | an engineer | The ten tools, the `{summary, claims, missing, data}` envelope, and the measured file-access list for each. |
| [TOOL_DESIGN.md](TOOL_DESIGN.md) | a sceptical engineer | Why one tool joins seven data files instead of seven tools joining none, priced in tokens rather than asserted. |
| [FLOW.md](FLOW.md) | a judge | One question end to end, then the gate attacked with fifteen adversarial mislabels: 9 caught, 6 missed, every miss named. |
| [THE_38_QUESTIONS.md](THE_38_QUESTIONS.md) | a judge holding the answer key | All 38 questions, what each is really asking, and the call that produces the answer. |
| [DESCRIPTION.md](DESCRIPTION.md) | a judge reading one thing | The product in prose, and the tier-by-tier coverage argument. |
| [SAMPLES.md](SAMPLES.md) | anyone | Six worked CLI transcripts, including [§E, the case the system handles poorly](SAMPLES.md#e-a-case-the-system-handles-poorly). |
| [NOTES.md](NOTES.md) | a senior engineer | How each rule was recovered from the keys and verified, which rules never bind, and every dead end. |
| [ISSUES.md](ISSUES.md) | anyone | What is still wrong, what is unverified, and every bug found with its evidence. |
| [REVIEW_DISPOSITION.md](REVIEW_DISPOSITION.md) | a judge | Two outside reviews: what was fixed, what was rejected, and the evidence for rejecting it. |
| [REVIEW_ASTRA_FINDINGS.md](REVIEW_ASTRA_FINDINGS.md) | a judge | One of those reviews, unedited, as it arrived. |
| [DECK.md](DECK.md) | a reader | Ten slides written to be read. |
| [crew_ops_advisor_project_deck.pptx](crew_ops_advisor_project_deck.pptx) | a room | The presented five slides. `DECK.md` is the same argument at length. |
| [architecture-system-overview.png](architecture-system-overview.png) | anyone | The system in one picture, 1721×1143. It is embedded at the top of `ARCHITECTURE.md`. |
| [ARCHITECTURE.drawio](ARCHITECTURE.drawio) | an engineer | Editable source for that picture. Three pages; the export is page "01 System overview". |

Not in this directory, and not ours: [`problem_statement/`](../problem_statement/) is the
organisers' brief and dataset. It went in as one commit and nothing in it has been edited since,
including the notes they wrote to each other.

The numbers these documents quote are reproducible from the repository root:

```bash
python3 -m aircrew.scoreboard         # 36/36 gradable, 19/19 scenario checks
python3 -m tests.test_agent_loop      # 30/30
npm i && node tests/ui_check.js       # 127 DOM checks
python3 -m tests.test_review_astra    # 9/13, the other 4 fail on purpose
```
