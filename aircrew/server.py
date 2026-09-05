"""Standard-library HTTP server. No build step, no dependencies.

Two endpoints matter:

  POST /api/tool   run one tool and return its envelope
  POST /api/chat   one agent turn: reply plus the tool results it produced

The workspace draws only from `/api/tool`, and every panel drill-down calls it
directly. That is why the right-hand pane keeps working with the language model
switched off, which is also the demo's safety net.
"""

from __future__ import annotations

import argparse
import copy
import json
import urllib.error
import urllib.request
import os
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .tools import Tools, dispatch

WEB = Path(__file__).resolve().parent.parent / "web"

_tools = Tools()
_agent = None
_agent_error: str | None = None

# One conversation per desk, not one per process. The agent holds its own
# history, which is what makes "C-3310 is also sick, now what?" work without a
# pairing id -- and with a single shared agent it also made a second browser
# inherit the first one's sick call, and made either browser's Clear wipe the
# other. Two judges at one demo is enough to hit that.
_desks: dict[str, object] = {}
_desks_lock = threading.Lock()
_desk_locks: dict[str, threading.Lock] = {}


def desk_of(handler) -> str:
    """Which conversation this request belongs to.

    The browser sends a per-tab id. Anything without one -- curl, the CLI,
    a health probe -- shares a single default desk, which is the old
    behaviour and is correct for a single operator.
    """
    said = handler.headers.get("X-Desk-Session")
    if not said:
        cookie = handler.headers.get("Cookie") or ""
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "session" and value:
                said = value
                break
    return (said or "default")[:64]


# Providers this has actually been run against, with what was observed. The
# UI offers these as starting points; any OpenAI-compatible endpoint works,
# because the loop only ever asks for one completion with tools.
PROVIDERS = {
    "sarvam": {
        "label": "Sarvam AI",
        "base_url": "https://api.sarvam.ai/v1",
        "models": ["sarvam-105b", "sarvam-105b-conversations"],
        "note": "Free tier. Tool calling works; answers in about 6 seconds.",
    },
    "gemini": {
        "label": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "models": ["gemini-3.7-flash", "gemini-3.5-flash", "gemini-3.8-flash",
                   "gemini-2.5-flash"],
        "note": "Free tier. 3.7-flash is the fastest tested; 3.8-flash hits the "
                "free request quota quickly.",
    },
    "cloudflare": {
        "label": "Cloudflare Workers AI",
        "base_url": "https://api.cloudflare.com/client/v4/accounts/"
                    "ACCOUNT_ID/ai/v1",
        "models": ["@cf/meta/llama-3.3-70b-instruct-fp8-fast",
                   "@cf/mistralai/mistral-small-3.1-24b-instruct",
                   "@cf/qwen/qwen3-30b-a3b-fp8"],
        "note": "Put your account id in the URL. Avoid @cf/openai/gpt-oss-*: "
                "they answer into a reasoning field and return no content.",
    },
    "nvidia": {
        "label": "NVIDIA NIM",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "models": ["meta/llama-3.3-70b-instruct", "qwen/qwen3-next-80b-a3b-instruct"],
        "note": "Free developer tier. Untested here -- bring a key and try it.",
    },
    "openai": {
        "label": "OpenAI-compatible (other)",
        "base_url": "https://api.openai.com/v1",
        "models": [],
        "note": "Any endpoint that serves /chat/completions with tool calling.",
    },
}

# What each desk chose in the settings panel. Keys live here and in nothing
# else: never written to disk, never logged, never returned by the API.
_desk_config: dict[str, dict] = {}


def configure_desk(desk: str, base_url: str, model: str, api_key: str | None):
    """Point one desk at a different provider, keeping its conversation.

    The history is what makes a follow-up work, and switching model is not a
    reason to lose it -- the messages are provider-agnostic.
    """
    from .agent import Agent

    with _desks_lock:
        previous = _desks.get(desk)
        keep = list(previous.messages) if previous is not None else None
        old = _desk_config.get(desk) or {}
        key = api_key or old.get("api_key")
        agent = Agent(model=model, base_url=base_url, api_key=key, tools=_tools)
        if keep:
            agent.messages = keep
        _desk_config[desk] = {"base_url": base_url, "model": model, "api_key": key}
        _desks[desk] = agent
        _desk_locks.setdefault(desk, threading.Lock())
    return agent


