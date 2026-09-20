"""Login/logout/change-password routes. See app/auth/session.py for the
underlying hashing/session mechanism and app/main.py for where
SessionMiddleware and NeedsLoginRedirect's exception handler are wired
up. Deliberately NOT under require_session/require_session_page — these
are exactly the routes that must stay reachable while logged out (login
itself) or with must_change_password still set (change-password), since
require_session_page would otherwise redirect a request to the very page
it's trying to reach, an infinite loop."""
import time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from app import state
from app.auth import session as auth_session
from app.db import repository

# Imported lazily inside the route handlers below (not at module import
# time) to avoid a circular import — app.main imports this router to
# include it, so this module can't import `templates` from app.main at
# the top level.

router = APIRouter(tags=["auth"])

# Simple in-memory per-IP rate limit — single-process app, no distributed
# state needed. Keyed on request.client.host, which behind a reverse proxy
# without X-Forwarded-For handling would collapse to one IP for everyone
# behind it; acceptable for this app's threat model (this isn't meant to
# survive a distributed attack, just slow down casual brute-forcing of a
# single-admin login over a LAN/Tailscale/VPN).
_LOGIN_ATTEMPT_WINDOW_SECONDS = 60
_LOGIN_ATTEMPT_MAX = 5
_login_attempts: dict[str, list[float]] = defaultdict(list)


def reset_rate_limit_state() -> None:
    """Called from main.py's lifespan on every startup — this dict is
    process-lifetime state, not something that should persist across a
    restart (an attacker's prior attempts shouldn't count against a fresh
    boot), and (just as importantly for tests) every `with TestClient(app):`
    re-enters lifespan() in the same Python process, so without this reset
    the SAME dict leaks across otherwise-independent test functions —
    confirmed live: test_login_rate_limited_after_repeated_failures'
    intentional 429 bled into unrelated later tests using the same fake
    client IP, causing them to fail on an unrelated 429 instead of the
    status they actually expected."""
    _login_attempts.clear()


def _rate_limited(ip: str) -> bool:
    now = time.monotonic()
    attempts = _login_attempts[ip]
    attempts[:] = [t for t in attempts if now - t < _LOGIN_ATTEMPT_WINDOW_SECONDS]
    return len(attempts) >= _LOGIN_ATTEMPT_MAX


def _record_attempt(ip: str) -> None:
    _login_attempts[ip].append(time.monotonic())


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, conn=Depends(state.get_conn)):
    from app.main import templates  # local import — avoids a circular import, see module docstring

    if auth_session.is_logged_in(request):
        creds = repository.get_admin_credentials(conn)
        if creds is not None and creds["must_change_password"]:
            return RedirectResponse("/change-password", status_code=303)
        return RedirectResponse("/", status_code=303)

    # Shown only while the account is still on the seeded default — once
    # a real password is set, must_change_password is False and this
    # hint disappears, since showing "default is admin/admin" forever
    # would be actively misleading past first setup.
    creds = repository.get_admin_credentials(conn)
    show_default_hint = creds is not None and creds["must_change_password"]
    return templates.TemplateResponse(request, "login.html", {"show_default_hint": show_default_hint})


@router.post("/api/auth/login")
def login_submit(req: LoginRequest, request: Request, conn=Depends(state.get_conn)):
    ip = request.client.host if request.client else "unknown"
    if _rate_limited(ip):
        raise HTTPException(status_code=429, detail="Too many login attempts — try again in a minute.")
    _record_attempt(ip)

    if not auth_session.verify_login(conn, req.username, req.password):
        raise HTTPException(status_code=401, detail="Incorrect username or password.")

    auth_session.log_in(request)
    creds = repository.get_admin_credentials(conn)
    redirect_to = "/change-password" if creds and creds["must_change_password"] else "/"
    return {"redirect": redirect_to}


@router.post("/api/auth/logout")
def logout_submit(request: Request):
    auth_session.log_out(request)
    return {"redirect": "/login"}


@router.get("/change-password", response_class=HTMLResponse)
def change_password_page(request: Request):
    from app.main import templates  # local import — avoids a circular import, see module docstring

    if not auth_session.is_logged_in(request):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "change_password.html", {})


@router.post("/api/auth/change-password")
def change_password_submit(req: ChangePasswordRequest, request: Request, conn=Depends(state.get_conn)):
    if not auth_session.is_logged_in(request):
        raise HTTPException(status_code=401, detail="Not logged in")

    creds = repository.get_admin_credentials(conn)
    if creds is None or not auth_session.verify_login(conn, creds["username"], req.current_password):
        raise HTTPException(status_code=401, detail="Current password is incorrect.")

    if req.new_password == auth_session.DEFAULT_PASSWORD:
        raise HTTPException(status_code=422, detail="Choose a password other than the default.")
    if not req.new_password:
        raise HTTPException(status_code=422, detail="Password must not be empty.")

    repository.update_admin_password(conn, auth_session.hash_password(req.new_password), must_change_password=False)
    return {"redirect": "/"}
