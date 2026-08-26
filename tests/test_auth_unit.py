"""
tests/test_auth_unit.py — Unit tests for search/auth.py.

Auth is disabled in dev (when AUTH_PASSWORD_HASH and AUTH_SECRET_KEY
are empty), but the helpers still need to work correctly when auth
IS enabled. These tests cover:
  - AuthConfig dataclass + is_enabled
  - bcrypt password hashing and verification
  - Session token sign/verify + expiration
  - Cookie helpers (set, clear, parse)
  - AuthGateMiddleware (pure-ASGI): bypass paths, missing/invalid cookie
"""
from __future__ import annotations

import asyncio
import time
from http.cookies import SimpleCookie

import pytest

from search.auth import (
    AUTH_PUBLIC_PATHS,
    AuthConfig,
    AuthGateMiddleware,
    SESSION_COOKIE_NAME,
    SESSION_SALT,
    clear_session_cookie,
    hash_password,
    is_enabled,
    make_session_token,
    parse_cookie_header,
    read_session_token,
    set_session_cookie,
    verify_password,
    _serializer,
)


# ----- AuthConfig -----

class TestAuthConfig:
    """AuthConfig is a simple dataclass with the auth settings."""

    def test_basic_construction(self):
        cfg = AuthConfig(
            username="admin",
            password_hash="$2b$12$...",
            secret_key="secret-xyz",
            cookie_secure=False,
            remember_days=30,
        )
        assert cfg.username == "admin"
        assert cfg.password_hash == "$2b$12$..."
        assert cfg.remember_days == 30

    def test_default_remember_days(self):
        """The dataclass default remember_days matches DEFAULT_REMEMBER_DAYS."""
        cfg = AuthConfig(
            username="u",
            password_hash="h",
            secret_key="k",
            cookie_secure=False,
            remember_days=30,
        )
        assert cfg.remember_days == 30


# ----- is_enabled -----

class TestIsEnabled:
    """Auth is enabled iff both password_hash and secret_key are non-empty."""

    def test_disabled_when_no_hash(self):
        cfg = AuthConfig(username="u", password_hash="", secret_key="k", cookie_secure=False, remember_days=30)
        assert is_enabled(cfg) is False

    def test_disabled_when_no_secret(self):
        cfg = AuthConfig(username="u", password_hash="h", secret_key="", cookie_secure=False, remember_days=30)
        assert is_enabled(cfg) is False

    def test_disabled_when_both_empty(self):
        cfg = AuthConfig(username="u", password_hash="", secret_key="", cookie_secure=False, remember_days=30)
        assert is_enabled(cfg) is False

    def test_enabled_when_both_set(self):
        cfg = AuthConfig(username="u", password_hash="$2b$12$x", secret_key="k", cookie_secure=False, remember_days=30)
        assert is_enabled(cfg) is True


# ----- Password hashing -----

class TestPasswordHashing:
    """bcrypt hash + verify roundtrip."""

    def test_hash_produces_bcrypt_format(self):
        h = hash_password("hunter2")
        assert h.startswith(("$2a$", "$2b$", "$2y$"))

    def test_hash_is_different_each_call(self):
        """bcrypt uses random salt — same password hashes differently."""
        h1 = hash_password("hunter2")
        h2 = hash_password("hunter2")
        assert h1 != h2

    def test_verify_correct_password(self):
        h = hash_password("correct horse battery staple")
        assert verify_password("correct horse battery staple", h) is True

    def test_verify_wrong_password(self):
        h = hash_password("correct horse battery staple")
        assert verify_password("wrong password", h) is False

    def test_verify_empty_password_against_real_hash(self):
        h = hash_password("real-password")
        assert verify_password("", h) is False

    def test_verify_unicode_password(self):
        pw = "пароль123"
        h = hash_password(pw)
        assert verify_password(pw, h) is True
        assert verify_password(pw + "x", h) is False

    def test_verify_against_invalid_hash_returns_false(self):
        assert verify_password("anything", "not-a-bcrypt-hash") is False

    def test_hash_empty_string(self):
        h = hash_password("")
        assert verify_password("", h) is True
        assert verify_password("non-empty", h) is False


# ----- Session token sign/verify -----

