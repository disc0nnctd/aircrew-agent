# Deploying to Cloudflare Workers

What deploys is the workspace: the rules engine, the ten tools, and the page
that draws them. Every panel — the ranked cover options, the duty timeline, the
exclusions with the rule that stopped each candidate — is computed by the same
Python that runs locally, over `/api/tool`.

The chat deploys too, with the key living in the browser rather than at the
edge — see "The chat, at the edge" below.

## Deploy

```bash
python3 worker/build.py                # assemble worker/dist
cd worker
npx wrangler deploy
```

`build.py` copies `aircrew/` in unchanged, writes the nine dataset tables into
`dist/dataset_bundle.py`, and copies `web/index.html` into `dist/public/`.
Rebuild before every deploy — `dist/` is generated and gitignored, and
deploying a stale bundle is the failure that guards against.

First deploy also needs `npx wrangler login`.

## Check it

```bash
curl https://vista-crew.<subdomain>.workers.dev/api/health
curl -X POST https://vista-crew.<subdomain>.workers.dev/api/tool \
  -H 'content-type: application/json' \
  -d '{"name":"resolve_cover","arguments":{"pairing_id":"P-2291","vacated_by":"C-1042"}}'
```

Health should report 150 crew, 147 flights, 39 pairings. The tool call should
recommend C-3310 at INR 18,500, which is the published answer key.

## The chat, at the edge

It runs. The loop is the same generator in both deployments -- `Agent.drive`
yields which completion to make and receives the message back -- so the local
server drives it with `urllib` and the Worker drives it with `fetch`. Tool
dispatch and the claim gate are inside that generator, which means the deployed
engine and the local one cannot drift.

What differs is where the key and the conversation live. A Worker isolate does
not outlive a request, so both travel with the question: the browser holds the
provider config in `sessionStorage` (dropped when the tab closes) and posts the
transcript back each turn. Nothing is stored at the edge, and this deployment
carries no key of its own -- open **settings** in the header and add one.

Verified against the live Worker:

```
POST /api/chat  provider=gemini-3.7-flash
  -> one resolve_cover call, grounded, corrected=false
  "Call out Captain C-3310 from the BLR reserve pool to cover pairing P-2291
   starting 15 Sep. They are legal across all flight, duty, and rest rules and
   provide the lowest-cost cover at INR 18,500 with no departure delay."
```

Providers behave differently here, so the settings panel says which were tried.
Gemini `3.7-flash` is the one to demo on. Sarvam answers fine locally but
returned empty content twice through the Worker, which the loop reports rather
than printing a blank answer.

## What was checked

The bundled dataset produces byte-identical tool output to the file-backed one,
and the full grading run passes against it with no filesystem at all:

```
ENGINE: 36/36 pass (0 fail, 0 TODO), 2 GEN not counted
SCENARIO CHECKS: 19/19
```

`tests/test_agent_loop.py::test_the_engine_runs_with_no_filesystem` keeps the
two paths from drifting.

## Notes on the runtime

- `compatibility_flags = ["python_workers"]` is required; Python Workers run on
  Pyodide.
- `run_worker_first = ["/api/*"]` in `wrangler.toml` matters. Without it the
  static-asset router answers `/api/*` with a 404 before the Worker is reached.
- Cold start parses 747 KB of bundled JSON on top of Pyodide's own startup. The
  parse happens once per isolate, at import, so the first request after an idle
  period is the slow one. If that becomes a problem, `duty_clocks.json` is
  384 KB of it and only some fields are read.
- `aircrew/server.py`, `cli.py`, `scoreboard.py` and `replay.py` are left out of
  the bundle. The Worker replaces the first and does not run the rest.
