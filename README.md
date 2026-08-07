# Subtitlarr

Fills gaps in your Bazarr subtitle library by translating subtitles you already
have (e.g. English) into languages you're missing (e.g. Spanish), using a
local or cloud LLM. Runs as its own container — it never mounts your media
folders. All reading and writing of subtitles happens through Bazarr's own
REST API, so Bazarr remains the only thing with filesystem access to your
library.

## How it works

1. Polls Bazarr's wanted-subtitle list on a schedule.
2. For each wanted item, checks (via Bazarr's API) whether a subtitle already
   exists in one of your preferred source languages.
3. If so, reads that subtitle's content (via Bazarr's API), translates the
   dialogue with your configured engine cascade, and reassembles it onto the
   *original* timing — the LLM never touches timestamps.
4. Uploads the translated subtitle back to Bazarr (via its upload API), which
   writes it to disk itself.
5. Items with no existing subtitle in any language are skipped — that needs
   speech-to-text, not translation, and is out of scope here.

Scheduled runs only pick up items that have been wanted for longer than the
configured age threshold, so Bazarr's normal providers get first chance at
finding a real subtitle. You can always force an immediate run — for the
whole queue or a single item — from the UI.

## Translation engines

Subtitlarr doesn't use a single fixed engine — you build a **cascade** of one
or more engine instances from the **Translation Engine** page in the UI, and
it walks down that list in order for every item. If an engine rate-limits,
errors, or has its output blocked, the next instance in the cascade is tried
automatically; an engine that trips 3 consecutive failures gets a 24-hour
cooldown and is skipped until it clears (or you clear it manually from the
Jobs page).

You can add as many instances as you like, of any mix of these provider
types, reorder them by dragging, and optionally insert a "stop cascade here"
separator to fence off a group (e.g. keep free-tier cloud engines first, with
local engines held in reserve behind a separator rather than tried
automatically):

- **Ollama** — local, free, no rate limits. CPU inference works but is slow
  on modest hardware; a dedicated/integrated GPU helps more with larger
  models than small ones. Pull a model first, e.g. `ollama pull gemma3:4b`.
