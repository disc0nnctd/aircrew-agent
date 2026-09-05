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
from dataclasses import dataclass

CLAIM_RE = re.compile(r"\{\{claim:(c\d+)\}\}")

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

# Figures that are part of the vocabulary rather than a computed result.
ALWAYS_OK = {
    "0", "1", "2", "3", "4", "5", "6", "7",       # small counts, rule ordinals
    "12", "13", "28", "30", "60", "100",           # the rule limits themselves
    "01", "02", "03", "04", "05", "06", "07",      # rule id suffixes
}


@dataclass
class Grounding:
    ok: bool
    ungrounded_numbers: list[str]
    unbacked_verdicts: bool
    rendered: str
    claims_used: list[str]

    def corrective_prompt(self) -> str:
        bits = []
        if self.ungrounded_numbers:
            bits.append(
                "these figures are not in any tool result from this turn: "
                + ", ".join(self.ungrounded_numbers)
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
        for k in range(n, 1, -1):
            if typed.endswith(" ".join(prefix[n - k:]).lower()) and safe(prefix):
                return rest
    return text


def check(reply: str, tool_results: list[dict]) -> Grounding:
    claims, figures = collect(tool_results)

    used: list[str] = []

    def sub(m):
        cid = m.group(1)
        used.append(cid)
        c = claims.get(cid)
        if not c:
            return f"[unknown claim {cid}]"
        return _fit(c["text"], reply[: m.start()])

    rendered = CLAIM_RE.sub(sub, reply)

    # Numbers the model typed itself, outside any placeholder.
    typed = CLAIM_RE.sub("", reply)
    ungrounded = []
    for m in NUMBER_RE.finditer(typed):
        n = _norm_num(m.group(1))
        if n in ALWAYS_OK or n in figures:
            continue
        ungrounded.append(m.group(1))

    # A verdict with no tool result behind it at all.
    unbacked = bool(VERDICT_RE.search(typed)) and not tool_results

    unknown_claim = any(cid not in claims for cid in used)
    return Grounding(
        ok=not ungrounded and not unbacked and not unknown_claim,
        ungrounded_numbers=sorted(set(ungrounded)),
        unbacked_verdicts=unbacked,
        rendered=rendered,
        claims_used=used,
    )
