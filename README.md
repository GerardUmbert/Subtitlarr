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

Create a container from this image with:

- **Port**: `8000` → your choice of host port
- **Volume**: a host path (e.g. `/mnt/user/appdata/subtitlarr`) → `/data`
  (this is Subtitlarr's own database, *not* a media path)
- **Environment variables** (see table below) — set `OLLAMA_BASE_URL` and
  `BAZARR_BASE_URL` to reach your existing containers, e.g.
  `http://<unraid-ip>:11434` and `http://<unraid-ip>:6767`, or the container
  name if they're on the same custom Docker network.

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
| `ACTIVE_ENGINE` | `ollama` or `gemini` | `ollama` |
| `FALLBACK_ENGINE` | Same values, used if the active engine fails/rate-limits | *(none)* |
| `OLLAMA_BASE_URL` | Ollama server URL | `http://localhost:11434` |
| `OLLAMA_MODEL` | Model name | `gemma3:4b` |
| `OLLAMA_NUM_CTX` | Context window size in tokens — raise if long subtitle files come back incomplete (Ollama's own default of 4096 silently truncates larger prompts) | `8192` |
| `GEMINI_API_KEY` | Gemini API key | *(none)* |
| `GEMINI_MODEL` | Model name (verify current at [aistudio.google.com](https://aistudio.google.com) — Google retires older model IDs periodically) | `gemini-2.0-flash` |
| `SCHEDULE_CRON` | 5-field cron expression for scheduled runs | `0 3 * * *` |
| `AGE_THRESHOLD_DAYS` | Days a subtitle must be missing before a scheduled run will translate it | `14` |
| `DB_PATH` | SQLite file path inside the container | `/data/subtitlarr.db` |
| `RUN_CONCURRENCY` | Reserved for future use | `1` |
| `LOG_LEVEL` | Logging verbosity | `INFO` |
| `PORT` | Web UI port | `8000` |

Source-language priority (e.g. prefer English, then Italian) and the set of
managed target languages are configured from the **Language Rules** page in
the UI, not as environment variables — they're structured lists that persist
in Subtitlarr's own database.

## Translation engines

Only Ollama (local, free, no rate limits, CPU inference works but is slow on
modest hardware) and Gemini (cloud, has a usable free tier, rate-limited) are
wired up currently. The provider interface is written so OpenAI, Anthropic,
and Grok can be added later without changes to the rest of the app.

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

- The Docker image has not yet been build-tested in this environment
  (no Docker daemon was available during development) — before deploying,
  run `docker build -t subtitlarr .` yourself and confirm it completes
  cleanly on `python:3.12-alpine`. If any dependency fails to build due to
  missing musl wheels, switch the base image in the `Dockerfile` to
  `python:3.12-slim`.
- Bazarr's upload/read endpoints were verified against the current
  `morpheus65535/bazarr` source on GitHub, not against a live instance —
  the very first real translation you run is the actual proof this works
  end-to-end. Check that the new subtitle appears correctly in Bazarr's own
  UI afterward.
