"""The claim gate.

The model is allowed to think out loud, propose, hedge and explain. It is not
allowed to put a figure or a verdict on a controller's screen unless a
deterministic function produced it in this conversation.

Two mechanisms, in order of preference:

1. **Substitution.** The model writes `{{claim:c12}}`; the renderer replaces it
   with that claim's validated text. A figure written this way cannot be wrong,
   because the model never typed it.
2. **Checking.** Any number or verdict word the model typed directly is checked
   against every figure the tools returned this turn. Anything unaccounted for
   is reported, and the caller sends one corrective turn.

This is deliberately not a filter that rewrites the model's prose. Silently
correcting a figure would hide the failure; a crew controller needs to know the
system disagreed with itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# The documented form is {{claim:c3}}, but a model that writes {claim:c3}
# once in twenty turns puts a raw token on a controller's screen, which
# looks broken and hides the figure. Accept either, and tolerate spaces.
CLAIM_RE = re.compile(r"\{\{?\s*claim:\s*(c\d+)\s*\}?\}")

# Anything still shaped like a placeholder after substitution never reaches
# the screen: it means the model cited an id this turn does not have.
LEFTOVER_RE = re.compile(r"\{\{?\s*claim:[^}]*\}?\}")

# Numbers a controller reads and acts on. Ordinals, list positions and years are
# not figures in this sense, so a short allowlist keeps the gate from crying
# wolf on "the 7 rules" or "2026-09-15".
# A figure is a run of digits that is not part of an identifier (C-1042,
# DX401, P-2291), a date (2026-09-15) or a clock time (09:00Z). The trailing
# guard rejects a following digit, comma, colon or hyphen, so a sentence's full
# stop is not swallowed into the number but "12.75h" still reads as 12.75.
NUMBER_RE = re.compile(r"(?<![\w:/-])(\d[\d,]*(?:\.\d+)?)(?![\d,:/-]|\.\d)")

VERDICT_RE = re.compile(
    r"\b(legal|illegal|legally|breach(?:es|ed)?|compliant|"
    r"cheapest|callable|qualified|unqualified|eligible|ineligible)\b",
    re.I,
)

# A figure written as prose is invisible to a check that looks for digits.
# "Cancelling would cost one million two hundred and fifty thousand rupees"
# passed every test above while "INR 1,250,000" was caught, which made the
# whole gate a formatting preference rather than a guarantee.
#
# Only magnitude words are matched, not "three" or "both": the engine always
# emits figures as digits, so a magnitude word in a reply is prose standing in
# for a number. Measured against every string the engine produces and every
# reply captured in the live runs: zero of 36 would be flagged.
MAGNITUDE_RE = re.compile(
    r"\b(hundreds?|thousands?|lakhs?|crores?|millions?|billions?)\b", re.I
)

# A day written next to its month is a date, not a figure. Controllers say
# "15 Sep" constantly, and the gate was failing correct answers for it: an A/B
# on the live model produced "three flights on 15 Sep and three on 16 Sep",
# which is entirely true and cost a corrective round. A gate that fires on
# correct answers is a gate that gets switched off.
MONTHS = r"jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
DATE_NUM_RE = re.compile(
    r"(?:\b(?:" + MONTHS + r")[a-z]*\.?\s+\d{1,2}\b"      # Sep 15
    r"|\b\d{1,2}\s*(?:st|nd|rd|th)?\s+(?:" + MONTHS + r")[a-z]*\.?"  # 15 Sep
    r"(?:\s+(?:19|20)\d\d)?)",                            # 15 Sep 2026
    re.I,
)

# The controller does not know what a tool is called. "call resolve_cover" in a
# reply reads as a debug log leaking into an operational answer, and the names
# come straight from the `missing` field, which the model is right to be
# reading. So the steer stays technical and the reply has to be plain.
TOOL_NAMES = (
    "lookup", "crew_profile", "trace_disruption", "check_assignment",
    "duty_timeline", "simulate_disruption", "resolve_cover",
    "earliest_next_report", "draft_notification", "validate",
)
TOOL_NAME_RE = re.compile(r"\b(" + "|".join(TOOL_NAMES) + r")\b")


def tool_names_in(reply: str) -> list[str]:
    """Tool names the controller should never have been shown."""
    return sorted({m.group(1) for m in TOOL_NAME_RE.finditer(reply)})


# Figures that are part of the vocabulary rather than a computed result.
ALWAYS_OK = {
    "0", "1", "2", "3", "4", "5", "6", "7",       # small counts, rule ordinals
    "12", "13", "28", "30", "60", "100",           # the rule limits themselves
    "01", "02", "03", "04", "05", "06", "07",      # rule id suffixes
}


# A figure can be real and still be wrong: "the delay costs 486" when 486 is
# this turn's passenger count passes every check above, because 486 genuinely
# came from a tool. So the second question is not "is this number real?" but
# "is it the right KIND of number for the sentence it is in?"
#
# Both halves are cheap to answer. The engine knows what each field holds, from
# its name; the reply says what it thinks it is quoting, from the words around
# the digits. A conflict between the two is a mislabel.
#
# This is deliberately conservative. It fires only when the figure lives in
# fields of exactly one kind and the sentence clearly asserts a different one,
# because a gate that fires on correct answers gets switched off.
FIELD_KIND = [
    ("money", r"cost|inr|price|fee|fare"),
    ("people", r"passenger|seats|pax"),
    ("hours", r"hours|fdp|rest|block|headroom"),
    ("minutes", r"minutes|reachab"),
    ("count", r"count|total_flights|sectors|plan_count"),
]
WORD_KIND = [
    ("money", r"\b(inr|rs|rupees?|costs?|costing|price[ds]?|cheape[rs]t?)\b"),
    ("people", r"\b(passengers?|pax|seats?)\b"),
    ("hours", r"\b(hours?|rest|duty|fdp|headroom|block)\b"),
    ("minutes", r"\b(minutes?|mins?)\b"),
    ("count", r"\b(flights?|legs?|candidates?|options?|crew|plans?|pairings?|sectors?)\b"),
]


def _field_kind(path: str) -> str | None:
    low = path.lower()
    for kind, rx in FIELD_KIND:
        if re.search(rx, low):
            return kind
    return None


def _said_kinds(text: str, start: int, end: int, window: int = 28) -> set[str]:
    """What the sentence around the number claims it is."""
    ctx = text[max(0, start - window):end + window].lower()
    return {kind for kind, rx in WORD_KIND if re.search(rx, ctx)}


def _figure_fields(tool_results: list[dict]) -> dict[str, set[str]]:
    """Every numeric value in this turn, and the field paths it came from."""
    out: dict[str, set[str]] = {}

    def walk(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for v in node:
                walk(v, path + "[]")
        elif isinstance(node, (int, float)) and not isinstance(node, bool):
            out.setdefault(_norm_num(node), set()).add(path)

    for r in tool_results:
        walk(r.get("data"))
    return out


def mislabelled(reply: str, tool_results: list[dict]) -> list[dict]:
    """Figures that are real but attached to the wrong kind of thing."""
    fields = _figure_fields(tool_results)
    found = []
    for m in NUMBER_RE.finditer(reply):
        raw = m.group(1)
        norm = _norm_num(raw)
        if norm in ALWAYS_OK:
            # Small counts appear in every payload; "day 1" would flag forever.
            continue
        paths = fields.get(norm)
        if not paths:
            continue  # not a real figure: the ungrounded check owns that case
        kinds = {k for k in (_field_kind(p) for p in paths) if k}
        if len(kinds) != 1:
            continue  # ambiguous source, so no confident verdict
        said = {k for k in _said_kinds(reply, m.start(), m.end()) if k}
        if said and not (said & kinds):
            found.append({
                "figure": raw,
                "is": sorted(kinds)[0],
                "written_as": sorted(said),
                "fields": sorted(paths)[:3],
            })
    return found


@dataclass
class Grounding:
    ok: bool
    ungrounded_numbers: list[str]
    unbacked_verdicts: bool
    rendered: str
    claims_used: list[str]
    mislabelled_figures: list[dict] = field(default_factory=list)
    spelled_figures: list[str] = field(default_factory=list)
    leaked_tool_names: list[str] = field(default_factory=list)

    def corrective_prompt(self) -> str:
        bits = []
        if self.ungrounded_numbers:
            bits.append(
                "these figures are not in any tool result from this turn: "
                + ", ".join(self.ungrounded_numbers)
            )
        if self.leaked_tool_names:
            bits.append(
                "you named " + ", ".join(self.leaked_tool_names)
                + " in the reply; the controller does not know what those are, "
                "so say what you have and have not established in plain words"
            )
        if self.spelled_figures:
            bits.append(
                "you wrote a figure as words (" + ", ".join(self.spelled_figures)
                + "); every figure must be a digit that came from a tool result"
            )
        for f in self.mislabelled_figures:
            bits.append(
                f"{f['figure']} is a {f['is']} figure in this turn's results "
                f"({', '.join(f['fields'])}), but you wrote it as "
                f"{' or '.join(f['written_as'])}"
            )
        if self.unbacked_verdicts:
            bits.append(
                "you stated a legality or cost verdict without a tool result "
                "that establishes it"
            )
        return (
            "Your reply was not sent. "
            + "; ".join(bits)
            + ". Every figure and verdict must come from a tool result. Call the "
            "tool that computes it -- check_assignment for legality, "
            "resolve_cover for cost and ranking, validate for a statement you "
            "want to check -- then answer using {{claim:ID}} placeholders for "
            "the figures, or drop the claim."
        )


def collect(tool_results: list[dict]) -> tuple[dict[str, dict], set[str]]:
    """Every claim by id, and every figure the tools returned this turn.

    The figure set is drawn from the whole `data` payload, not just the claims,
    because a controller may legitimately ask about any field the tool returned
    and the model should be free to quote it.
    """
    claims: dict[str, dict] = {}
    figures: set[str] = set()

    def walk(x):
        if isinstance(x, bool) or x is None:
            return
        if isinstance(x, (int, float)):
            figures.add(_norm_num(x))
            return
        if isinstance(x, str):
            for m in NUMBER_RE.finditer(x):
                figures.add(_norm_num(m.group(1)))
            return
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)

    for r in tool_results:
        for c in r.get("claims", []):
            claims[c["id"]] = c
            walk(c["text"])
            walk(c["value"])
        walk(r.get("data"))
        walk(r.get("summary"))
    return claims, figures


def _norm_num(x) -> str:
    """'1,500,000' and 1500000.0 are the same figure."""
    s = str(x).replace(",", "")
    try:
        f = float(s)
    except ValueError:
        return s
    return str(int(f)) if f == int(f) else f"{f:g}"


_LEAD_IN = re.compile(r"[A-Za-z][\w'’-]*(?:\s+[\w'’-]+)*\s*$")


def _fit(text: str, before: str) -> str:
    """Drop the part of a claim the model has already written.

    A claim is a whole sentence -- "the cheapest legal option is Assign Captain
    C-3310 (reserve callout) at INR 18,500" -- because on its own it has to
    stand up as a statement. The model writes around it, so the placeholder
    lands mid-sentence and the reader gets "the cheapest option is the cheapest
    legal option is Assign Captain...". Substituting only the part that is not
    already on the page fixes the stutter without letting the model edit the
    claim: the words it keeps are its own, and every word it gains is the
    engine's.

    Only a leading run of whole words is ever dropped, so a figure can never be
    removed this way.
    """
    tail = _LEAD_IN.search(before or "")
    if not tail:
        return text
    typed = tail.group(0).strip().lower()
    if not typed:
        return text
    words = text.split()
    low = (before or "").lower()

    def safe(dropped: list[str]) -> bool:
        # Never hide anything the reader has not already been shown: every
        # word removed must already appear in what the model wrote before it.
        return all(w.lower().strip(".,;:()") in low for w in dropped)

    # Longest prefix of the claim the model has already typed, longest first so
    # "the cheapest legal option is" beats "the". The prefix can match either in
    # full, or on its last few words: the model writes "C-2210 is based in DEL
    # and is rated on {{claim}}" where the claim starts "C-2210 is rated on".
    for n in range(len(words) - 1, 0, -1):
        rest = " ".join(words[n:]).strip()
        if not rest:
            continue
        prefix = words[:n]
        for k in range(n, 0, -1):
            if typed.endswith(" ".join(prefix[n - k:]).lower()) and safe(prefix):
                return rest
    return text


def _pick(claim: dict, before: str, after: str = "") -> str:
    """The full sentence, or just the figure, depending on where it lands.

    The model writes its own lead-in. "C-1042's absence breaks three flights on
    day one: {{claim:c1}}" with the full claim substituted reads "...on day
    one: 3 flights uncovered on day 1: DX412, DX413, DX588" -- the same fact
    announced twice, once by the model and once by the engine. After a colon,
    a dash, or a lead-in the model has already finished, the bare figure is
    what belongs there.

    Both forms come from the engine, so this only chooses how much of the
    engine's own wording to use. It can never introduce a figure.
    """
    text = claim.get("text", "")
    short = claim.get("short") or text
    tail = (before or "").rstrip()
    head = (after or "").lstrip()

    # Standing alone as its own sentence, the claim has to say what it is:
    # nothing around it supplies the label.
    starts_sentence = not tail or tail.endswith((".", "!", "?", "\n"))
    ends_sentence = not head or head[0] in ".!?\n" or head[:1].isupper()
    if starts_sentence and ends_sentence:
        return text

    # Otherwise the model is building a sentence around it and has supplied the
    # label itself, so the bare figure is what belongs in the gap.
    if short != text:
        return short

    # No short form: drop whatever the model has already said, if anything.
    return _fit(text, before)


def check(reply: str, tool_results: list[dict]) -> Grounding:
    claims, figures = collect(tool_results)

    used: list[str] = []

    def sub(m):
        cid = m.group(1)
        used.append(cid)
        c = claims.get(cid)
        if not c:
            return f"[unknown claim {cid}]"
        return _pick(c, reply[: m.start()], reply[m.end():])

    rendered = CLAIM_RE.sub(sub, reply)

    # Numbers the model typed itself, outside any placeholder.
    typed = CLAIM_RE.sub("", reply)
    date_spans = [m.span() for m in DATE_NUM_RE.finditer(typed)]
    ungrounded = []
    for m in NUMBER_RE.finditer(typed):
        n = _norm_num(m.group(1))
        if n in ALWAYS_OK or n in figures:
            continue
        if any(a <= m.start() and m.end() <= b for a, b in date_spans):
            continue  # "15 Sep" is a date the controller said, not a figure
        ungrounded.append(m.group(1))

    # A verdict with no tool result behind it at all.
    unbacked = bool(VERDICT_RE.search(typed)) and not tool_results

    # A placeholder that survived substitution is an id this turn does not
    # have. It must not reach the controller as a raw token, so the turn is
    # failed and regenerated rather than printed with {claim:c9} in the prose.
    leftover = bool(LEFTOVER_RE.search(rendered))
    unknown_claim = any(cid not in claims for cid in used) or leftover
    # Checked on the rendered text: a claim's own wording is correct by
    # construction, so only what the model typed itself can be mislabelled.
    wrong_label = mislabelled(typed, tool_results)
    # A magnitude word is a number written as prose, and the digit checks
    # above cannot see it.
    spelled = sorted({m.group(0).lower() for m in MAGNITUDE_RE.finditer(typed)})
    leaked = tool_names_in(rendered)
    return Grounding(
        ok=(not ungrounded and not unbacked and not unknown_claim
            and not wrong_label and not spelled and not leaked),
        ungrounded_numbers=sorted(set(ungrounded)),
        unbacked_verdicts=unbacked,
        rendered=rendered,
        claims_used=used,
        mislabelled_figures=wrong_label,
        spelled_figures=spelled,
        leaked_tool_names=leaked,
    )