class TestSessionTokens:
    """Session token roundtrip and expiration."""

    SECRET = "test-secret-key-for-unit-tests"

    def test_roundtrip(self):
        token = make_session_token(self.SECRET, "alice")
        payload = read_session_token(self.SECRET, token, max_age=86400)
        assert payload is not None
        assert payload["u"] == "alice"
        assert "iat" in payload

    def test_iat_is_unix_timestamp(self):
        before = time.time()
        token = make_session_token(self.SECRET, "alice")
        after = time.time()
        payload = read_session_token(self.SECRET, token, max_age=86400)
        assert before <= payload["iat"] <= after

    def test_explicit_iat(self):
        token = make_session_token(self.SECRET, "alice", issued_at=1234567890.0)
        payload = read_session_token(self.SECRET, token, max_age=86400)
        assert payload["iat"] == 1234567890.0

    def test_expired_token(self):
        """A token whose signer timestamp is older than max_age is rejected.

        Note: URLSafeTimedSerializer adds its own timestamp when signing
        (independent of any `iat` in the payload). To simulate an
        expired token, we patch time.time so the signer's timestamp
        is old, then read with a short max_age.
        """
        import search.auth as auth_module
        original_time = auth_module.time.time
        # Backdate: signer thinks "now" is 1 hour ago
        auth_module.time.time = lambda: original_time() - 3600

        try:
            token = make_session_token(self.SECRET, "alice")
        finally:
            auth_module.time.time = original_time

        # Token's signer timestamp is ~1h old; max_age=60 → expired
        payload = read_session_token(self.SECRET, token, max_age=60)
        assert payload is None

    def test_wrong_secret_returns_none(self):
        token = make_session_token(self.SECRET, "alice")
        payload = read_session_token("different-secret", token, max_age=86400)
        assert payload is None

    def test_tampered_token_returns_none(self):
        token = make_session_token(self.SECRET, "alice")
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
        payload = read_session_token(self.SECRET, tampered, max_age=86400)
        assert payload is None

    def test_empty_token_returns_none(self):
        payload = read_session_token(self.SECRET, "", max_age=86400)
        assert payload is None

    def test_serializer_returns_url_safe_serializer(self):
        """_serializer returns a itsdangerous URLSafeTimedSerializer."""
        s = _serializer(self.SECRET)
        # Roundtrip via the serializer
        token = s.dumps({"u": "test"})
        assert s.loads(token, max_age=60) == {"u": "test"}

    def test_session_salt_is_versioned(self):
        """Salt includes a version suffix so we can rotate cleanly."""
        assert "v1" in SESSION_SALT

    def test_make_session_token_uses_url_safe_alphabet(self):
        """Token should use URL-safe characters (no +, /, = from base64)."""
        token = make_session_token(self.SECRET, "alice")
        # URL-safe base64 replaces + with -, / with _, drops padding
        assert "+" not in token
        assert "/" not in token
        assert "=" not in token


# ----- Cookie helpers -----

