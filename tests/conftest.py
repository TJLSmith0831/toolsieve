"""Shared fixtures for the toolsieve test suite."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from toolsieve import oauth as ts_oauth
from toolsieve import setup as ts_setup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

FAKE = str(Path(__file__).resolve().parent / "fake_server.py")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def browser_stand_in(monkeypatch) -> None:
    """Replace `webbrowser.open` with an HTTP client that follows the redirect.

    Every test that reaches the real interactive `OAuth` MUST call this.
    Without it the flow launches a real browser window on the developer's
    machine, and on a headless box (CI) nothing ever hits the callback, so
    the test blocks for fastmcp's full 300s `callback_timeout` before
    failing. Learned the hard way: a suite that was green locally hung for
    5m49s and failed on ubuntu-latest for exactly this reason.

    Only the human at the browser is stood in for — the discovery,
    registration, redirect and token exchange are all still real.
    """
    import threading

    import httpx

    def open_in_background(url: str) -> bool:
        def visit():
            # The callback server is only started after this returns, so wait
            # for it rather than racing it.
            for _ in range(100):
                try:
                    httpx.get(url, follow_redirects=True, timeout=5)
                    return
                except httpx.HTTPError:
                    time.sleep(0.1)

        threading.Thread(target=visit, daemon=True).start()
        return True

    monkeypatch.setattr("webbrowser.open", open_in_background)


@pytest.fixture(scope="module")
def oauth_url():
    """A real OAuth-gated MCP server on localhost for the module.

    Real 401 + WWW-Authenticate, real PRM/ASM discovery, real DCR — the spec
    flow D6 relies on, with no network egress.
    """
    port = free_port()
    proc = subprocess.Popen([sys.executable, FAKE, "gated", "--oauth", str(port)])
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"fake oauth server exited early with {proc.returncode}")
        with socket.socket() as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.1)
    else:
        proc.kill()
        raise RuntimeError("fake oauth server never came up")
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Isolate every client-dotfile target and the OAuth token dir under tmp_path.

    setup.py and oauth.py both resolve HOME-derived paths once, at import
    time, so setting the env var alone does nothing inside a single test
    process (unlike a subprocess CLI invocation) — this patches the
    already-bound module attributes directly, same as
    test_setup_toolsieve.py's load_module() has always done per-file, just
    shared so new test files don't each redefine it.

    Does not touch Path.cwd() — claude-code-project keys off the project
    directory, not HOME; use monkeypatch.chdir(tmp_path) alongside this
    fixture for scenarios that need that target isolated too.
    """
    monkeypatch.setattr(ts_setup, "HOME", tmp_path)
    monkeypatch.setattr(ts_setup, "TOOLSIEVE_CONFIG", tmp_path / ".toolsieve/config.json")
    monkeypatch.setattr(ts_oauth, "DEFAULT_TOKEN_DIR", tmp_path / ".toolsieve/oauth")
    return tmp_path
