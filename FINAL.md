# FINAL — ship it: new repo, then Cloudflare

Two jobs. The first is mechanical. The second has one real obstacle, named
below, and everything else follows from how you resolve it.

Target repo: **https://github.com/disc0nnctd/vista-crew**
Source: this working tree, branch `rebuild`.

---

## Part 1 — Push to vista-crew

### 1.1 Check what would go out

Only the current tree ships (see 1.2), so scan the tree, not the log:

```bash
git ls-files | grep -Ei "\.env|private/|Keys/"        # must print nothing
git grep -nEi "sk-[A-Za-z0-9_-]{16,}" -- . ':!*.md'   # must print nothing
```

Nothing secret has ever been committed here either, and that is worth
confirming once before the orphan commit is made:

```bash
git log --all -S "sk-clb" --oneline        # must print nothing
```

`private/`, `**/Keys/`, `.env*` and `generate.py` are gitignored. `generate.py`
is organiser-internal — it reveals how the answer keys were derived, and this
build exists to recover the rules from the data instead. It must not appear in
the new repo.

### 1.2 Create the remote and push, without the history

The new repo starts at one commit. This history is a hackathon's worth of false
starts and a branch split that only ever mattered here, and it is history nobody
has audited line by line for what got staged and reverted along the way. A clean
tree is both tidier and safer.

Do it with an orphan branch, not by deleting `.git`, so this repo keeps its own
history intact:

```bash
git checkout rebuild
git checkout --orphan release          # same files, no parent commit
git add -A
git commit -m "Crew Ops Advisor: a deterministic crew-control engine with a model-driven desk"

gh repo create disc0nnctd/vista-crew --public   --description "Crew Ops Advisor - deterministic rules engine, model-driven desk assistant"
git remote add vista https://github.com/disc0nnctd/vista-crew.git
git push vista release:main

git checkout rebuild                   # back to the working branch
git branch -D release
```

Then prove the new repo has no ancestry, from a fresh clone rather than from
here:

```bash
git clone https://github.com/disc0nnctd/vista-crew /tmp/check
git -C /tmp/check log --oneline        # exactly one line
```

Squashing and force-pushing is not the same thing: it rewrites the remote ref,
but a fork or a cached ref can still reach the old objects. An orphan commit has
no parents to reach.

### 1.3 What the new repo must have that this one does not

- `README.md` rewritten for someone arriving cold: what it is, the one-command
  run, and the boundary claim stated in the first paragraph.
- `LICENSE` — pick one before the repo is public.
- `screenshots/` — the 38-question sweep, so the repo shows the product without
  running it.
- No `TASK.md`, no `REVIEW_ASTRA.md`, no scratch logs.

Verify the clone works from nothing:

```bash
git clone https://github.com/disc0nnctd/vista-crew && cd vista-crew
python -m aircrew.server --port 8765     # workspace must work with no API key
```

That has to pass. The workspace running without a model is the demo's safety
net and the README promises it.

---

## Part 2 — Deploy on Cloudflare Workers

### 2.1 The obstacle, first

The model endpoint currently in use is `http://100.96.201.27:2455/v1`. That is a
Tailscale address on a private tailnet. **A Cloudflare Worker cannot reach it.**
No amount of Worker configuration fixes this; it is not a routing problem, the
host does not exist from Cloudflare's side.

So before anything is deployed, decide which of these is true:

- **(a) A publicly reachable OpenAI-compatible endpoint exists.** Then set its
  URL and key as Worker secrets and the deployment is straightforward.
- **(b) It does not.** Then the Worker cannot host `/api/chat`, and you deploy
  the workspace only — which is still a working product, because every panel
  draws from `/api/tool` and the engine needs no model. Say so on the page
  rather than letting a judge click into a 502.

Do not deploy and discover this at the demo.

### 2.2 What actually has to run

