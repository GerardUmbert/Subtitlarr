# Agent instructions for Subtitlarr

This file orients an AI coding agent running this project locally, outside
Docker (e.g. for development or a friend trying it without a container).
See README.md for what the project does and full Docker/env-var docs — this
file is about how to actually run and work on it day to day.

## First-time setup

```bash
python -m venv .venv
source .venv/Scripts/activate   # .venv/bin/activate on Linux/macOS
pip install -r requirements-dev.txt
```

Create a `.env` file in the repo root (there is no `.env` by default) with
at minimum:

```
DB_PATH=./data/subtitlarr.db
```

Bazarr connection and translation engines are set from the web UI after
first start, not via `.env` — see README.md.

**Set `DB_PATH` explicitly and always as a relative repo-local path.**
`config.py`'s hardcoded default is `/data/subtitlarr.db` — an absolute Unix
path. On Windows this silently resolves to `C:\data\subtitlarr.db`, a
location completely disconnected from the repo, and a real database can end
up living there without anyone noticing (this happened during development).
Never assume the default is fine; always pin `DB_PATH` in `.env`.

## Running the dev server

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8412
```

No `--reload` is used in normal development here — Python code changes
require a manual restart to take effect, and **static JS/CSS/HTML template
changes also require a restart**, because `ASSET_VERSION` (the cache-busting
query string on every asset URL) is set once at process start, not
per-request. Editing `app/static/css/base.css` and refreshing the browser
without restarting the server will keep serving the OLD file under the same
unchanged `?v=` URL — this looks exactly like "the fix didn't work" and has
caused real confusion during development. If a UI or behavior fix doesn't
seem to be taking effect, restart the server before assuming the fix itself
is wrong.

**Before restarting the server, check whether a translation is in
progress**: `curl http://127.0.0.1:8412/api/run/current` — if `"active":
true`, restarting will kill the in-flight translation. There is no
checkpointing; a killed item resets to `pending` and gets fully retried
later, so it's not catastrophic, but it does waste whatever GPU time was
already spent. Prefer waiting for `"active": false` before restarting when a
real run is going.

## Running tests

```bash
python -m pytest
```

The suite is fast (a few hundred ms per file, whole suite well under a
minute) and uses `tmp_path`-scoped SQLite databases plus `respx` for
mocking Ollama/Gemini HTTP calls — no real Bazarr/Ollama instance is needed
to run tests. Always run the full suite after a change, not just tests in
the file you touched — several bugs this project has hit were in shared
code (the reconciler, the migration runner) that many tests exercise
indirectly.

## Docs site (GitHub Pages, `docs/`)

Separate from the app itself — a static site deployed via
`.github/workflows/docs-pages.yml` on every push that touches `docs/**`.
Two things need a manual rebuild after editing their source, since neither
runs automatically on deploy:

- **Tailwind CSS**: real build (not the CDN runtime script — see
  `docs/assets/tailwind.build.css`'s header comment in `tailwind.config.js`
  for why), scoped to its own `docs/package.json`. After adding/removing
  Tailwind utility classes in any `docs/*.html` file, run:
  ```bash
  cd docs && npm install && npm run build:css
  ```
  and commit the regenerated `docs/assets/tailwind.build.css`.
- **Changelog page**: `docs/changelog.html` is generated from the root
  `CHANGELOG.md` by `docs/assets/build_changelog.py`. After editing
  `CHANGELOG.md`, run `python docs/assets/build_changelog.py` and commit
  the regenerated HTML — if you edit `build_changelog.py`'s
  `PAGE_HEAD`/`PAGE_TAIL` templates (nav links, head tags), regenerate too
  or the next real changelog update will silently revert them.

## Project-specific things worth knowing

- **Bazarr is the only thing with filesystem access.** This app never
  mounts media folders — everything is read/written through Bazarr's REST
  API. Do not add direct filesystem access to media paths; it defeats a
  deliberate safety design choice.
- **Subtitle translation never touches timestamps.** The LLM only ever sees
  dialogue text (index + content), never timing — timing is always taken
  from the original source subtitle and reattached after translation
  (`app/subtitles/reconciler.py::reassemble`). Do not change this to trust
  LLM-generated timestamps.
- **The Ollama/gemma3:4b response parser has been hardened against several
  real live formatting failures** (see reconciler.py's comments): markdown
  code fences, stray `<index>` wrapper tags, missing
  blank-line separation between cues, literal `\n` escape sequences instead
  of real newlines, and mixed header styles (`"N\ntext"` vs `"N. text"` in
  the same response). If you see a new "Only recovered N/M cues" failure,
  check the raw LLM response (logged via `logger.error` in
  `_parse_llm_response`'s caller) before assuming it's a translation-quality
  problem — it has usually been a parsing/formatting-drift issue, not a bad
  translation.
- **Batch size (`ollama_batch_token_budget`) is not the same as context
  window (`ollama_num_ctx`).** A batch that fits within the context window
  doesn't mean the model can reliably FORMAT that much output — small
  models lose structural reliability on long responses well before hitting
  the context limit. If translations come back mostly untranslated or with
  low cue-recovery counts, try lowering the batch override (Engine settings
  page) before assuming it's a different bug.
- **An item can be translated by Claude itself instead of a configured
  engine** — for content a cloud provider's safety filter blocks
  outright (e.g. period-accurate slurs/violence in a serious historical
  drama) rather than genuinely fails on. See the `manual-translate`
  skill (`.claude/skills/manual-translate/`) for the two API endpoints
  and the chunking/verification workflow — don't translate a whole
  large file in one pass; a long single generation is where cue-index
  alignment mistakes happen.
- **An MCP server (`mcp_server/`) exposes this same API to MCP-aware
  assistants** (Claude Code, Claude Desktop, etc.) as typed tools —
  see `plans/mcp-server.md` for the design. It is a deliberately
  separate process with its own venv (`mcp_server/requirements.txt`),
  never imported into `app/` — the `mcp` SDK pulls in a `starlette`
  version that conflicts with this app's pinned FastAPI, confirmed
  live when it was first installed into the main venv. To run it
  locally: `python -m venv mcp_server/.venv && mcp_server/.venv/Scripts/pip
  install -r mcp_server/requirements.txt`, then with the main dev
  server already running, `SUBTITLARR_BASE_URL=http://127.0.0.1:8412
  mcp_server/.venv/Scripts/python -m mcp_server` — it fetches its auth
  token automatically from the main app's `GET /api/mcp/status`
  (also shown on the Jobs page). Never install `mcp` into the main
  app's `.venv`.

## What NOT to do without being asked

- Don't restart the dev server or kill Ollama while a translation is
  actively running (check `/api/run/current` first) unless explicitly told
  to.
- Don't clear/wipe the database (`items`/`run_history`/`item_run_log`)
  without confirming scope first — the Jobs page (`/jobs`) has a built-in,
  safe "Clear database" action that preserves all settings; prefer that
  over manual SQL.
- Don't add Docker-only assumptions into code paths meant to also work
  bare-metal (e.g. hardcoded `/data/...` paths) — this file exists because
  that already happened once.
