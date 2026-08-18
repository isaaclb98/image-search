"""Pytest fixtures for Playwright E2E tests.

Boots the demo server with --demo-data --no-model, seeds it via bin/seed-demo.py,
exposes a Playwright browser fixture, and tears everything down on session end.

This module is intentionally separate from tests/conftest.py because E2E tests
need a live HTTP server + Playwright, which would slow down unit tests if
imported unconditionally.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator

import pytest
import urllib.request


REPO_ROOT = Path(__file__).resolve().parents[2]
VENV_PY = REPO_ROOT / ".venv" / "bin" / "python"
SEED_SCRIPT = REPO_ROOT / "scripts" / "seed-demo.py"
SCREENSHOT_DIR = REPO_ROOT / "tests" / "e2e" / "screenshots"


def _free_port() -> int:
    """Bind to port 0, read assigned port, release. Returns the port number."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_for_server(base: str, timeout_s: float = 30.0) -> None:
    """Poll GET / until 200 or timeout."""
    deadline = time.time() + timeout_s
    last_exc: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/", timeout=2) as r:
                if r.status == 200:
                    return
        except Exception as e:
            last_exc = e
            time.sleep(0.25)
    raise RuntimeError(f"Demo server at {base} did not become ready within {timeout_s}s (last error: {last_exc})")


@pytest.fixture(scope="session")
def demo_base_url() -> Iterator[str]:
    """Boot the demo server on a free port and seed it. Yields the base URL."""
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    proc = subprocess.Popen(
        [
            str(VENV_PY),
            "-m",
            "search.dev_server",
            "--demo-data",
            "--no-model",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--demo-count",
            "8",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_server(base, timeout_s=30)
        # Seed via the canonical script. This is what we want users to invoke
        # manually too — keep the wiring honest.
        seed_proc = subprocess.run(
            [str(VENV_PY), str(SEED_SCRIPT), "--base", base],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if seed_proc.returncode != 0:
            # Non-fatal: the test will still run, just against the empty state.
            # We log instead of failing because the seed is idempotent and a
            # re-run on stale state prints warnings but exits 0.
            sys.stderr.write(f"seed-demo exited {seed_proc.returncode}\nstderr: {seed_proc.stderr}\n")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="session")
def screenshot_dir() -> Path:
    """Where E2E tests save screenshots. Created on first use."""
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    return SCREENSHOT_DIR


@pytest.fixture()
def browser(demo_base_url: str):
    """Lazy-import Playwright so unit tests don't pay the import cost."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        try:
            yield b
        finally:
            b.close()


@pytest.fixture()
def context(browser, request):
    """A fresh browser context per test for isolation."""
    from playwright.sync_api import sync_playwright
    viewport_name = getattr(request, "param", "desktop")
    viewport = {"desktop": {"width": 1440, "height": 900}, "mobile": {"width": 390, "height": 844}}.get(viewport_name, {"width": 1440, "height": 900})
    ctx = browser.new_context(viewport=viewport)
    try:
        yield ctx
    finally:
        ctx.close()


@pytest.fixture()
def page(context):
    """A blank page within the test's context."""
    p = context.new_page()
    console_errors: list[str] = []
    page_errors: list[str] = []
    failed_requests: list[str] = []

    def on_console(msg):
        if msg.type == "error":
            console_errors.append(msg.text)

    def on_pageerror(err):
        page_errors.append(str(err))

    def on_requestfailed(req):
        failed_requests.append(f"{req.method} {req.url}")

    p.on("console", on_console)
    p.on("pageerror", on_pageerror)
    p.on("requestfailed", on_requestfailed)

    # Stash the listeners on the page so tests can inspect them.
    p._e2e_console_errors = console_errors  # type: ignore[attr-defined]
    p._e2e_page_errors = page_errors  # type: ignore[attr-defined]
    p._e2e_failed_requests = failed_requests  # type: ignore[attr-defined]

    try:
        yield p
    finally:
        p.close()