- **llama.cpp** — [llama.cpp's](https://github.com/ggml-org/llama.cpp) own
  built-in local HTTP server. A separate local runtime from Ollama, not
  another name for it — no web UI, no model-switching endpoint; the server
  is started with one fixed model already loaded via its own CLI flags.
- **Gemini** — Google's cloud API, usable free tier. Get a key at
  [Google AI Studio](https://aistudio.google.com).
- **NVIDIA NIM** — free tier, up to 40 requests/minute. Get a key at
  [build.nvidia.com](https://build.nvidia.com). Must be pointed at a real
  instructable chat model (defaults to DeepSeek V4 Flash) — NVIDIA also
  hosts dedicated translation-only models (e.g. Riva Translate) that don't
  support the formatting instructions this app relies on and aren't
  compatible.
- **OpenRouter** — routes to many different underlying chat models via one
  OpenAI-compatible endpoint. Get a key at
  [openrouter.ai/keys](https://openrouter.ai/keys); see
  [openrouter.ai/models](https://openrouter.ai/models) for the full lineup.
  Free `:free` model variants are capped at 20 requests/minute and 50/day.
- **Groq** — fixed lineup of models on Groq's own LPU hardware. Get a free
  key at [console.groq.com/keys](https://console.groq.com/keys).

Every instance has its own API key/model/batch-size configuration, kept
entirely in Subtitlarr's own database — there's no environment variable for
engine setup anymore. The provider interface is written so more engines can
be added later without changes to the rest of the app.

### Recommended cascade

Google's Gemini free tier gives each *model* its own separate 500-requests/day
quota, and that quota is tied to the API key (i.e. the Google account), not
to Subtitlarr — and those two things (2 models × 2 accounts) stack. Two
consequences worth using deliberately:

- Two Gemini API keys from two different Google accounts, each added as
  **two** instances (one per fast free-tier model), for **4 instances
  total** — 2000 requests/day combined instead of 500:
  1. **"Gemini Main"** — key from account A, model **`gemini-3.5-flash-lite`**
  2. **"Gemini Main gemini-3.1-flash-lite"** — key from account A, model
     **`gemini-3.1-flash-lite`**
  3. **"Gemini Secondary"** — key from account B, model
     **`gemini-3.5-flash-lite`**
  4. **"Gemini Secondary gemini-3.1-flash-lite"** — key from account B,
     model **`gemini-3.1-flash-lite`**

  Order them in the cascade so both of account A's models are tried before
  falling to account B — the cascade only spends an instance's quota once
  everything ahead of it is exhausted or rate-limited.
- Put your fastest, most reliable free-tier Gemini model(s) at the top of
  the cascade, add a **separator** right after them, and leave your local
  engines (Ollama, llama.cpp) below the separator, disabled from automatic
  fallback. This keeps a scheduled run from silently burning hours of local
  GPU/CPU time as a fallback for what's usually just temporary cloud
  rate-limiting — instead, once every Gemini instance above the separator
  is exhausted for the day, the run stops with the remaining items still
  `pending`, and you can pick up the leftovers with a manual run against a
  local engine (or just wait for tomorrow's quota reset).
- See [`docs/api-keys-setup.md`](docs/api-keys-setup.md) for exact steps to
  get Gemini/NVIDIA keys and the token budgets to set per engine.

## Requirements

- A running Bazarr instance and its API key (Bazarr → Settings → General).
- At least one translation engine configured from the Translation Engine
  page after first start — see above.

## Running it

### Docker (published image)

```bash
docker run -d \
  --name subtitlarr \
  -p 8000:8000 \
  -v ./data:/data \
  -e BAZARR_BASE_URL=http://<your-bazarr-host>:6767 \
  -e BAZARR_API_KEY=<your-bazarr-api-key> \
  ghcr.io/gerardumbert/subtitlarr:latest
```

Open `http://localhost:8000`, then add at least one engine instance from the
**Translation Engine** page before running a translation.

### Docker Compose (local dev, or if you don't already run Ollama)

```bash
cp .env.example .env   # fill in BAZARR_BASE_URL, BAZARR_API_KEY, etc.
docker compose up -d
```

This also starts an Ollama container. Then open `http://localhost:8000` and
add an Ollama engine instance pointing at `http://ollama:11434`.

### Unraid (or any Docker host with Bazarr/Ollama already running)

- **Community Applications template**: use
  [`unraid/subtitlarr.xml`](unraid/subtitlarr.xml) as a starting point, then
  add it as a template in Unraid's Docker tab (Add Container → Template →
  paste the raw file URL or import it manually). It points at the published
  `ghcr.io/gerardumbert/subtitlarr` image and documents every environment
  variable below as a proper UI field.
- **Manual container**: create a container from
  `ghcr.io/gerardumbert/subtitlarr:latest` with:
  - **Port**: `8000` → your choice of host port
  - **Volume**: a host path (e.g. `/mnt/user/appdata/subtitlarr`) → `/data`
    (this is Subtitlarr's own database, *not* a media path)
  - **Network type**: `bridge` works for most setups. If Bazarr/Ollama are
    reachable only from a specific custom Docker network (e.g. a macvlan
    `br0` network some Tailscale subnet-router setups use), switch
    Subtitlarr to that same network in Unraid's network-type dropdown so
    it can reach them by container name.
  - **Environment variables** (see table below) — set `BAZARR_BASE_URL` to
    reach your existing Bazarr container, e.g. `http://<unraid-ip>:6767`, or
    the container name if they're on the same custom Docker network. Engine
    connection details (Ollama/Gemini/etc.) are configured from the web UI
    after first start, not via environment variables.

No media folders need to be mounted into this container — that's
intentional.

## Configuration

Everything below can be set as an environment variable at container start,
and all of it can also be changed live from the web UI afterward (Settings /
Language Rules / Bazarr Connection pages). Translation engine setup lives
entirely in the **Translation Engine** page — see above — not in this table.

| Variable | Purpose | Default |
|---|---|---|
| `BAZARR_BASE_URL` | Bazarr root URL | *(required)* |
| `BAZARR_API_KEY` | Bazarr API key | *(required)* |
| `SCHEDULE_CRON` | 5-field cron expression for the main scheduled translation job | `0 3 * * *` |
| `AGE_THRESHOLD_DAYS` | Days a subtitle must be missing before a scheduled run will translate it | `14` |
| `DAILY_TRANSLATION_LIMIT` | Max items translated per day by scheduled/full runs (0 = unlimited); per-item re-runs bypass this | `100` |
| `PAUSE_BETWEEN_ITEMS_SECONDS` | Rest between translations so the GPU isn't pegged non-stop | `30` |
| `QUEUE_UPLOADS_ENABLED` | Hold translated subtitles locally instead of uploading immediately — push them all later in one batch from the Jobs page (see note below) | `false` |
| `SYNC_MEDIA_CRON` | Optional cron to auto-refresh Bazarr's wanted list; blank = manual only | *(blank)* |
| `SYNC_SUBS_CRON` | Optional cron to auto pre-fetch source subtitle content; blank = manual only | *(blank)* |
| `DB_PATH` | SQLite file path inside the container | `/data/subtitlarr.db` |
| `RUN_CONCURRENCY` | Reserved for future use | `1` |
| `LOG_LEVEL` | Logging verbosity | `INFO` |

`PORT` is accepted but currently has no effect — the container always
listens on `8000` internally (map it to any host port you like via Docker's
own port mapping).

**On `QUEUE_UPLOADS_ENABLED`**: if your Bazarr host (or its storage — e.g. a
NAS array) spins down disks when idle, leave this `false` (upload
immediately) only if the disks are already awake for other reasons, or set
it `true` and push the queued batch manually from the Jobs page once you're
ready — that way a scheduled overnight run doesn't wake sleeping disks once
per item. If your disks never spin down anyway, it doesn't matter either
way — leave it `false` for simplicity.

Source-language priority (e.g. prefer English, then Italian) and the set of
managed target languages are configured from the **Language Rules** page in
the UI, not as environment variables — they're structured lists that persist
in Subtitlarr's own database. That page also has two translation-style
toggles worth knowing about:

- **European Spanish** — on by default, and only affects the `es` target
  language. Steers the LLM toward Spain Spanish phrasing/vocabulary instead
  of a generic or Latin American register.
- **Catalan Vegeta insults** — only affects the `ca` (Catalan) target
  language. Off by default; when enabled, insults/put-downs in the source
  dialogue are translated in the flavor of Vegeta's iconic Catalan dub
  (proud, colorful, larger-than-life), not literally. Has no effect on any
  other target language.

## Development

```bash
python -m venv .venv
source .venv/Scripts/activate   # or .venv/bin/activate on Linux/macOS
pip install -r requirements-dev.txt
DB_PATH=./data/dev.db uvicorn app.main:app --reload
```

Run the test suite:

```bash
python -m pytest
```

See [AGENTS.md](AGENTS.md) for more detailed day-to-day development notes.

## Known limitations / verify-before-relying-on

- The `Dockerfile` uses `python:3.12-slim` (Debian-based) rather than
  Alpine — pydantic-core (used by FastAPI/pydantic, this app's core web
  framework) is Rust-based and its prebuilt wheels don't reliably cover
  musl/Alpine, which caused real build failures. `slim` trades a larger
  image (~150MB base vs. Alpine's ~50MB) for a build that's known to work.
- `PORT` is not yet wired to anything — see the Configuration table above.
