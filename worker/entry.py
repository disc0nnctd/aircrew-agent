"""The Worker's routing layer -- the edge equivalent of aircrew/server.py.

Everything below the routing is the same code that runs locally: the same
tools, the same engine, the same claim gate. That is deliberate. A deployment
that reimplements the engine in another language is a second engine to keep
correct, and the point of this build is that there is exactly one.

Two things differ at the edge, and both are named rather than papered over:

  * There is no filesystem, so the dataset arrives as a bundled module.
  * The chat does not run here at all. The agent loop is synchronous and a
    Worker cannot block on an outbound request, so /api/chat says so plainly
    instead of failing on the first click. What deploys is the half that
    proves the claim: every panel in the workspace is computed by this engine
    and needs no model.
"""

import json

from js import Object, Response
from js import fetch as js_fetch
from pyodide.ffi import to_js


def _to_js(obj):
    return to_js(obj, dict_converter=Object.fromEntries)

import dataset_bundle
from aircrew import data as _data

# Before anything constructs a Dataset. Tools() does, at import time.
_data.BUNDLED = dataset_bundle.tables()

from aircrew.tools import SCHEMAS, Tools, dispatch  # noqa: E402  (after BUNDLED)

_tools = Tools()


async def _complete(base, key, model, messages, tool_choice):
    """One completion, over the platform's fetch.

    This is the only thing the Worker does differently from the local server:
    `Agent.drive` is the same generator in both, so the tool rounds, the claim
    substitution and the gate cannot drift between the two deployments.
    """
    from aircrew.tools import OPENAI_TOOLS

    body = json.dumps({
        "model": model, "messages": messages,
        "tools": OPENAI_TOOLS, "tool_choice": tool_choice,
    })
    response = await js_fetch(
        f"{base}/chat/completions",
        _to_js({
            "method": "POST",
            "headers": {"content-type": "application/json",
                        "authorization": f"Bearer {key}"},
            "body": body,
        }),
    )
    text = await response.text()
    if response.status >= 400:
        raise RuntimeError(f"HTTP {response.status} from the model endpoint: "
                           f"{text[:300]}")
    data = json.loads(text)
    choices = data.get("choices")
    if not choices:
        raise RuntimeError(f"no choices in the model response: {text[:200]}")
    msg = {k: v for k, v in (choices[0].get("message") or {}).items()
           if v is not None}
    msg.setdefault("content", "")
    return msg


def _json(obj, status=200):
    return Response.new(
        json.dumps(obj, default=str),
        _to_js({"status": status,
                "headers": {"content-type": "application/json; charset=utf-8"}}),
    )




async def on_fetch(request, env):
    url = request.url
    path = url.split("://", 1)[-1].split("/", 1)[-1].split("?")[0]
    path = "/" + path if not path.startswith("/") else path

    if request.method == "GET":
        if path in ("/", "/index.html"):
            return await env.ASSETS.fetch(request)

        if path == "/api/health":
            return _json({
                "engine": "ok",
                "crew": len(_tools.ds.crew),
                "flights": len(_tools.ds.flights),
                "pairings": len(_tools.ds.pairings),
                "model": False,
                "model_name": None,
                "snapshot": _tools.ds.snapshot_utc.strftime("%Y-%m-%d %H:%MZ"),
                "model_error":
                    "no model on this deployment; every figure in the "
                    "workspace is computed by the engine and needs none",
            })

        if path == "/api/tools":
            return _json(SCHEMAS)

        if path == "/api/prompt":
            from aircrew.agent import SYSTEM_PROMPT

            dates = _tools.ds.schedule_dates
            return _json({
                "model": None,
                "system_prompt": SYSTEM_PROMPT.format(
                    schedule_from=dates[0],
                    schedule_to=dates[-1],
                    snapshot=_tools.ds.snapshot_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    year=dates[0][:4],
                ),
            })

        return await env.ASSETS.fetch(request)

    if request.method != "POST":
        return _json({"error": "not found"}, 404)

    try:
        payload = json.loads(await request.text() or "{}")
    except Exception:
        return _json({"error": "bad JSON body"}, 400)

    if path == "/api/tool":
        name = payload.get("name")
        if not name:
            return _json({"error": "name required"}, 400)
        try:
            return _json(dispatch(_tools, name, payload.get("arguments") or {}))
        except Exception as exc:  # a tool must never take the isolate down
            return _json({"error": "tool raised", "detail": str(exc)}, 500)

    if path == "/api/reset":
        # Nothing to reset: the conversation lives in the browser here.
        return _json({"ok": True})

    if path == "/api/chat":
        provider = payload.get("provider") or {}
        base = (provider.get("base_url") or "").strip().rstrip("/")
        key = (provider.get("api_key") or "").strip()
        model = (provider.get("model") or "").strip()
        if not (base and key and model):
            return _json({
                "error": "no model configured",
                "detail": "This deployment holds no API key of its own.",
                "hint": "Open settings and add a provider -- Sarvam, Gemini, "
                        "Cloudflare Workers AI or any OpenAI-compatible "
                        "endpoint. The key stays in this browser and is sent "
                        "with each question; it is never stored at the edge. "
                        "The workspace is computed by the engine and works "
                        "without a model at all.",
            }, 503)

        message = (payload.get("message") or "").strip()
        if not message:
            return _json({"error": "message required"}, 400)

        from aircrew.agent import Agent

        agent = Agent(model=model, base_url=base, api_key=key, tools=_tools)
        # A Worker isolate does not outlive the request, so the browser is the
        # only thing that remembers the conversation. It sends it back, and
        # this replays it: the same history the local server keeps in memory.
        for past in payload.get("history") or []:
            if past.get("role") in ("user", "assistant") and past.get("content"):
                agent.messages.append({"role": past["role"],
                                       "content": past["content"]})

        loop = agent.drive(message)
        try:
            choice = loop.send(None)
            while True:
                try:
                    msg = await _complete(base, key, model, agent.messages, choice)
                except Exception as exc:
                    loop.close()
                    return _json({"error": "model call failed",
                                  "detail": str(exc)[:300]}, 502)
                choice = loop.send(msg)
        except StopIteration as done:
            turn = done.value

        return _json({
            "reply": turn.reply,
            "tool_calls": turn.tool_calls,
            "tool_results": turn.tool_results,
            "corrected": turn.corrected,
            "grounded": turn.grounding.ok if turn.grounding else None,
            "ungrounded_numbers":
                turn.grounding.ungrounded_numbers if turn.grounding else [],
        })

    return _json({"error": "not found"}, 404)