| Piece | Size | Where it can live |
| --- | --- | --- |
| `web/index.html` | 80 KB, no build step, no dependencies | Workers Static Assets, trivially |
| `problem_statement/data/*.json` | 669 KB, read-only | Bundled with the Worker |
| `aircrew/` engine + tools | ~4,000 lines, standard library only | The decision below |
| `aircrew/agent.py` | Needs outbound HTTPS to the model | Only if 2.1(a) |

The engine is pure standard library — no numpy, no pandas, nothing compiled.
That is what makes any of this possible.

### 2.3 Route A — Python Worker (recommended if it holds)

Cloudflare runs Python Workers on Pyodide behind the `python_workers`
compatibility flag. Pure-Python, standard-library-only code is exactly the case
it supports.

```
vista-crew/
  wrangler.toml
  src/
    entry.py           # on_fetch(request, env) -> Response
    aircrew/           # unchanged, minus server.py
    data/*.json        # bundled
  public/
    index.html         # served as a static asset
```

```toml
name = "vista-crew"
main = "src/entry.py"
compatibility_flags = ["python_workers"]
compatibility_date = "2026-09-01"

[assets]
directory = "public"
binding = "ASSETS"
```

`entry.py` replaces `aircrew/server.py` only — the routing layer, nothing else:

- `GET /` → `env.ASSETS.fetch(request)`
- `GET /api/health`, `GET /api/tools`, `GET /api/prompt` → as today
- `POST /api/tool` → `dispatch(_tools, name, args)`, unchanged
- `POST /api/chat` → the agent loop, but see 2.4

Two things to prove before committing to this route, in this order:

1. **Data loading.** `aircrew/data.py` opens files by path. Pyodide's filesystem
   is not the one `pathlib` expects at the edge. Either bundle each JSON as a
   Python module (`DATA = {...}`) generated at build time, or load them through
   the module loader — but test it, do not assume `open()` works.
2. **Cold start.** 669 KB of JSON parsed on first request, on top of Pyodide's
   own startup. Measure it. If a cold request takes seconds, put the parse
   behind a module-level cache and accept the first-hit cost, or trim
   `duty_clocks.json` (384 KB, the bulk of it) to the fields the engine reads.

If either of those fails, do not fight it. Go to Route B.

### 2.4 The agent loop on Workers

`aircrew/agent.py` posts with `urllib.request` and blocks. Neither works in a
Worker. Two changes:

- Replace `_post()` with the platform `fetch`. The rest of the loop — tool
  dispatch, the claim gate, the one corrective round — is untouched, which is
  the point of having kept transport in one method.
- `Agent` holds `self.messages` as instance state. A Worker is not one process
  with one conversation; two judges hitting the demo at once would interleave
  into the same history. **This is already a latent bug in the current server**
  (`_agent` is a module-level singleton and `ThreadingHTTPServer` is
  concurrent). Fix it the same way in both: key the history by a session id the
  browser sends, or make the client post the history back each turn.

Secrets, never in `wrangler.toml`:

```bash
wrangler secret put AIRCREW_API_KEY
wrangler secret put AIRCREW_BASE_URL
```

### 2.5 Route B — static workspace, engine elsewhere

If Route A does not hold, deploy `web/index.html` to Workers Static Assets and
point `/api/*` at the Python server running somewhere that can host it — a small
VM, or `cloudflared tunnel` from the demo machine.

This is not a lesser demo. The workspace is the part that proves the claim:
every panel, every ranked option, every exclusion is computed by Python and
drawn from `/api/tool`. It runs with the model switched off. What you lose is
the chat, and the chat is the part that needs a model anyway.

### 2.6 Done means

- [ ] `https://vista-crew.<subdomain>.workers.dev` loads the workspace
- [ ] A tool drill-down returns real data from the deployed engine
- [ ] `/api/health` reports the crew, flight and pairing counts, and states
      honestly whether a model is configured
- [ ] If no model: the chat input says so on the page, before it is clicked
- [ ] `tests/ui_check.js` passes against the deployed HTML, not just the local file
- [ ] The 38 questions run against the deployed engine and match `docs/THE_38_QUESTIONS.md`
- [ ] No key in the repo, in `wrangler.toml`, or in the built bundle