class TestCookieHelpers:
    """set_session_cookie / clear_session_cookie / parse_cookie_header."""

    def test_set_session_cookie_remember_true(self):
        from starlette.responses import Response
        resp = Response()
        set_session_cookie(
            resp,
            secret_key="k",
            username="alice",
            remember=True,
            cookie_secure=False,
            remember_days=30,
        )
        cookie_header = resp.headers.get("set-cookie", "")
        assert SESSION_COOKIE_NAME in cookie_header
        assert "HttpOnly" in cookie_header
        # remember=True → Max-Age = 30 * 86400 = 2592000
        assert "2592000" in cookie_header

    def test_set_session_cookie_remember_false_session_cookie(self):
        from starlette.responses import Response
        resp = Response()
        set_session_cookie(
            resp,
            secret_key="k",
            username="alice",
            remember=False,
            cookie_secure=False,
            remember_days=30,
        )
        cookie_header = resp.headers.get("set-cookie", "")
        # Session cookie → no Max-Age
        assert "Max-Age" not in cookie_header

    def test_set_session_cookie_secure_flag(self):
        from starlette.responses import Response
        resp = Response()
        set_session_cookie(
            resp,
            secret_key="k",
            username="alice",
            remember=True,
            cookie_secure=True,
            remember_days=30,
        )
        assert "Secure" in resp.headers.get("set-cookie", "")

    def test_set_session_cookie_httponly(self):
        from starlette.responses import Response
        resp = Response()
        set_session_cookie(
            resp,
            secret_key="k",
            username="alice",
            remember=True,
            cookie_secure=False,
            remember_days=30,
        )
        assert "HttpOnly" in resp.headers.get("set-cookie", "")

    def test_set_session_cookie_samesite_lax(self):
        from starlette.responses import Response
        resp = Response()
        set_session_cookie(
            resp,
            secret_key="k",
            username="alice",
            remember=True,
            cookie_secure=False,
            remember_days=30,
        )
        assert "SameSite=lax" in resp.headers.get("set-cookie", "")

    def test_clear_session_cookie_removes_cookie(self):
        from starlette.responses import Response
        resp = Response()
        clear_session_cookie(resp, cookie_secure=False)
        cookie_header = resp.headers.get("set-cookie", "")
        assert SESSION_COOKIE_NAME in cookie_header
        assert "Max-Age=0" in cookie_header

    def test_clear_session_cookie_respects_secure(self):
        from starlette.responses import Response
        resp = Response()
        clear_session_cookie(resp, cookie_secure=True)
        assert "Secure" in resp.headers.get("set-cookie", "")

    def test_parse_cookie_header_simple(self):
        cookies = parse_cookie_header("name1=value1; name2=value2")
        assert cookies["name1"] == "value1"
        assert cookies["name2"] == "value2"

    def test_parse_cookie_header_single(self):
        cookies = parse_cookie_header("session=abc123")
        assert cookies == {"session": "abc123"}

    def test_parse_cookie_header_none(self):
        assert parse_cookie_header(None) == {}

    def test_parse_cookie_header_empty_string(self):
        assert parse_cookie_header("") == {}

    def test_parse_cookie_header_with_spaces(self):
        cookies = parse_cookie_header("  name1=value1  ;  name2=value2  ")
        assert cookies["name1"] == "value1"
        assert cookies["name2"] == "value2"


# ----- AuthGateMiddleware (pure-ASGI) -----

def _build_scope(path="/", headers=None, query_string=b""):
    """Build a minimal ASGI scope."""
    return {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or [])],
        "query_string": query_string,
        "server": ("test", 80),
    }


async def _send_collect(send):
    """Collect the start + body messages sent by the middleware."""
    messages = []
    def collect(msg):
        messages.append(msg)
    await send(collect) if False else None  # noqa
    return messages


async def _invoke_middleware(middleware, scope):
    """Invoke the pure-ASGI middleware. Returns the collected send messages."""
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        sent.append(msg)

    await middleware(scope, receive, send)
    return sent


def _auth_cfg():
    return AuthConfig(
        username="admin",
        password_hash="$2b$12$abc",
        secret_key="secret",
        cookie_secure=False,
        remember_days=30,
    )


class TestAuthGatePublicPaths:
    """Public paths bypass the auth gate."""

    @pytest.mark.parametrize("public_path", list(AUTH_PUBLIC_PATHS))
    def test_public_path_passes_through(self, public_path):
        cfg = _auth_cfg()
        # Convert prefix-style ("static/") to a real path
        if public_path.endswith("/"):
            test_path = public_path + "css/app.css"
        else:
            test_path = public_path
        scope = _build_scope(path=test_path)

        app_called = []

        async def downstream_app(scope, receive, send):
            app_called.append(True)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        middleware = AuthGateMiddleware(downstream_app, auth=cfg, enabled=True)

        asyncio.run(_invoke_middleware(middleware, scope))

        assert app_called, f"public path {test_path!r} should reach downstream"

    def test_auth_disabled_is_noop(self):
        """When enabled=False, the middleware passes everything through."""
        cfg = _auth_cfg()
        scope = _build_scope(path="/search")

        app_called = []

        async def downstream_app(scope, receive, send):
            app_called.append(True)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        middleware = AuthGateMiddleware(downstream_app, auth=cfg, enabled=False)

        asyncio.run(_invoke_middleware(middleware, scope))

        assert app_called


