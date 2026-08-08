"""
search/auth.py — single-user app-level login.

The app is publicly reachable (no reverse-proxy auth in front),
so every request except /login, /logout, /static/* and /healthz
needs a valid signed session cookie. Configuration is environment-
driven: when AUTH_PASSWORD_HASH is empty, auth is disabled (dev /
tests); when set, the matching AUTH_USERNAME is required to log in.

Session: signed JSON cookie via URLSafeTimedSerializer (itsdangerous).
The cookie payload is `{"u": <username>, "iat": <unix-ts>}`. We
verify with max_age=AUTH_REMEMBER_DAYS so a stolen long-lived
cookie can't outlive the configured window even if the browser
keeps sending it.

"Remember me" controls the cookie's max_age at *issue* time:
checked → 30-day persistent cookie; unchecked → session cookie
(browser drops on close, server still enforces 30 days if it
somehow survives).

bcrypt is the password KDF (default cost 12). The hash is stored
in env (via a Kubernetes Secret), never in source. See
.env.example for the helper command that prints a fresh hash.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from http.cookies import SimpleCookie

import bcrypt  # type: ignore[import-not-found]
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

# Public — referenced from app.py (middleware) and base.html (logout form).
SESSION_COOKIE_NAME = "image_search_session"
DEFAULT_REMEMBER_DAYS = 30
# Salt used by URLSafeTimedSerializer. Changing it invalidates every
# outstanding session — only bump when rotating AUTH_SECRET_KEY, and
# expect everyone to log in again. Keep the salt namespaced to this
# app so a stolen itsdangerous key from a sibling service can't forge
# our cookies (and vice versa).
SESSION_SALT = "image-search-session-v1"

# Paths that bypass the auth gate. Exact match or "starts with <entry>/".
# /login + /logout must be reachable without a session so users can
# authenticate in the first place and end their session. /static/* is
# the asset mount; /healthz is the k8s probe.
AUTH_PUBLIC_PATHS: tuple[str, ...] = (
    "/login",
    "/logout",
    "/static/",
    "/healthz",
)


@dataclass(frozen=True)
class AuthConfig:
    username: str
    password_hash: str
    secret_key: str
    cookie_secure: bool
    remember_days: int


def is_enabled(cfg: AuthConfig) -> bool:
    """True when both password hash and secret key are configured.

    When False, the auth middleware short-circuits (no gating) and
    /login redirects to /. Used by dev / tests where AUTH_* env
    vars are intentionally blank.
    """
    return bool(cfg.password_hash) and bool(cfg.secret_key)


def auth_config_from(cfg) -> AuthConfig:
    """Lift the auth-relevant fields off the main Config dataclass.

    Kept as a separate function (not a Config method) so search.auth
    has no back-reference to search.config — keeps the auth module
    standalone and easy to test in isolation.
    """
    return AuthConfig(
        username=cfg.auth_username,
        password_hash=cfg.auth_password_hash,
        secret_key=cfg.auth_secret_key,
        cookie_secure=cfg.auth_cookie_secure,
        remember_days=cfg.auth_remember_days,
    )


# ----- Password hashing -----


def hash_password(plain: str) -> str:
    """bcrypt-hash a password at the default cost (12 rounds).

    Output is the standard $2b$... ASCII string suitable for storing
    in env / a Kubernetes Secret.
    """
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time compare against a bcrypt hash.

    Returns False (never raises) on a malformed hash — defensive
    against accidental config drift (e.g. a plain-text password
    pasted into AUTH_PASSWORD_HASH by mistake).
    """
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


# ----- Session token (sign / verify) -----


def _serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key, salt=SESSION_SALT)


def make_session_token(secret_key: str, username: str, issued_at: float | None = None) -> str:
    """Sign a session payload. `issued_at` defaults to time.time().

    Tests pass an explicit value to keep token contents deterministic.
    """
    payload = {"u": username, "iat": issued_at if issued_at is not None else time.time()}
    return _serializer(secret_key).dumps(payload)


def read_session_token(secret_key: str, token: str, max_age: int) -> dict | None:
    """Verify a session token. Returns the payload or None.

    `max_age` is in seconds and enforced server-side; a token whose
    `iat` is older than this is rejected with SignatureExpired.
    """
    try:
        return _serializer(secret_key).loads(token, max_age=max_age)
    except SignatureExpired:
        return None
    except BadSignature:
        return None


# ----- Cookie helpers -----