def desk_view(desk: str) -> dict:
    """What the settings panel is allowed to see. Never the key itself."""
    cfg = _desk_config.get(desk)
    if cfg:
        return {"base_url": cfg["base_url"], "model": cfg["model"],
                "key_set": bool(cfg["api_key"]), "source": "settings"}
    base = get_agent()
    return {
        "base_url": getattr(base, "_base_url", None),
        "model": getattr(base, "model", None),
        "key_set": base is not None,
        "source": "environment" if base is not None else "none",
    }


def desk_agent(desk: str):
    """The agent for one desk, built from the same configuration as the rest.

    A shallow copy shares the tools, the model and the transport; only the
    message history is per desk, and that is the only mutable state a
    conversation has. A desk that has chosen its own provider gets its own
    agent instead.
    """
    with _desks_lock:
        agent = _desks.get(desk)
        if agent is not None:
            return agent
    base = get_agent()
    if base is None:
        return None
    with _desks_lock:
        agent = _desks.get(desk)
        if agent is None:
            agent = copy.copy(base)
            agent.messages = list(base.messages[:1])
            _desks[desk] = agent
            _desk_locks[desk] = threading.Lock()
        return agent


def desk_lock(desk: str) -> threading.Lock:
    """Serialise one desk's turns.

    The server is threaded, and two overlapping questions on the same desk
    would interleave appends into one message list -- producing a history that
    never happened, in the component that is hardest to check.
    """
    with _desks_lock:
        return _desk_locks.setdefault(desk, threading.Lock())


def get_agent():
    """Built lazily: the workspace must run whether or not a model is reachable.

    A missing API key is reported as "no model", not as a working one -- the UI
    switches to engine-only mode on this answer, and claiming a model that is
    not there would put the demo one click from a stack trace.
    """
    global _agent, _agent_error
    if _agent is None and _agent_error is None:
        if not (os.environ.get("AIRCREW_API_KEY") or os.environ.get("OPENAI_API_KEY")):
            _agent_error = "no API key: set AIRCREW_API_KEY (or OPENAI_API_KEY)"
            return None
        try:
            from .agent import Agent

            _agent = Agent(tools=_tools)
        except Exception as exc:
            _agent_error = str(exc)
    return _agent


