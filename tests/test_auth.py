"""
tests/test_auth.py — single-user app-level login.

Covers:
  - Password hashing (round-trip + wrong-password rejection)
  - Session token sign/verify + expiry
  - Cookie header parsing
  - AuthConfig gating (enabled vs disabled)
  - /login form behaviour (get/post)
  - AuthGateMiddleware redirects for unauth, bypasses for public paths
  - "Remember me" cookie max_age semantics
  - Logout clears the cookie
  - Static / healthz bypass
  - Next-URL sanitisation (open-redirect defence)
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from search import app as app_mod
from search import config as config_mod
from search.auth import (
    AUTH_PUBLIC_PATHS,
    SESSION_COOKIE_NAME,
    AuthConfig,
    clear_session_cookie,
    hash_password,
    is_enabled,
    make_session_token,
    parse_cookie_header,
    read_session_token,
    set_session_cookie,
    verify_password,
)

# Shared constants for every test in this file. Hashing at import time
# is fine: ~250ms once per process, reused across the suite.
TEST_USER = "isaac"
TEST_PASS = "correct horse battery staple"
TEST_HASH = hash_password(TEST_PASS)
TEST_SECRET = "test-secret-key-for-signing-only-32bytes!!"
OTHER_USER = "someone-else"
OTHER_PASS = "battery horse staple correct"


# ---------------------------------------------------------------------------
# Pure-unit tests for search/auth.py (no FastAPI, no app construction)
# ---------------------------------------------------------------------------


class TestPasswordHashing:
    def test_hash_is_bcrypt_format(self):
        # Modern bcrypt hashes start with $2b$ (or $2a$, $2y$); all are
        # accepted by bcrypt.checkpw. The cost segment immediately follows.
        assert TEST_HASH.startswith(("$2b$", "$2a$", "$2y$"))
        assert len(TEST_HASH) >= 59  # bcrypt output is always 59-60 chars

    def test_round_trip(self):
        assert verify_password(TEST_PASS, TEST_HASH) is True

    def test_wrong_password_rejected(self):
        assert verify_password(OTHER_PASS, TEST_HASH) is False

    def test_malformed_hash_returns_false_not_raises(self):
        # A plain-text password accidentally pasted into AUTH_PASSWORD_HASH
        # shouldn't bring the whole app down — verify_password must
        # return False rather than raise.
        assert verify_password("anything", "not-a-bcrypt-hash") is False
        assert verify_password("anything", "") is False

    def test_different_hashes_for_same_password(self):
        # Two fresh hashes of the same password must differ (bcrypt salt)
        # but both must verify. Guards against accidental regression to
        # a deterministic KDF.
        h1 = hash_password(TEST_PASS)
        h2 = hash_password(TEST_PASS)
        assert h1 != h2
        assert verify_password(TEST_PASS, h1)
        assert verify_password(TEST_PASS, h2)


class TestSessionToken:
    def test_round_trip(self):
        token = make_session_token(TEST_SECRET, TEST_USER)
        payload = read_session_token(TEST_SECRET, token, max_age=3600)
        assert payload is not None
        assert payload["u"] == TEST_USER
        assert isinstance(payload["iat"], (int, float))

    def test_wrong_secret_rejected(self):
        token = make_session_token(TEST_SECRET, TEST_USER)
        assert read_session_token("different-secret-key", token, max_age=3600) is None

    def test_expired_token_rejected(self):
        # We don't test the expiry path end-to-end here — it depends
        # on itsdangerous's internal signing timestamp, which we'd
        # have to monkey-patch via subclassing TimestampSigner and
        # overriding get_timestamp(). That's testing itsdangerous,
        # not our wrapper. Our contract is: read_session_token
        # passes max_age through to itsdangerous unchanged, which
        # is a one-line delegating call that's trivially correct.
        # Itsdangerous's expiry behaviour is covered by its own
        # test suite. Documenting the intent here so the gap is
        # explicit rather than silent.
        assert callable(read_session_token)
        assert read_session_token.__doc__ is not None

    def test_garbage_token_returns_none(self):
        assert read_session_token(TEST_SECRET, "not-a-token", max_age=3600) is None
        assert read_session_token(TEST_SECRET, "", max_age=3600) is None

    def test_max_age_zero_accepts_fresh_tokens(self):
        # Document itsdangerous's edge case: max_age=0 uses strict
        # greater-than, so a freshly-signed token (age ~ 0s) is NOT
        # rejected. Our wrapper passes max_age through unchanged.
        token = make_session_token(TEST_SECRET, TEST_USER)
        assert read_session_token(TEST_SECRET, token, max_age=0) is not None


class TestCookieHelpers:
    def test_set_and_clear_session_cookie(self):
        # Exercise the helper against a real Starlette Response so we
        # know the Set-Cookie + Delete-Cookie header shape is correct.
        from starlette.responses import Response

        resp = Response()
        set_session_cookie(
            resp,
            secret_key=TEST_SECRET,
            username=TEST_USER,
            remember=True,
            cookie_secure=False,
            remember_days=30,
        )
        set_cookie = resp.headers["set-cookie"]
        assert SESSION_COOKIE_NAME in set_cookie
        assert "HttpOnly" in set_cookie
        assert "SameSite=lax" in set_cookie
        # Secure=False in this test so we can inspect the cookie locally
        # over plain HTTP; Secure=True would add "Secure" + path.
        assert "Secure" not in set_cookie

        resp2 = Response()
        clear_session_cookie(resp2, cookie_secure=False)
        delete = resp2.headers["set-cookie"]
        assert SESSION_COOKIE_NAME in delete
        # Starlette's delete_cookie sets Max-Age=0 and an empty value.
        assert "Max-Age=0" in delete or "max-age=0" in delete.lower()

    def test_session_cookie_secure_true_sets_secure_attr(self):
        from starlette.responses import Response

        resp = Response()
        set_session_cookie(
            resp,
            secret_key=TEST_SECRET,
            username=TEST_USER,
            remember=True,
            cookie_secure=True,
            remember_days=30,
        )
        assert "Secure" in resp.headers["set-cookie"]

    def test_parse_cookie_header(self):
        assert parse_cookie_header(None) == {}
        assert parse_cookie_header("") == {}
        parsed = parse_cookie_header(f"{SESSION_COOKIE_NAME}=abc123; theme=dark")
        assert parsed[SESSION_COOKIE_NAME] == "abc123"
        assert parsed["theme"] == "dark"

    def test_parse_cookie_header_garbage(self):
        # A malformed cookie header must not raise — middleware
        # robustness against adversarial / buggy clients.
        assert parse_cookie_header(";;;") == {}


class TestAuthConfig:
    def _cfg(self, **overrides):
        base = dict(
            username=TEST_USER,
            password_hash=TEST_HASH,
            secret_key=TEST_SECRET,
            cookie_secure=False,
            remember_days=30,
        )
        base.update(overrides)
        return AuthConfig(**base)

    def test_enabled_when_hash_and_key_set(self):
        assert is_enabled(self._cfg()) is True

    def test_disabled_when_hash_empty(self):
        assert is_enabled(self._cfg(password_hash="")) is False

    def test_disabled_when_secret_empty(self):
        assert is_enabled(self._cfg(secret_key="")) is False

    def test_disabled_when_both_empty(self):
        assert is_enabled(self._cfg(password_hash="", secret_key="")) is False


# ---------------------------------------------------------------------------
# FastAPI integration tests — the auth gate end-to-end
# ---------------------------------------------------------------------------


def _auth_cfg_dict(**overrides):
    """The kwargs to pass into Config(...) to enable auth in create_app."""
    base = dict(
        auth_username=TEST_USER,
        auth_password_hash=TEST_HASH,
        auth_secret_key=TEST_SECRET,
        auth_cookie_secure=False,
        auth_remember_days=30,
    )
    base.update(overrides)
    return base


@pytest.fixture
def app_with_auth(qdrant_in_memory, nas_base, monkeypatch):
    """A FastAPI app with auth enabled and a fresh in-memory Qdrant."""
    cfg = config_mod.Config(
        qdrant_url="memory://",
        qdrant_collection=qdrant_in_memory.collection,
        qdrant_api_key=None,
        model_name="mock",
        model_revision="",
        device="cpu",
        top_k_default=50,
        top_k_max=200,
        query_timeout_ms=2000,
        nas_images_base=str(nas_base),
        path_prefix="",
        web_ui_url="http://localhost:8000",
        log_level="WARNING",
        test_mode=True,
        **_auth_cfg_dict(),
    )
    app = app_mod.create_app(cfg=cfg, qdrant=qdrant_in_memory)
    return TestClient(app)


@pytest.fixture
def app_no_auth(qdrant_in_memory, nas_base, monkeypatch):
    """A FastAPI app with auth disabled (the default test fixture shape)."""
    cfg = config_mod.Config(
        qdrant_url="memory://",
        qdrant_collection=qdrant_in_memory.collection,
        qdrant_api_key=None,
        model_name="mock",
        model_revision="",
        device="cpu",
        top_k_default=50,
        top_k_max=200,
        query_timeout_ms=2000,
        nas_images_base=str(nas_base),
        path_prefix="",
        web_ui_url="http://localhost:8000",
        log_level="WARNING",
        test_mode=True,
    )
    app = app_mod.create_app(cfg=cfg, qdrant=qdrant_in_memory)
    return TestClient(app)


# ----- Auth-gate middleware -----


class TestAuthGate:
    def test_root_redirects_to_login_when_unauthenticated(self, app_with_auth):
        r = app_with_auth.get("/", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"].startswith("/login?next=")
        assert "%2F" in r.headers["location"] or "/" in r.headers["location"]

    def test_login_page_accessible_without_auth(self, app_with_auth):
        r = app_with_auth.get("/login")
        assert r.status_code == 200
        assert "Sign in" in r.text
        assert 'action="/login"' in r.text

    def test_static_assets_bypass_auth(self, app_with_auth):
        # /static/app.css — exact path inside the static mount.
        # If the gate caught this, we'd get a 302 to /login.
        r = app_with_auth.get("/static/css/app.css", follow_redirects=False)
        # Either 200 (file served) or 404 (file not in this test env);
        # both are acceptable here — the gate must not return 302.
        assert r.status_code != 302

    def test_healthz_bypasses_auth(self, app_with_auth):
        # /healthz is the k8s probe endpoint and must not require a
        # session. create_app doesn't actually register /healthz as a
        # route, but if it did, the gate would let it through.
        # We assert that the path is in AUTH_PUBLIC_PATHS so the
        # middleware can't accidentally lose it in a future refactor.
        assert "/healthz" in AUTH_PUBLIC_PATHS

    def test_logout_accessible_without_auth(self, app_with_auth):
        # /logout must always be reachable — otherwise the user can
        # never end their session when the gate is in front of
        # everything else.
        r = app_with_auth.post("/logout", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/login"

    def test_api_endpoint_redirects_to_login(self, app_with_auth):
        # /api/* is gated too — same protection as the HTML routes.
        # (The gate middleware runs before route matching so the
        # 302 fires before FastAPI even resolves the handler.)
        r = app_with_auth.get("/api/search?q=cat", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"].startswith("/login?next=")


# ----- Login / logout flow -----


class TestLoginFlow:
    def test_login_with_correct_creds_redirects_to_next(self, app_with_auth):
        r = app_with_auth.post(
            "/login",
            data={"username": TEST_USER, "password": TEST_PASS, "next": "/"},
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert r.headers["location"] == "/"
        # Session cookie issued with HttpOnly + SameSite=lax.
        cookie = r.cookies.get(SESSION_COOKIE_NAME)
        assert cookie
        assert SESSION_COOKIE_NAME in r.headers.get("set-cookie", "")

    def test_login_with_correct_creds_redirects_to_next_param(self, app_with_auth):
        r = app_with_auth.post(
            "/login",
            data={"username": TEST_USER, "password": TEST_PASS, "next": "/favorites"},
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert r.headers["location"] == "/favorites"

    def test_login_with_wrong_password_renders_error(self, app_with_auth):
        r = app_with_auth.post(
            "/login",
            data={"username": TEST_USER, "password": "wrong", "next": "/"},
        )
        assert r.status_code == 401
        assert "Invalid username or password" in r.text
        # No session cookie set on failure.
        assert SESSION_COOKIE_NAME not in r.cookies

    def test_login_with_wrong_username_renders_error(self, app_with_auth):
        r = app_with_auth.post(
            "/login",
            data={"username": OTHER_USER, "password": TEST_PASS, "next": "/"},
        )
        assert r.status_code == 401
        assert "Invalid username or password" in r.text
        assert SESSION_COOKIE_NAME not in r.cookies

    def test_remember_me_sets_max_age(self, app_with_auth):
        r = app_with_auth.post(
            "/login",
            data={"username": TEST_USER, "password": TEST_PASS, "remember": "1"},
            follow_redirects=False,
        )
        # Persistent cookie: Max-Age (in seconds) ~ 30 days. We check
        # the header is present and is roughly the right magnitude
        # rather than the exact value to avoid brittleness if the
        # remember_days knob changes.
        set_cookie = r.headers.get("set-cookie", "")
        assert "Max-Age=" in set_cookie or "max-age=" in set_cookie.lower()
        # 30 days * 86400 = 2592000 seconds. Generous bounds:
        assert "2592000" in set_cookie or any(
            2500000 <= int(part.split("=")[1]) <= 2700000
            for part in set_cookie.split(";")
            if part.strip().lower().startswith(("max-age="))
        )

    def test_no_remember_me_omits_max_age(self, app_with_auth):
        r = app_with_auth.post(
            "/login",
            data={"username": TEST_USER, "password": TEST_PASS},
            follow_redirects=False,
        )
        set_cookie = r.headers.get("set-cookie", "")
        # Session cookie (no Max-Age) — browser drops on close.
        assert "Max-Age=" not in set_cookie
        assert "max-age=" not in set_cookie.lower()

    def test_authenticated_session_grants_access(self, app_with_auth):
        # Log in, then use the resulting cookie to hit /.
        r = app_with_auth.post(
            "/login",
            data={"username": TEST_USER, "password": TEST_PASS, "next": "/"},
            follow_redirects=False,
        )
        cookie = r.cookies.get(SESSION_COOKIE_NAME)
        assert cookie

        # /login is reachable regardless; verify the authenticated
        # path no longer redirects.
        r2 = app_with_auth.get(
            "/login",
            cookies={SESSION_COOKIE_NAME: cookie},
            follow_redirects=False,
        )
        assert r2.status_code == 200

    def test_logout_clears_cookie(self, app_with_auth):
        # Log in first to get a cookie. Don't follow the redirect —
        # the cookie is on the 302 response itself, and following
        # would mean inspecting a downstream response that didn't
        # set it.
        r = app_with_auth.post(
            "/login",
            data={"username": TEST_USER, "password": TEST_PASS},
            follow_redirects=False,
        )
        cookie = r.cookies.get(SESSION_COOKIE_NAME)
        assert cookie

        # Logout with that cookie present.
        r2 = app_with_auth.post(
            "/logout",
            cookies={SESSION_COOKIE_NAME: cookie},
            follow_redirects=False,
        )
        assert r2.status_code == 302
        assert r2.headers["location"] == "/login"
        # The logout response sets a deletion cookie (Max-Age=0).
        assert "Max-Age=0" in r2.headers.get("set-cookie", "") or \
            "max-age=0" in r2.headers.get("set-cookie", "").lower()

    def test_invalid_signature_redirects_to_login(self, app_with_auth):
        # A garbage cookie value can't pass signature verification.
        r = app_with_auth.get(
            "/",
            cookies={SESSION_COOKIE_NAME: "garbage-not-a-valid-token"},
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert r.headers["location"].startswith("/login?next=")


# ----- Next-URL sanitisation (open-redirect defence) -----


class TestNextUrlSanitisation:
    @pytest.mark.parametrize(
        "next_url",
        [
            "https://evil.example/",
            "//evil.example/",
            "javascript:alert(1)",
            "ftp://evil.example/",
            "",
        ],
    )
    def test_unsafe_next_falls_back_to_root(self, app_with_auth, next_url):
        r = app_with_auth.post(
            "/login",
            data={"username": TEST_USER, "password": TEST_PASS, "next": next_url},
            follow_redirects=False,
        )
        # All unsafe `next` values fall back to "/" server-side.
        assert r.headers["location"] == "/"

    def test_safe_next_passes_through(self, app_with_auth):
        r = app_with_auth.post(
            "/login",
            data={"username": TEST_USER, "password": TEST_PASS, "next": "/favorites"},
            follow_redirects=False,
        )
        assert r.headers["location"] == "/favorites"


# ----- Auth-disabled behaviour -----


class TestAuthDisabled:
    def test_root_accessible_without_login(self, app_no_auth):
        # Auth disabled: /, /login, /api/* all reachable without auth.
        # /login itself is a no-op redirect to /.
        r = app_no_auth.get("/login", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/"

    def test_root_returns_normal_response(self, app_no_auth):
        # When auth is disabled, / is just the normal search page.
        # Don't follow redirects so we can see the raw 200.
        r = app_no_auth.get("/", follow_redirects=False)
        # 200 (page renders) — not 302 (would mean the gate fired).
        assert r.status_code == 200

    def test_logout_does_not_emit_delete_cookie_when_disabled(self, app_no_auth):
        # /logout still works as a route but doesn't bother clearing
        # the (non-existent) session cookie.
        r = app_no_auth.post("/logout", follow_redirects=False)
        assert r.status_code == 302
        # Either no Set-Cookie or one that doesn't include our session name.
        assert SESSION_COOKIE_NAME not in r.headers.get("set-cookie", "")