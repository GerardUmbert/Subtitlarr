import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import state
from app.api import bazarr_conn, dashboard, engines, history, jobs, languages, queue, run, schedule
from app.bazarr.client import BazarrClient
from app.config import settings
from app.db import database, repository, settings_store
from app.engine.runner import RunController
from app.logging_conf import configure_logging
from app.scheduler.cron import CronScheduler

configure_logging()

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Changes on every process start, so static asset URLs change on every
# restart/deploy — forces browsers to fetch fresh JS/CSS instead of serving
# a stale cached copy. Static JS files got edited many times in a single
# session during development, and the browser silently kept serving an old
# cached version, making fixed bugs look unfixed.
ASSET_VERSION = str(int(time.time()))
templates.env.globals["asset_version"] = ASSET_VERSION


@asynccontextmanager
async def lifespan(app: FastAPI):
    state.db_conn = database.connect(settings.db_path)
    database.apply_migrations(state.db_conn)
    settings_store.load_into(state.db_conn, settings)
    reset_count = repository.reset_stuck_translating_items(state.db_conn)
    if reset_count:
        logging.getLogger(__name__).warning(
            "Reset %d item(s) stuck in 'translating' from a previous run "
            "(interrupted by a restart) back to 'pending'.", reset_count,
        )

    state.bazarr_client = BazarrClient(
        base_url=settings.bazarr_base_url, api_key=settings.bazarr_api_key
    )
    state.run_controller = RunController(state.db_conn, lambda: state.bazarr_client, settings)

    state.cron_scheduler = CronScheduler()
    state.cron_scheduler.start()
    state.cron_scheduler.install(settings.schedule_cron, state.run_controller.run_scheduled)

    yield

    state.cron_scheduler.shutdown()
    await state.bazarr_client.aclose()
    state.db_conn.close()


app = FastAPI(title="Subtitlarr", lifespan=lifespan)

app.include_router(dashboard.router)
app.include_router(run.router)
app.include_router(queue.router)
app.include_router(engines.router)
app.include_router(languages.router)
app.include_router(bazarr_conn.router)
app.include_router(schedule.router)
app.include_router(jobs.router)
app.include_router(history.router)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


def _page(name: str, active_page: str):
    async def handler(request: Request):
        response = templates.TemplateResponse(
            request, f"{name}.html", {"active_page": active_page}
        )
        # The HTML itself must never be cached — it's what carries the
        # current asset_version query string. If the browser cached an old
        # page, it would keep pointing at old (or now-404) asset URLs
        # forever instead of picking up the new version on next load.
        response.headers["Cache-Control"] = "no-store"
        return response

    return handler


app.get("/", response_class=HTMLResponse)(_page("dashboard", "dashboard"))
app.get("/queue", response_class=HTMLResponse)(_page("queue", "queue"))
app.get("/engines", response_class=HTMLResponse)(_page("engines", "engines"))
app.get("/languages", response_class=HTMLResponse)(_page("languages", "languages"))
app.get("/bazarr", response_class=HTMLResponse)(_page("bazarr", "bazarr"))
app.get("/settings", response_class=HTMLResponse)(_page("settings", "settings"))
app.get("/jobs", response_class=HTMLResponse)(_page("jobs", "jobs"))
app.get("/history", response_class=HTMLResponse)(_page("history", "history"))