class Handler(BaseHTTPRequestHandler):
    server_version = "CrewOpsAdvisor"

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))

    # ------------------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, default=str).encode("utf-8"), "application/json; charset=utf-8")

    # ------------------------------------------------------------------
    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            f = WEB / "index.html"
            if not f.exists():
                return self._json({"error": "web/index.html missing"}, 404)
            return self._send(200, f.read_bytes(), "text/html; charset=utf-8")
        if path == "/api/health":
            return self._json(
                {
                    "engine": "ok",
                    "crew": len(_tools.ds.crew),
                    "flights": len(_tools.ds.flights),
                    "pairings": len(_tools.ds.pairings),
                    "model": bool(get_agent()),
                    "model_name": getattr(get_agent(), "model", None),
                    "snapshot": _tools.ds.snapshot_utc.strftime("%Y-%m-%d %H:%MZ"),
                    "model_error": _agent_error,
                }
            )
        # The boundary, served rather than described. Both of these are the
        # real objects the loop uses, not a copy written for the screen: if
        # the tool surface changes, this changes with it.
        if path == "/api/tools":
            from .tools import SCHEMAS

            return self._json(SCHEMAS)

        if path == "/api/provider":
            return self._json({
                "current": desk_view(desk_of(self)),
                "providers": PROVIDERS,
            })
        if path == "/api/prompt":
            from .agent import DEFAULT_MODEL, SYSTEM_PROMPT

            dates = _tools.ds.schedule_dates
            return self._json(
                {
                    "model": DEFAULT_MODEL if get_agent() else None,
                    "system_prompt": SYSTEM_PROMPT.format(
                        schedule_from=dates[0],
                        schedule_to=dates[-1],
                        snapshot=_tools.ds.snapshot_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        year=dates[0][:4],
                    ),
                }
            )
        return self._json({"error": "not found"}, 404)

    # ------------------------------------------------------------------
    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._json({"error": "bad JSON body"}, 400)

        if self.path == "/api/tool":
            name = payload.get("name")
            args = payload.get("arguments") or {}
            if not name:
                return self._json({"error": "name required"}, 400)
            try:
                return self._json(dispatch(_tools, name, args))
            except Exception:
                traceback.print_exc()
                return self._json({"error": "tool raised"}, 500)

        if self.path == "/api/provider":
            base_url = (payload.get("base_url") or "").strip().rstrip("/")
            model = (payload.get("model") or "").strip()
            api_key = (payload.get("api_key") or "").strip() or None
            if not base_url or not model:
                return self._json({"error": "base_url and model are required"}, 400)
            desk = desk_of(self)
            try:
                configure_desk(desk, base_url, model, api_key)
            except Exception as exc:
                return self._json({"error": "could not configure",
                                   "detail": str(exc)}, 400)
            return self._json({"ok": True, "current": desk_view(desk)})

        if self.path == "/api/models":
            # The provider's own list, fetched with the key the controller
            # just typed. Proxied rather than fetched from the browser so the
            # key never has to survive a redirect or a CORS preflight.
            base_url = (payload.get("base_url") or "").strip().rstrip("/")
            api_key = (payload.get("api_key") or "").strip()
            if not base_url:
                return self._json({"error": "base_url required"}, 400)
            if not api_key:
                cfg = _desk_config.get(desk_of(self)) or {}
                api_key = cfg.get("api_key") or ""
            req = urllib.request.Request(
                f"{base_url}/models",
                headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            )
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                return self._json({"error": f"HTTP {exc.code} from the provider"}, 200)
            except Exception as exc:
                return self._json({"error": str(exc)[:200]}, 200)
            listed = body.get("data") if isinstance(body, dict) else None
            names = sorted(
                (m.get("id") or "").split("/")[-1] if str(m.get("id", "")).startswith("models/")
                else m.get("id") or ""
                for m in (listed or []) if isinstance(m, dict)
            )
            return self._json({"models": [n for n in names if n]})

        if self.path == "/api/reset":
            # The browser's Clear reloads the page, which empties the log the
            # controller sees. The agent's own history lived on, so the next
            # question still carried the last one -- fine for a follow-up,
            # wrong for "start again" and wrong between two judges at a demo.
            desk = desk_of(self)
            agent = desk_agent(desk)
            if agent is not None:
                with desk_lock(desk):
                    agent.reset()
            return self._json({"ok": True, "desk": desk})

        if self.path == "/api/chat":
            desk = desk_of(self)
            agent = desk_agent(desk)
            if agent is None:
                return self._json(
                    {
                        "error": "no model configured",
                        "detail": _agent_error,
                        "hint": "Set AIRCREW_API_KEY (and AIRCREW_BASE_URL / "
                        "AIRCREW_MODEL). The workspace works without it.",
                    },
                    503,
                )
            msg = (payload.get("message") or "").strip()
            if not msg:
                return self._json({"error": "message required"}, 400)
            try:
                with desk_lock(desk):
                    turn = agent.ask(msg)
            except Exception as exc:
                traceback.print_exc()
                return self._json({"error": "model call failed", "detail": str(exc)}, 502)
            return self._json(
                {
                    "reply": turn.reply,
                    "tool_calls": turn.tool_calls,
                    "tool_results": turn.tool_results,
                    "corrected": turn.corrected,
                    "grounded": turn.grounding.ok if turn.grounding else None,
                    "ungrounded_numbers": turn.grounding.ungrounded_numbers if turn.grounding else [],
                }
            )

        return self._json({"error": "not found"}, 404)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Crew Ops Advisor web server")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"Crew Ops Advisor on http://{a.host}:{a.port}")
    print(f"  engine: {len(_tools.ds.crew)} crew, {len(_tools.ds.flights)} flights")
    print("  workspace runs with or without a model configured")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
