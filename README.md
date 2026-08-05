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
   dialogue with the configured LLM engine, and reassembles it onto the
   *original* timing — the LLM never touches timestamps.
4. Uploads the translated subtitle back to Bazarr (via its upload API), which
   writes it to disk itself.
5. Items with no existing subtitle in any language are skipped — that needs
   speech-to-text, not translation, and is out of scope here.

Scheduled runs only pick up items that have been wanted for longer than the
configured age threshold, so Bazarr's normal providers get first chance at
finding a real subtitle. You can always force an immediate run — for the
whole queue or a single item — from the UI.

## Requirements

- A running Bazarr instance and its API key (Bazarr → Settings → General).
- A translation engine: either [Ollama](https://ollama.com) running locally
  with a model pulled (e.g. `ollama pull gemma3:4b`), or a Gemini API key
  (free tier available at [Google AI Studio](https://aistudio.google.com)).

## Running it

### Docker Compose (local dev, or if you don't already run Ollama)

```bash
cp .env.example .env   # fill in BAZARR_BASE_URL, BAZARR_API_KEY, etc.
docker compose up -d
```

This also starts an Ollama container. Then open `http://localhost:8000`.

### Unraid (or any Docker host with Bazarr/Ollama already running)

There's no publicly published image yet, so on Unraid you first build and
push your own, then point a container at it:

```bash
docker build -t ghcr.io/<your-github-username>/subtitlarr:latest .
docker push ghcr.io/<your-github-username>/subtitlarr:latest
```

(any registry works — Docker Hub, GHCR, or Unraid's own local image if
you build directly on the box with `docker build` and skip the push)

Then either:

- **Community Applications template**: use
  [`unraid/subtitlarr.xml`](unraid/subtitlarr.xml) as a starting point —
  replace `YOUR_GITHUB_USERNAME` with wherever you pushed the image, then
  add it as a template in Unraid's Docker tab (Add Container → Template →
  paste the raw file URL or import it manually). It documents every
  environment variable below as a proper UI field.
- **Manual container**: create a container from your image with:
  - **Port**: `8000` → your choice of host port
  - **Volume**: a host path (e.g. `/mnt/user/appdata/subtitlarr`) → `/data`
    (this is Subtitlarr's own database, *not* a media path)
  - **Network type**: `bridge` works for most setups. If Bazarr/Ollama are
    reachable only from a specific custom Docker network (e.g. a macvlan
    `br0` network some Tailscale subnet-router setups use), switch
    Subtitlarr to that same network in Unraid's network-type dropdown so
    it can reach them by container name.
  - **Environment variables** (see table below) — set `OLLAMA_BASE_URL` and
    `BAZARR_BASE_URL` to reach your existing containers, e.g.
    `http://<unraid-ip>:11434` and `http://<unraid-ip>:6767`, or the
    container name if they're on the same custom Docker network.

No media folders need to be mounted into this container — that's
intentional.

## Configuration

Everything below can be set as an environment variable at container start,
and most can also be changed live from the web UI afterward (Settings /
Translation Engine / Language Rules / Bazarr Connection pages).

| Variable | Purpose | Default |
|---|---|---|
| `BAZARR_BASE_URL` | Bazarr root URL | *(required)* |
| `BAZARR_API_KEY` | Bazarr API key | *(required)* |
| `ACTIVE_ENGINE` | `ollama`, `gemini`, or `nvidia` | `ollama` |
| `FALLBACK_ENGINE` | Same values, used if the active engine fails/rate-limits | *(none)* |
| `OLLAMA_BASE_URL` | Ollama server URL | `http://localhost:11434` |
| `OLLAMA_MODEL` | Model name | `gemma3:4b` |
| `OLLAMA_NUM_CTX` | Context window size in tokens — raise if long subtitle files come back incomplete (Ollama's own default of 4096 silently truncates larger prompts) | `8192` |
| `GEMINI_API_KEY` | Gemini API key | *(none)* |
| `GEMINI_MODEL` | Model name (verify current at [aistudio.google.com](https://aistudio.google.com) — Google retires older model IDs periodically) | `gemini-2.0-flash` |
| `NVIDIA_API_KEY` | NVIDIA NIM API key (free tier at [build.nvidia.com](https://build.nvidia.com)) | *(none)* |
| `NVIDIA_MODEL` | Must be a real instructable chat model, not a dedicated translation model | `deepseek-ai/deepseek-v4-flash` |
| `SCHEDULE_CRON` | 5-field cron expression for the main scheduled translation job | `0 3 * * *` |
| `AGE_THRESHOLD_DAYS` | Days a subtitle must be missing before a scheduled run will translate it | `14` |
| `DAILY_TRANSLATION_LIMIT` | Max items translated per day by scheduled/full runs (0 = unlimited); per-item re-runs bypass this | `100` |
| `PAUSE_BETWEEN_ITEMS_SECONDS` | Rest between translations so the GPU isn't pegged non-stop | `30` |
| `QUEUE_UPLOADS_ENABLED` | Hold translated subtitles locally instead of uploading immediately — push them all later in one batch from the Jobs page | `false` |
| `SYNC_MEDIA_CRON` | Optional cron to auto-refresh Bazarr's wanted list; blank = manual only | *(blank)* |
| `SYNC_SUBS_CRON` | Optional cron to auto pre-fetch source subtitle content; blank = manual only | *(blank)* |
| `DB_PATH` | SQLite file path inside the container | `/data/subtitlarr.db` |
| `RUN_CONCURRENCY` | Reserved for future use | `1` |
| `LOG_LEVEL` | Logging verbosity | `INFO` |

`PORT` is accepted but currently has no effect — the container always
listens on `8000` internally (map it to any host port you like via Docker's
own port mapping). Fixing `PORT` to actually change the internal listen
port is tracked in `TODO.md`.

Source-language priority (e.g. prefer English, then Italian) and the set of
managed target languages are configured from the **Language Rules** page in
the UI, not as environment variables — they're structured lists that persist
in Subtitlarr's own database.

## Translation engines

Ollama (local, free, no rate limits, CPU inference works but is slow on
modest hardware), Gemini (cloud, has a usable free tier, rate-limited), and
NVIDIA's free-tier NIM API (cloud, up to 40 requests/minute) are wired up
currently. The NVIDIA engine must be pointed at a real instructable chat
model (defaults to DeepSeek V4 Flash) — NVIDIA also hosts dedicated
translation-only models (e.g. Riva Translate) which don't support the
formatting instructions this app relies on and aren't compatible. The
provider interface is written so OpenAI, Anthropic, and Grok can be added
later without changes to the rest of the app.

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

## Known limitations / verify-before-relying-on

- The `Dockerfile` uses `python:3.12-slim` (Debian-based) rather than
  Alpine — pydantic-core (used by FastAPI/pydantic, this app's core web
  framework) is Rust-based and its prebuilt wheels don't reliably cover
  musl/Alpine, which caused real build failures. `slim` trades a larger
  image (~150MB base vs. Alpine's ~50MB) for a build that's known to work.
- The Docker image has not been build-tested in *this* development
  environment (no Docker daemon available here) — run
  `docker build -t subtitlarr .` yourself once before deploying, and check
  `docker compose up` reaches `http://localhost:8000`.
- `PORT` is not yet wired to anything — see the Configuration table above.
- Bazarr's upload/read endpoints were verified against the current
  `morpheus65535/bazarr` source on GitHub, not against a live instance —
  the very first real translation you run is the actual proof this works
  end-to-end. Check that the new subtitle appears correctly in Bazarr's own
  UI afterward.
