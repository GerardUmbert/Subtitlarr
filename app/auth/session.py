"""App-wide login: single admin account, bcrypt-hashed password, a signed
session cookie (Starlette's own SessionMiddleware, wired up in
app/main.py). Session lifetime is "until logout or app restart" — no
expiry timer, matching how the *arr tools default and keeping this first
version simple (see AUTH_PLAN.md, a local, untracked design doc — not in
this repo's git history).

Login-rate-limiting and the actual /login, /change-password, /logout
ROUTES live in app/api/auth.py — this module is the underlying mechanism
(hashing, seeding, the require_session dependency) reused by them and by
main.py's page-route wiring."""
import secrets

import bcrypt
from fastapi import Depends, HTTPException, Request

from app import state
from app.db import repository


class NeedsLoginRedirect(Exception):
    """Raised by require_session_page (page routes only) instead of a
    plain HTTPException — a browser loading a page wants a redirect to
    /login or /change-password, not a JSON 401/403 body. Caught by a
    dedicated exception handler registered in main.py. API routes
    (app/api/*.py) use require_session instead, which raises a normal
    HTTPException — a script/fetch() call wants a real status code and
    JSON body to branch on, not a redirect it would have to follow and
    then fail to parse as JSON anyway."""
    def __init__(self, location: str):
        self.location = location

DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin"

_SESSION_KEY = "admin_logged_in"

# A fixed, valid-format bcrypt hash with no corresponding real password —
# compared against on every login attempt for a username that doesn't
# match, so a wrong-username attempt costs the same bcrypt.checkpw() work
# as a wrong-password attempt against a real hash. Without this, "unknown
# username" would return near-instantly (no hash to check against) while
# "wrong password for a real user" always pays bcrypt's cost, letting a
# timing difference distinguish the two outside of any error message
# difference.
_DUMMY_HASH = bcrypt.hashpw(secrets.token_bytes(32), bcrypt.gensalt())


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def ensure_admin_seeded(conn) -> None:
    """Creates the single admin_credentials row on first-ever startup —
    username "admin", password "admin", must_change_password=True. Called
    from main.py's lifespan, AFTER migrations have run and BEFORE the app
    starts accepting requests — ordering matters: if enforcement (the
    require_session dependency) could ever run before this seed exists,
    the app would be permanently unreachable with no recovery path."""
    with state.db_lock:
        creds = repository.get_admin_credentials(conn)
    if creds is None:
        with state.db_lock:
            repository.create_admin_credentials(
                conn, DEFAULT_USERNAME, hash_password(DEFAULT_PASSWORD), must_change_password=True,
            )


def verify_login(conn, username: str, password: str) -> bool:
    """Timing-safe: always performs exactly one bcrypt.checkpw() call,
    against the real stored hash if the username matches, or against
    _DUMMY_HASH otherwise — so a wrong username and a wrong password for
    the real username take the same time and both simply return False,
    with no separate code path an attacker could distinguish."""
    with state.db_lock:
        creds = repository.get_admin_credentials(conn)
    if creds is not None and username == creds["username"]:
        return _verify(password, creds["password_hash"])
    _verify(password, _DUMMY_HASH.decode("utf-8"))
    return False


def log_in(request: Request) -> None:
    """Regenerates the session (a fresh dict, not just setting a key on
    whatever session already existed) — session-fixation defense.
    Starlette's SessionMiddleware itself doesn't rotate the underlying
    cookie value automatically; clearing and rebuilding the session dict
    here is what actually changes the signed cookie value sent back,
    since the cookie encodes the session's contents directly (there's no
    separate server-side session-id to rotate — see SessionMiddleware's
    own implementation)."""
    request.session.clear()
    request.session[_SESSION_KEY] = True


def log_out(request: Request) -> None:
    request.session.clear()


def is_logged_in(request: Request) -> bool:
    return bool(request.session.get(_SESSION_KEY))


def _check_session(request: Request, conn) -> str | None:
    """Returns None if the session is fully valid, or a redirect-target
    path ("/login" or "/change-password") describing what's missing —
    shared by both require_session (API: turns this into a JSON error)
    and require_session_page (pages: turns this into a real redirect)."""
    if not is_logged_in(request):
        return "/login"
    with state.db_lock:
        creds = repository.get_admin_credentials(conn)
    if creds is not None and creds["must_change_password"]:
        return "/change-password"
    return None


def require_session(request: Request, conn=Depends(state.get_conn)) -> None:
    """API-route dependency (app/api/*.py) — a plain HTTPException with a
    real status code and JSON body, for a fetch()/script caller to branch
    on. See require_session_page for the page-route equivalent."""
    target = _check_session(request, conn)
    if target == "/login":
        raise HTTPException(status_code=401, detail="Not logged in")
    if target == "/change-password":
        raise HTTPException(status_code=403, detail="Password change required")


def require_session_page(request: Request, conn=Depends(state.get_conn)) -> None:
    """Page-route dependency (main.py's _page() routes) — raises
    NeedsLoginRedirect instead of a JSON error, since a browser loading an
    HTML page should be redirected to /login or /change-password, not
    shown a raw JSON error body."""
    target = _check_session(request, conn)
    if target is not None:
        raise NeedsLoginRedirect(target)


def require_session_or_mcp_token(request: Request, conn=Depends(state.get_conn)) -> None:
    """Router-level dependency for most app/api/*.py routers — passes for
    EITHER a valid logged-in session (the browser's own page JS) OR the
    MCP server's bearer token (a script/webhook that isn't a logged-in
    browser). Deliberately does NOT also accept the external-translate
    token here — that token is scoped narrowly to its own
    /api/external-translate/* routes (which keep their existing separate
    _require_auth check, untouched by this dependency) and must not
    become a de facto skeleton key for the rest of the API, e.g.
    authenticating a POST to /api/run/now.

    A valid session still must not have must_change_password pending
    (same as require_session) — a bearer token bypasses that check
    entirely, since a script has no password to change."""
    # Local import: app.api.mcp doesn't import this module, so there's no
    # real cycle, but keeping it local avoids coupling this module's
    # import-time behavior to app.api's internal structure.
    from app.api.mcp import get_or_create_token

    auth_header = request.headers.get("authorization", "")
    if auth_header == f"Bearer {get_or_create_token(conn)}":
        return

    target = _check_session(request, conn)
    if target == "/login":
        raise HTTPException(status_code=401, detail="Not logged in")
    if target == "/change-password":
        raise HTTPException(status_code=403, detail="Password change required")
