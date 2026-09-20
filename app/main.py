import functools
import logging
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app import __version__, state
from app.api import (
    auth as auth_routes,
    bazarr_conn,
    compare,
    dashboard,
    debug,
    engine_instances,
    engines,
    external_translate,
    history,
    jobs,
    languages,
    mcp,
    queue,
    run,
    schedule,
)
from app.auth.session import NeedsLoginRedirect, ensure_admin_seeded, require_session_page
from app.bazarr.client import BazarrClient
from app.config import settings
from app.db import database, repository, settings_store
from app.engine.runner import RunController
from app.logging_conf import configure_logging
from app.providers import languages as language_names
from app.scheduler.cron import CronScheduler
from app import telemetry
from mcp_server.auth import wrap as wrap_mcp_auth
from mcp_server.server import create_mcp_asgi_app

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
templates.env.globals["app_version"] = __version__


@asynccontextmanager
async def lifespan(app: FastAPI):
    state.db_conn = database.connect(settings.db_path)
    database.apply_migrations(state.db_conn)
    # Must run before anything starts enforcing require_session/
    # require_session_page — if the admin_credentials row didn't exist yet
    # when a request first hit one of those dependencies, the app would be
    # unreachable with no recovery path (no account to log in with at all).
    ensure_admin_seeded(state.db_conn)
    auth_routes.reset_rate_limit_state()
    settings_store.load_into(state.db_conn, settings)
    reset_count = repository.reset_stuck_translating_items(state.db_conn)
    if reset_count:
        logging.getLogger(__name__).warning(
            "Reset %d item(s) stuck in 'translating' from a previous run "
            "(interrupted by a restart) back to 'pending'.", reset_count,
        )
    closed_runs = repository.close_stale_open_runs(state.db_conn)
    if closed_runs:
        logging.getLogger(__name__).warning(
            "Closed %d run(s) left open (finished_at IS NULL) from a "
            "previous process that was killed mid-batch.", closed_runs,
        )

    state.bazarr_client = BazarrClient(
        base_url=settings.bazarr_base_url, api_key=settings.bazarr_api_key
    )
    await language_names.refresh_bazarr_names(state.bazarr_client)
    state.run_controller = RunController(state.db_conn, lambda: state.bazarr_client, settings)

    state.cron_scheduler = CronScheduler()
    state.cron_scheduler.start()
    state.cron_scheduler.install(settings.schedule_cron, state.run_controller.run_scheduled)
    if settings.sync_media_cron:
        state.cron_scheduler.install(
            settings.sync_media_cron,
            functools.partial(jobs.cron_sync_media, state.run_controller),
            job_id="sync_media",
        )
    if settings.sync_subs_cron:
        state.cron_scheduler.install(
            settings.sync_subs_cron,
            functools.partial(jobs.cron_sync_subs, state.run_controller),
            job_id="sync_subs",
        )
    if settings.language_check_cron:
        state.cron_scheduler.install(
            settings.language_check_cron,
            functools.partial(jobs.cron_language_check, state.run_controller),
            job_id="language_check",
        )
    if settings.push_uploads_cron:
        state.cron_scheduler.install(
            settings.push_uploads_cron,
            jobs.cron_push_uploads,
            job_id="push_uploads",
        )
    if settings.backup_cron:
        state.cron_scheduler.install(
            settings.backup_cron,
            jobs.cron_backup,
            job_id="backup",
        )
    if settings.telemetry_cron:
        state.cron_scheduler.install(
            settings.telemetry_cron,
            functools.partial(telemetry.send_ping, state.db_conn, settings),
            job_id="telemetry",
        )

    # A fresh MCP tool server (and therefore a fresh session manager) is
    # built and mounted on every lifespan start, not once at import time
    # — the mcp SDK's StreamableHTTPSessionManager can only ever be
    # run() once per instance, but this lifespan itself can start more
    # than once in the same process (every integration test's
    # `with TestClient(app):` re-enters it). Replacing the mount here
    # each time keeps the app importable as a true module-level
    # singleton everywhere else while still giving MCP a clean session
    # manager per run. Mounted at "" (ASGI root) rather than "/mcp" — the
    # sub-app's own default internal path IS "/mcp", so mounting at ""
    # lets that produce the final "/mcp" endpoint directly; mounting at
    # "/mcp" too would either double up to "/mcp/mcp" or (with the inner
    # path overridden to "/") trigger a 307 redirect from "/mcp" to
    # "/mcp/". Identified for removal by name, not by path, since the
    # mount's path is "" here. See mcp_server/server.py's module
    # docstring for the session-manager constraint driving all of this.
    app.router.routes = [r for r in app.router.routes if getattr(r, "name", None) != "mcp"]
    mcp_asgi_app = create_mcp_asgi_app()
    app.mount("", wrap_mcp_auth(mcp_asgi_app), name="mcp")
    async with mcp_asgi_app.router.lifespan_context(mcp_asgi_app):
        yield

    state.cron_scheduler.shutdown()
    await state.bazarr_client.aclose()
    state.db_conn.close()


def _get_or_create_session_secret() -> str:
    """SessionMiddleware needs its signing key at app-construction time,
    before lifespan() has opened the DB connection this app otherwise uses
    for every other persisted secret (the MCP/external-translate tokens
    live in app_config) — middleware can't be added inside lifespan once
    the app has started. Stored as a plain file next to the DB instead;
    same file survives restarts the same way app_config would, just one
    layer earlier in startup than the DB is available."""
    secret_path = Path(settings.db_path).parent / ".session_secret"
    if secret_path.exists():
        return secret_path.read_text(encoding="utf-8").strip()
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_urlsafe(32)
    secret_path.write_text(secret, encoding="utf-8")
    return secret


app = FastAPI(title="Subtitlarr", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=_get_or_create_session_secret(), same_site="lax")


@app.exception_handler(NeedsLoginRedirect)
async def _needs_login_redirect_handler(request: Request, exc: NeedsLoginRedirect):
    return RedirectResponse(exc.location, status_code=303)


app.include_router(auth_routes.router)
app.include_router(dashboard.router)
app.include_router(run.router)
app.include_router(queue.router)
app.include_router(engines.router)
app.include_router(engine_instances.router)
app.include_router(languages.router)
app.include_router(bazarr_conn.router)
app.include_router(schedule.router)
app.include_router(jobs.router)
app.include_router(history.router)
app.include_router(compare.router)
app.include_router(debug.router)
app.include_router(mcp.router)
app.include_router(external_translate.router)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
# The /mcp mount itself is (re-)created inside lifespan() above, not
# here — see that function's comment for why.


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


def _page(name: str, active_page: str):
    async def handler(request: Request, _auth=Depends(require_session_page)):
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
app.get("/compare", response_class=HTMLResponse)(_page("compare", "engines"))
app.get("/mcp-server", response_class=HTMLResponse)(_page("mcp", "mcp-server"))
app.get("/external-translate", response_class=HTMLResponse)(_page("external_translate", "external-translate"))
