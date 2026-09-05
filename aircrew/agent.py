"""The agent loop: an OpenAI-compatible model with tool calling.

Target is `gpt-5.6-luna`. The scaffolding here is deliberately thin -- the
model is capable, and every extra rule is a thing that can be wrong. What is
here has a reason:

- tool summaries steer, because a result that looks answer-shaped is where an
  invented figure comes from;
- the claim gate runs on every final turn, because that is the product's whole
  premise and it must be a mechanism rather than an instruction;
- there is no push-back heuristic for a model that announces a tool instead of
  calling it. That belongs in the "add only if measured" pile.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from . import grounding
from .tools import OPENAI_TOOLS, Tools, dispatch

DEFAULT_MODEL = os.environ.get("AIRCREW_MODEL", "gpt-5.6-luna")
DEFAULT_BASE_URL = os.environ.get("AIRCREW_BASE_URL", "https://api.openai.com/v1")

SYSTEM_PROMPT = """\
You are the Crew Ops Advisor for an airline crew-control desk. The controller \
is working under time pressure and will act on what you say.

WHAT YOU DO AND WHAT THE ENGINE DOES

Deterministic Python computes every figure, every legality verdict and every \
cost. You choose which question to ask it, you resolve what the controller \
meant, and you explain the result. You never calculate.

You may think out loud and propose. Saying "C-2210 might work if we position \
them from DEL" is useful. But a proposal is not an answer: before it reaches \
the controller, call the tool that settles it -- `check_assignment` for \
legality, `resolve_cover` for cost and ranking, `validate` for a statement you \
want checked -- and report what came back, including when it refutes you.

WRITING FIGURES

Every number, cost, count and verdict in your reply must come from a tool \
result in this conversation. Prefer to write it as a placeholder:

    {{claim:c7}}

which is replaced with that claim's validated text. Claim ids are in each tool \
result's `claims` list. If you type a figure directly it is checked against the \
tool results, and a figure that is not there stops your reply from being sent.

If you do not have a figure, say so and call the tool. Never estimate, never \
carry a number over from an earlier answer, and never round one for readability.

WHAT YOU ADD

The engine cannot resolve ambiguity, and that is your job:
- Pin down what the controller meant -- which date "tomorrow" is, which pairing \
"the DXA captain" is on -- and say what you assumed.
- Say which of two verdicts was asked for. `check_assignment` returns both \
"does this breach a rule" and "can we call this person out"; they have \
different answers and the controller asked one of them.
- Call out ties. When several options cost the same, cost has stopped deciding \
and the choice is the controller's, usually on reachability. Say that.
- Read the `missing` list on every result. It tells you what that result does \
not establish. An impact result has no costs in it; do not supply one.
- When a follow-up changes who is available, pass `exclude_crew` and let the \
engine re-rank. Do not read the next row off the previous ranking -- it is a \
different question and usually has a different answer.

STYLE

The workspace beside you already shows the ranked options, the timeline and the \
exclusions. Do not re-list what is on screen. Give the recommendation and the \
one reason that decided it, then stop. Two or three sentences is usually right. \
Carry the plain-English constraint with any rule id you mention: "RULE-REST-04 \
(minimum 12h rest)", never the bare id.
"""


@dataclass
class Turn:
    reply: str
    tool_calls: list[dict] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)
    grounding: grounding.Grounding | None = None
    corrected: bool = False


class Agent:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str | None = None,
        tools: Tools | None = None,
        max_rounds: int = 8,
    ):
        self.model = model
        self.tools = tools or Tools()
        self.max_rounds = max_rounds
        self.messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        self._client = None
        self._base_url = base_url
        self._api_key = api_key or os.environ.get("AIRCREW_API_KEY") or os.environ.get(
            "OPENAI_API_KEY"
        )

    # ------------------------------------------------------------------
    @property
    def client(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover
                raise SystemExit(
                    "The agent needs the `openai` package: pip install openai\n"
                    "The engine and the workspace both run without it -- see "
                    "`python -m aircrew.cli` and `python -m aircrew.server`."
                ) from exc
            self._client = OpenAI(base_url=self._base_url, api_key=self._api_key)
        return self._client

    # ------------------------------------------------------------------
    def ask(self, question: str) -> Turn:
        """One controller question, through as many tool rounds as it needs."""
        self.messages.append({"role": "user", "content": question})
        results: list[dict] = []
        calls: list[dict] = []

        for _ in range(self.max_rounds):
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=self.messages,
                tools=OPENAI_TOOLS,
                tool_choice="auto",
            )
            msg = resp.choices[0].message
            self.messages.append(msg.model_dump(exclude_none=True))

            if not msg.tool_calls:
                return self._finish(msg.content or "", calls, results)

            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = dispatch(self.tools, name, args)
                calls.append({"name": name, "arguments": args})
                results.append(result)
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, default=str),
                    }
                )

        return self._finish(
            "I ran out of tool rounds before reaching an answer. The workspace "
            "shows what was computed so far.",
            calls,
            results,
        )

    # ------------------------------------------------------------------
    def _finish(self, reply: str, calls: list[dict], results: list[dict]) -> Turn:
        g = grounding.check(reply, results)
        if g.ok:
            return Turn(g.rendered, calls, results, g)

        # One corrective turn. If the model cannot ground the figure the second
        # time, the honest thing is to say so rather than print it anyway.
        self.messages.append({"role": "user", "content": g.corrective_prompt()})
        resp = self.client.chat.completions.create(
            model=self.model, messages=self.messages, tools=OPENAI_TOOLS, tool_choice="auto"
        )
        msg = resp.choices[0].message
        self.messages.append(msg.model_dump(exclude_none=True))

        if msg.tool_calls:
            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = dispatch(self.tools, name, args)
                calls.append({"name": name, "arguments": args})
                results.append(result)
                self.messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, default=str)}
                )
            resp = self.client.chat.completions.create(
                model=self.model, messages=self.messages, tools=OPENAI_TOOLS, tool_choice="none"
            )
            msg = resp.choices[0].message
            self.messages.append(msg.model_dump(exclude_none=True))

        g2 = grounding.check(msg.content or "", results)
        if g2.ok:
            return Turn(g2.rendered, calls, results, g2, corrected=True)
        return Turn(
            "I could not ground every figure in that answer against a computed "
            "result, so I am not stating it. The workspace shows what the engine "
            "did compute.",
            calls,
            results,
            g2,
            corrected=True,
        )