def set_session_cookie(
    response,
    *,
    secret_key: str,
    username: str,
    remember: bool,
    cookie_secure: bool,
    remember_days: int,
    issued_at: float | None = None,
) -> None:
    """Attach the signed session cookie to a response.

    `remember=True` → persistent cookie (max_age = remember_days * 86400).
    `remember=False` → session cookie (browser drops on close).
    Both are verified server-side with max_age = remember_days * 86400,
    so the persistent variant expires regardless of browser behaviour.
    """
    token = make_session_token(secret_key, username, issued_at=issued_at)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=remember_days * 86400 if remember else None,
        httponly=True,
        secure=cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response, *, cookie_secure: bool) -> None:
    """Delete the session cookie. Used by /logout."""
    response.delete_cookie(SESSION_COOKIE_NAME, path="/", secure=cookie_secure)


# ----- Cookie header parsing -----


def parse_cookie_header(header: str | None) -> dict[str, str]:
    """Parse a Cookie header into a {name: value} dict.

    Empty / missing / malformed input returns {}. Used by the
    ASGI auth middleware, which sees raw scope headers (not a
    parsed Cookie object the way FastAPI dependencies do).
    """
    if not header:
        return {}
    try:
        c = SimpleCookie()
        c.load(header)
        return {k: v.value for k, v in c.items()}
    except Exception:
        return {}


# ----- ASGI auth gate middleware -----


class AuthGateMiddleware:
    """ASGI middleware that gates every request on a valid session.

    Public paths (`AUTH_PUBLIC_PATHS`) bypass the gate: /login and
    /logout so users can authenticate / end their session in the
    first place; /static/* for asset requests; /healthz for the k8s
    probe.

    Every other path requires a signed session cookie whose `iat`
    is within the configured `remember_days` window. Authenticated
    requests have `request.state.current_user` set so templates can
    render the logout button and any other user-aware chrome.

    Unauthenticated requests get a 302 to `/login?next=<path>`, so
    deep links land the user back where they started after login.

    When auth is disabled (empty password hash + secret key), the
    middleware is a no-op — the inner app handles every request
    and `/login` itself redirects home (handled by the route).

    Implemented as a pure-ASGI middleware (not Starlette's
    BaseHTTPMiddleware) so it composes cleanly with the existing
    no-cache middleware and avoids the streaming-response quirks
    BaseHTTPMiddleware is known for.
    """

    def __init__(self, app, *, auth: AuthConfig, enabled: bool) -> None:
        self.app = app
        self.auth = auth
        self.enabled = enabled

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not self.enabled:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if self._is_public(path):
            await self.app(scope, receive, send)
            return

        # Read the raw Cookie header from the ASGI scope. Starlette's
        # Request.cookies parses this same header; doing it here keeps
        # the middleware self-contained.
        headers = dict(scope.get("headers") or [])
        cookie_header = headers.get(b"cookie", b"").decode("latin-1", errors="replace")
        cookies = parse_cookie_header(cookie_header)
        token = cookies.get(SESSION_COOKIE_NAME)
        max_age = self.auth.remember_days * 86400
        payload = read_session_token(self.auth.secret_key, token, max_age) if token else None
        if payload:
            # Starlette reads `scope["state"]` as `request.state`.
            # Setting it here makes `request.state.current_user`
            # available in route handlers and (via AuthAwareTemplates)
            # in every Jinja template render.
            scope.setdefault("state", {})
            scope["state"]["current_user"] = str(payload.get("u", ""))
            await self.app(scope, receive, send)
            return

        # Unauthenticated → 302 to /login?next=<original>. The next
        # URL is the path + original query string; safe-characters
        # allow common URL chars through unescaped so the redirect
        # stays readable.
        from urllib.parse import quote

        next_path = path
        qs = scope.get("query_string") or b""
        if qs:
            next_path = f"{path}?{qs.decode('latin-1', errors='replace')}"
        location = f"/login?next={quote(next_path, safe='/?&=:')}"
        await send(
            {
                "type": "http.response.start",
                "status": 302,
                "headers": [(b"location", location.encode("utf-8"))],
            }
        )
        await send({"type": "http.response.body", "body": b""})

    @staticmethod
    def _is_public(path: str) -> bool:
        # AUTH_PUBLIC_PATHS mixes exact paths ("/login", "/logout",
        # "/healthz") and a directory prefix ("/static/"). We treat
        # entries ending in "/" as prefix matches and others as
        # exact matches. That keeps `/loginfoo` from accidentally
        # bypassing the gate.
        for public in AUTH_PUBLIC_PATHS:
            if public.endswith("/"):
                if path.startswith(public):
                    return True
            else:
                if path == public:
                    return True
        return False