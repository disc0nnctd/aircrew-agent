"""The agent loop and the claim gate, exercised without a network call.

The engine's correctness is covered by `python -m aircrew.scoreboard`, which
replays the 38 questions and 6 scenarios against the answer keys. What is left
to test is the boundary itself: that a figure the model invents does not reach
the controller.

Run with: python -m tests.test_agent_loop   (or pytest)
"""

from __future__ import annotations

import json
import types

from aircrew import grounding
from aircrew.agent import Agent
from aircrew.tools import Tools, dispatch


# --- a stub OpenAI client -------------------------------------------------
class _Msg:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls

    def model_dump(self, **kw):
        return {"role": "assistant", "content": self.content}


class _Call:
    def __init__(self, name, args):
        self.id = "call_1"
        self.function = types.SimpleNamespace(name=name, arguments=json.dumps(args))


class _Stub:
    """Replays a fixed script of assistant messages."""

    def __init__(self, script):
        self.script = list(script)
        self.chat = types.SimpleNamespace(completions=self)

    def create(self, **kw):
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=self.script.pop(0))]
        )


def _agent(script, tools):
    a = Agent(tools=tools)
    a._client = _Stub(script)
    return a


# --- the gate itself ------------------------------------------------------
def test_placeholder_is_substituted_with_validated_text():
    t = Tools()
    env = dispatch(t, "resolve_cover", {"pairing_id": "P-2291", "vacated_by": "C-1042", "limit": 3})
    cid = env["claims"][2]["id"]
    g = grounding.check(f"Call out {{{{claim:{cid}}}}}.", [env])
    assert g.ok
    assert "C-3310" in g.rendered and "18,500" in g.rendered


def test_invented_figure_is_caught():
    t = Tools()
    env = dispatch(t, "trace_disruption", {"crew_id": "C-1042", "pairing_id": "P-2291"})
    g = grounding.check("Cancelling would cost INR 1,250,000.", [env])
    assert not g.ok
    assert "1,250,000" in g.ungrounded_numbers


def test_figure_present_in_tool_data_is_allowed():
    """The model may quote any field the tool returned, not only the claims."""
    t = Tools()
    env = dispatch(t, "trace_disruption", {"crew_id": "C-1042", "pairing_id": "P-2291"})
    g = grounding.check("486 passengers are booked on day 1.", [env])
    assert g.ok


def test_rule_limits_are_not_flagged():
    g = grounding.check("RULE-DUTY-02 caps duty at 60 hours in any 7 days.", [])
    assert g.ok


# --- the loop -------------------------------------------------------------
def test_loop_grounds_a_good_answer():
    t = Tools()
    env = dispatch(t, "resolve_cover", {"pairing_id": "P-2291", "vacated_by": "C-1042", "limit": 3})
    cid = env["claims"][2]["id"]
    # the ids the live run mints will differ; ask for the third claim by shape
    a = _agent(
        [
            _Msg(tool_calls=[_Call("resolve_cover",
                                   {"pairing_id": "P-2291", "vacated_by": "C-1042", "limit": 3})]),
            _Msg(content="Call out C-3310, the cheapest legal cover."),
        ],
        t,
    )
    turn = a.ask("C-1042 is sick for P-2291, what should I do?")
    assert turn.grounding.ok
    assert turn.tool_calls[0]["name"] == "resolve_cover"


def test_loop_sends_one_corrective_turn_and_recovers():
    a = _agent(
        [
            _Msg(tool_calls=[_Call("trace_disruption",
                                   {"crew_id": "C-1042", "pairing_id": "P-2291"})]),
            _Msg(content="Three flights uncovered; cancelling costs INR 1,250,000."),
            _Msg(content="Three flights are uncovered on day 1. I do not have a "
                         "cancellation cost -- that needs resolve_cover."),
        ],
        Tools(),
    )
    turn = a.ask("What breaks if C-1042 goes sick?")
    assert turn.corrected
    assert turn.grounding.ok
    assert "1,250,000" not in turn.reply


def test_loop_refuses_rather_than_printing_an_ungrounded_figure():
    """If the model cannot ground the figure on the second try, the answer is
    withheld. Printing it anyway is the failure the product exists to prevent."""
    a = _agent(
        [
            _Msg(tool_calls=[_Call("trace_disruption",
                                   {"crew_id": "C-1042", "pairing_id": "P-2291"})]),
            _Msg(content="Cancelling costs INR 1,250,000."),
            _Msg(content="It is still INR 1,250,000."),
        ],
        Tools(),
    )
    turn = a.ask("What does cancelling cost?")
    assert "1,250,000" not in turn.reply
    assert "could not ground" in turn.reply.lower()


# --- the tools ------------------------------------------------------------
def test_no_tool_accepts_a_cost_or_a_verdict():
    """There is nowhere for a remembered figure to enter the engine."""
    from aircrew.tools import SCHEMAS

    banned = {"cost", "cost_inr", "price", "total", "legal", "illegal", "verdict",
              "breach", "passengers", "count"}
    for s in SCHEMAS:
        for name in s["parameters"]["properties"]:
            assert name not in banned, f"{s['name']} takes {name}"


def test_no_tool_can_skip_a_rule():
    from aircrew.tools import SCHEMAS

    for s in SCHEMAS:
        for name in s["parameters"]["properties"]:
            assert "skip" not in name and "ignore" not in name and "only" not in name


def test_exclude_crew_re_ranks_rather_than_reading_the_next_row():
    t = Tools()
    first = dispatch(t, "resolve_cover", {"pairing_id": "P-2291", "vacated_by": "C-1042"})
    top = first["data"]["recommended"]["crew_id"]
    again = dispatch(t, "resolve_cover",
                     {"pairing_id": "P-2291", "vacated_by": "C-1042", "exclude_crew": [top]})
    assert again["data"]["recommended"]["crew_id"] != top
    # and it is a real re-simulation: every option was legality-checked again
    assert all(o["rules_checked"] for o in again["data"]["options"] if o["crew_id"])


if __name__ == "__main__":
    import sys

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"ok   {fn.__name__}")
        except Exception as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
