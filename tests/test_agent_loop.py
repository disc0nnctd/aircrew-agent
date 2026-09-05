"""The agent loop and the claim gate, exercised without a network call.

The engine's correctness is covered by `python -m aircrew.scoreboard`, which
replays the 38 questions and 6 scenarios against the answer keys. What is left
to test is the boundary itself: that a figure the model invents does not reach
the controller.

Run with: python -m tests.test_agent_loop   (or pytest)
"""

from __future__ import annotations

import json

from aircrew import grounding
from aircrew.agent import Agent
from aircrew.tools import Tools, dispatch


# --- a stub model endpoint ------------------------------------------------
# The seam is `_post`, the one HTTP call the agent makes. Faking at that level
# means the test still exercises the real response parsing, the real history
# handling and the real tool dispatch, and only the network is pretend.
def _Msg(content=None, tool_calls=None):
    """One assistant message, in the wire shape a gateway returns."""
    msg: dict = {"role": "assistant"}
    if content is not None:
        msg["content"] = content
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def _Call(name, args, call_id="call_1"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def _agent(script, tools):
    a = Agent(tools=tools, api_key="test-key")
    replies = [{"choices": [{"message": m}]} for m in script]

    def _post(path, payload):
        assert path == "/chat/completions", path
        assert payload["messages"][0]["role"] == "system"
        return replies.pop(0)

    a._post = _post
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


def test_a_claim_does_not_repeat_what_the_model_already_wrote():
    """Claims are whole sentences so they stand up alone, but the model writes
    around them, so the placeholder lands mid-sentence: "the cheapest option is
    the cheapest legal option is Assign...". Only words already on the page are
    dropped, so no figure can be lost this way."""
    from aircrew.grounding import _fit

    assert _fit("the cheapest joint plan costs INR 42,500",
                "The cheapest joint plan costs ") == "INR 42,500"
    assert _fit("C-2210 is rated on A320",
                "C-2210 is based in DEL and is rated on ") == "A320"
    # nothing typed yet: the claim stands whole
    assert _fit("5 candidates are legal", "") == "5 candidates are legal"
    # an unrelated lead-in must not eat the claim
    assert _fit("5 candidates are legal", "Here is the answer. ") == "5 candidates are legal"
    # and the figure survives every path
    for before in ("The cheapest joint plan costs ", "", "Cost: "):
        assert "42,500" in _fit("the cheapest joint plan costs INR 42,500", before)


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


def test_the_loop_runs_without_a_third_party_package():
    """The engine, the CLI and the workspace have no dependencies, and the
    chat pane must not quietly add one. A missing package used to raise
    SystemExit, which is a BaseException, so the server's `except Exception`
    let it kill the request thread and the browser saw a closed socket."""
    import sys

    t = Tools()
    a = _agent([_Msg(content="RULE-DUTY-02 caps duty at 60 hours in any 7 days.")], t)
    a.ask("what is the duty limit?")
    assert "openai" not in sys.modules


def test_a_tool_that_raises_returns_a_result_the_model_can_act_on():
    """`dispatch` is called unguarded inside the loop, so anything it raises
    kills the whole turn. A missing field has to come back as a readable
    envelope instead, or one blank argument costs the controller the answer."""
    t = Tools()
    for name, args in (
        ("validate", {"claim_kind": "crew_qualified", "crew_id": "C-3305"}),
        ("validate", {"claim_kind": "assignment_legal", "crew_id": "C-3305"}),
        ("validate", {"claim_kind": "cheapest_option"}),
        ("duty_timeline", {"crew_id": "C-3310", "pairing_id": "P-9999"}),
    ):
        env = dispatch(t, name, args)
        assert "error" in env["data"], f"{name} {args} should report, not raise"
        assert name in env["summary"]
        assert not env.get("claims"), "a failed call must state nothing"


def test_a_blank_optional_argument_does_not_become_a_missing_one():
    """The model fills optionals with "", `clean_args` strips them, and the
    tool then indexes a key that is no longer there. Both halves must hold."""
    t = Tools()
    blank = dispatch(t, "validate", {"claim_kind": "crew_qualified",
                                     "crew_id": "C-3305", "aircraft_type": ""})
    assert "error" in blank["data"]
    good = dispatch(t, "validate", {"claim_kind": "crew_qualified",
                                    "crew_id": "C-3305", "aircraft_type": "A320"})
    assert "CONFIRMED" in good["summary"]


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