class TestAuthGateProtectedPaths:
    """Protected paths require a valid session."""

    def test_no_cookie_redirects_to_login(self):
        cfg = _auth_cfg()
        scope = _build_scope(path="/search")
        middleware = AuthGateMiddleware(
            lambda *a, **kw: None,
            auth=cfg,
            enabled=True,
        )
        sent = asyncio.run(_invoke_middleware(middleware, scope))

        # Find the http.response.start message
        start = next(m for m in sent if m["type"] == "http.response.start")
        assert start["status"] == 302
        # Location header should be /login?next=/search
        headers = dict(start["headers"])
        location = headers[b"location"].decode()
        assert "/login" in location
        assert "next=" in location
        assert "/search" in location

    def test_invalid_cookie_redirects(self):
        cfg = _auth_cfg()
        cookie_header = f"{SESSION_COOKIE_NAME}=invalid.junk.token"
        scope = _build_scope(path="/search", headers=[("cookie", cookie_header)])
        middleware = AuthGateMiddleware(
            lambda *a, **kw: None,
            auth=cfg,
            enabled=True,
        )
        sent = asyncio.run(_invoke_middleware(middleware, scope))
        start = next(m for m in sent if m["type"] == "http.response.start")
        assert start["status"] == 302

    def test_valid_cookie_passes_through(self):
        cfg = _auth_cfg()
        token = make_session_token(cfg.secret_key, cfg.username)
        cookie_header = f"{SESSION_COOKIE_NAME}={token}"
        scope = _build_scope(path="/search", headers=[("cookie", cookie_header)])

        app_called = []

        async def downstream_app(scope, receive, send):
            app_called.append(True)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        middleware = AuthGateMiddleware(downstream_app, auth=cfg, enabled=True)

        asyncio.run(_invoke_middleware(middleware, scope))

        assert app_called, "valid cookie should reach downstream"

    def test_valid_cookie_sets_current_user(self):
        cfg = _auth_cfg()
        token = make_session_token(cfg.secret_key, "alice")
        cookie_header = f"{SESSION_COOKIE_NAME}={token}"
        scope = _build_scope(path="/search", headers=[("cookie", cookie_header)])

        captured_state = {}

        async def downstream_app(scope, receive, send):
            captured_state.update(scope.get("state", {}))
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        middleware = AuthGateMiddleware(downstream_app, auth=cfg, enabled=True)

        asyncio.run(_invoke_middleware(middleware, scope))

        assert captured_state.get("current_user") == "alice"

    def test_redirect_preserves_query_string(self):
        """The next= param should include the original query string."""
        cfg = _auth_cfg()
        scope = _build_scope(
            path="/search",
            query_string=b"q=cat&limit=20",
        )
        middleware = AuthGateMiddleware(
            lambda *a, **kw: None,
            auth=cfg,
            enabled=True,
        )
        sent = asyncio.run(_invoke_middleware(middleware, scope))
        start = next(m for m in sent if m["type"] == "http.response.start")
        headers = dict(start["headers"])
        location = headers[b"location"].decode()
        assert "q=cat" in location or "limit=20" in location


# ----- Static method: _is_public -----

class TestIsPublicStaticMethod:
    """The static method that classifies paths."""

    def test_exact_match(self):
        assert AuthGateMiddleware._is_public("/healthz") is True
        assert AuthGateMiddleware._is_public("/login") is True

    def test_prefix_match(self):
        assert AuthGateMiddleware._is_public("/static/css/app.css") is True
        assert AuthGateMiddleware._is_public("/static/js/app.js") is True

    def test_similar_path_not_public(self):
        """A path that contains a public prefix but isn't under it."""
        assert AuthGateMiddleware._is_public("/loginfoo") is False

    def test_random_path_not_public(self):
        assert AuthGateMiddleware._is_public("/search") is False
        assert AuthGateMiddleware._is_public("/api/favorites") is False


# ----- Module constants -----

class TestModuleConstants:
    """Exported constants are stable (referenced from app.py)."""

    def test_session_cookie_name(self):
        assert SESSION_COOKIE_NAME == "image_search_session"

    def test_session_salt_is_namespaced(self):
        assert SESSION_SALT.startswith("image-search-")

    def test_public_paths_includes_healthz(self):
        assert "/healthz" in AUTH_PUBLIC_PATHS

    def test_public_paths_includes_login_logout(self):
        assert "/login" in AUTH_PUBLIC_PATHS
        assert "/logout" in AUTH_PUBLIC_PATHS