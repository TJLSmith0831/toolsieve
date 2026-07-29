"""One check per spec scenario, for the oauth-authentication capability.

Same no-mocks rule as test_toolsieve.py: the OAuth-gated backend is a real
MCP server over a real transport that really returns 401 + WWW-Authenticate,
because the whole point is that toolsieve speaks the real spec flow.
"""

from __future__ import annotations

import asyncio
import json
import stat
import sys
import time
from pathlib import Path

import pytest
from mcp.shared.auth import OAuthToken

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from conftest import FAKE, free_port  # noqa: E402
from fake_server import SEEDED_TOKEN  # noqa: E402
from toolsieve.aggregator import Aggregator  # noqa: E402
from toolsieve.auth_cli import (  # noqa: E402
    authorize_server,
    clear_tokens,
    main,
    unauthorized_servers,
)
from toolsieve.config import load_config  # noqa: E402
from toolsieve.oauth import (  # noqa: E402
    AuthorizationRequiredError,
    NonInteractiveOAuth,
    token_store,
)


def write_config(path: Path, servers: dict) -> Path:
    path.write_text(json.dumps({"mcpServers": servers}))
    return path


# oauth_url fixture lives in conftest.py — shared with test_setup_flows.py.

# --- token storage (task 2.1, D3 as amended, D17) ------------------------------


def test_token_store_directory_is_owner_only(tmp_path):
    """Scenario: Storage directory permissions.

    Tokens sit here unencrypted, so a default umask leaving them
    world-readable is the trivially-avoidable disclosure D17 rules out.
    """
    directory = tmp_path / "oauth"
    token_store(directory)

    assert directory.is_dir()
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700


# --- the non-interactive variant (task 2.2, D16) -------------------------------


def test_non_interactive_oauth_refuses_to_open_a_browser(tmp_path, monkeypatch):
    """The live server process must never launch a browser or block (D16).

    `webbrowser.open` is booby-trapped rather than asserted-on afterwards: if
    the handler ever regresses to the interactive path, the test fails at the
    call instead of quietly passing on a machine with no browser to open.
    """
    import webbrowser

    monkeypatch.setattr(
        webbrowser, "open", lambda *a, **k: pytest.fail("opened a browser in-process")
    )
    auth = NonInteractiveOAuth(mcp_url="https://example.test/mcp", token_storage=token_store(tmp_path))

    with pytest.raises(AuthorizationRequiredError):
        asyncio.run(auth.redirect_handler("https://example.test/authorize"))


def test_non_interactive_oauth_does_not_wait_for_a_callback(tmp_path):
    """The callback server would block for `callback_timeout` seconds (D16)."""
    auth = NonInteractiveOAuth(mcp_url="https://example.test/mcp", token_storage=token_store(tmp_path))

    with pytest.raises(AuthorizationRequiredError):
        asyncio.run(auth.callback_handler())


def test_registers_as_a_public_client(tmp_path, oauth_url):
    """Scenario: dynamic client registration declares a public (no-secret) client.

    toolsieve is a CLI process — it cannot keep a client_secret confidential,
    it would just sit in a plaintext token file next to the access token
    itself (D17). Declaring `token_endpoint_auth_method: none` at
    registration is the correct native-app declaration (RFC 8252 / OAuth
    2.1), and it also avoids a real failure: a server that defaults an
    unspecified client to `client_secret_basic` (confirmed against Linear)
    gets a token request carrying both an `Authorization: Basic` header and
    `client_id` in the body, which a spec-strict authorization server
    rejects as "multiple authentication methods".
    """
    token_dir = tmp_path / "oauth"
    cfg = write_config(tmp_path / "c.json", {"gated": {"url": oauth_url}})

    async def run():
        # Unauthorized — this fails past discovery and DCR, before the
        # interactive step NonInteractiveOAuth refuses (D16). Registration
        # has already happened by the time it fails.
        agg = Aggregator(cfg, token_dir=token_dir)
        await agg.start()
        await agg.stop()

    asyncio.run(run())

    async def registered():
        auth = NonInteractiveOAuth(mcp_url=oauth_url, token_storage=token_store(token_dir))
        return await auth.token_storage_adapter.get_client_info()

    client_info = asyncio.run(registered())
    assert client_info is not None
    assert client_info.token_endpoint_auth_method == "none"
    assert client_info.client_secret is None


# --- aggregator wiring (tasks 2.3/2.4, D6, D9, D16) ----------------------------


def test_unauthorized_server_is_isolated_and_names_the_fix(tmp_path, oauth_url):
    """Scenario: Unauthorized server at startup or reload.

    The gated server costs its own tools and nothing else, and the reason
    tells the user the one command that fixes it (D9, D16).
    """
    cfg = write_config(
        tmp_path / "c.json",
        {"local": {"command": sys.executable, "args": [FAKE, "local"]}, "gated": {"url": oauth_url}},
    )

    async def run():
        agg = Aggregator(cfg, token_dir=tmp_path / "oauth")
        await agg.start()
        try:
            return sorted({t.server for t in agg.catalog.tools}), dict(agg.catalog.failed)
        finally:
            await agg.stop()

    servers, failed = asyncio.run(run())
    assert servers == ["local"]
    assert "toolsieve-auth gated" in failed["gated"]


def test_stored_token_connects_with_no_interactive_step(tmp_path, oauth_url):
    """Scenario: Valid stored access token.

    Seeded through fastmcp's own storage adapter rather than by writing files
    directly, so the test cannot drift from the key layout the client reads.
    """
    token_dir = tmp_path / "oauth"

    async def seed():
        auth = NonInteractiveOAuth(mcp_url=oauth_url, token_storage=token_store(token_dir))
        await auth.token_storage_adapter.set_tokens(
            OAuthToken(access_token=SEEDED_TOKEN, token_type="Bearer")
        )

    asyncio.run(seed())
    cfg = write_config(tmp_path / "c.json", {"gated": {"url": oauth_url}})

    async def run():
        agg = Aggregator(cfg, token_dir=token_dir)
        await agg.start()
        try:
            return sorted(t.name for t in agg.catalog.tools), dict(agg.catalog.failed)
        finally:
            await agg.stop()

    tools, failed = asyncio.run(run())
    assert not failed
    assert "get_weather" in tools


def test_configured_headers_are_used_instead_of_oauth(tmp_path):
    """Scenario: Server with explicit headers is unaffected.

    A configured credential means the user already answered the auth
    question — toolsieve must not attach a provider that second-guesses it.
    That the header itself still reaches the server is
    test_toolsieve.py's `test_auth_header_reaches_the_downstream_server`;
    this pins the branch that decides whether OAuth enters the picture.
    """
    cfg = write_config(
        tmp_path / "c.json",
        {
            "with_headers": {
                "url": "https://example.test/mcp",
                "headers": {"Authorization": "Bearer static"},
            },
            "headerless": {"url": "https://example.test/mcp"},
            "local": {"command": sys.executable, "args": [FAKE, "local"]},
        },
    )
    servers = {s.name: s for s in load_config(cfg)}
    agg = Aggregator(cfg, token_dir=tmp_path / "oauth")

    assert agg._auth_for(servers["with_headers"]) is None
    assert agg._auth_for(servers["local"]) is None
    # Nothing has needed a token store yet, so none has been built.
    assert not (tmp_path / "oauth").exists()

    assert isinstance(agg._auth_for(servers["headerless"]), NonInteractiveOAuth)
    assert (tmp_path / "oauth").is_dir()


# --- toolsieve-auth CLI (section 3, D13-D15) -----------------------------------


def seed_token(url: str, token_dir: Path, token: str = SEEDED_TOKEN) -> None:
    """Put a usable access token in the store, the way the client will read it."""

    async def go():
        auth = NonInteractiveOAuth(mcp_url=url, token_storage=token_store(token_dir))
        await auth.token_storage_adapter.set_tokens(
            OAuthToken(access_token=token, token_type="Bearer")
        )

    asyncio.run(go())


def test_only_servers_that_really_need_auth_are_offered(tmp_path, oauth_url):
    """Scenario: Bare invocation offers a checkbox of unauthorized servers.

    Headerless is not the same as unauthorized — the server decides. The same
    config yields a different answer once a token exists, which is what stops
    the wizard offering servers that are already fine.
    """
    token_dir = tmp_path / "oauth"
    cfg = write_config(
        tmp_path / "c.json",
        {"local": {"command": sys.executable, "args": [FAKE, "local"]}, "gated": {"url": oauth_url}},
    )

    assert unauthorized_servers(cfg, token_dir) == ["gated"]

    seed_token(oauth_url, token_dir)
    assert unauthorized_servers(cfg, token_dir) == []


def test_named_run_on_an_authorized_server_does_nothing(tmp_path, oauth_url, capsys):
    """Scenario: Named invocation on an already-authorized server is a no-op.

    Re-running a command should not cost the user a surprise browser window
    (D15).
    """
    token_dir = tmp_path / "oauth"
    cfg = write_config(tmp_path / "c.json", {"gated": {"url": oauth_url}})
    seed_token(oauth_url, token_dir)
    authorized = []

    code = main(
        ["gated"],
        config_path=cfg,
        token_dir=token_dir,
        authorize=lambda server, **kw: authorized.append(server.name),
    )

    assert code == 0
    assert authorized == []
    assert "already authorized" in capsys.readouterr().out


def test_force_reauthorizes_an_authorized_server(tmp_path, oauth_url):
    """Scenario: `--force` re-authorizes despite a valid existing token.

    The stored token must be gone before the flow runs, or the switch-accounts
    case would silently keep the old identity (D15).
    """
    token_dir = tmp_path / "oauth"
    cfg = write_config(tmp_path / "c.json", {"gated": {"url": oauth_url}})
    seed_token(oauth_url, token_dir)
    authorized = []

    code = main(
        ["gated", "--force"],
        config_path=cfg,
        token_dir=token_dir,
        authorize=lambda server, **kw: authorized.append(server.name),
    )

    assert code == 0
    assert authorized == ["gated"]
    assert unauthorized_servers(cfg, token_dir) == ["gated"]  # old token cleared


def test_bare_run_authorizes_only_what_was_ticked(tmp_path, oauth_url, monkeypatch):
    """Scenario: Selecting servers in the wizard authorizes them.

    The picker is offered the unauthorized servers, and only the ticked ones
    are acted on — an unticked server is left exactly as it was (D12, D14).
    """
    import questionary

    token_dir = tmp_path / "oauth"
    cfg = write_config(
        tmp_path / "c.json",
        {
            "gated": {"url": oauth_url},
            "local": {"command": sys.executable, "args": [FAKE, "local"]},
        },
    )
    offered = {}

    class FakePrompt:
        def ask(self):
            return ["gated"]

    def fake_checkbox(message, choices, **kwargs):
        offered["choices"] = list(choices)
        return FakePrompt()

    monkeypatch.setattr(questionary, "checkbox", fake_checkbox)
    authorized = []

    code = main(
        [],
        config_path=cfg,
        token_dir=token_dir,
        authorize=lambda server, **kw: authorized.append(server.name),
    )

    assert code == 0
    assert offered["choices"] == ["gated"]  # the stdio server is never offered
    assert authorized == ["gated"]


def test_bare_run_prompts_for_nothing_when_all_are_authorized(tmp_path, oauth_url, monkeypatch):
    """Scenario: No flagged servers — don't make the user dismiss an empty list."""
    import questionary

    token_dir = tmp_path / "oauth"
    cfg = write_config(tmp_path / "c.json", {"gated": {"url": oauth_url}})
    seed_token(oauth_url, token_dir)

    monkeypatch.setattr(
        questionary, "checkbox", lambda *a, **k: pytest.fail("prompted with nothing to do")
    )

    assert main([], config_path=cfg, token_dir=token_dir, authorize=lambda s, **k: None) == 0


def test_full_authorization_round_trip_persists_a_usable_token(tmp_path, oauth_url, monkeypatch):
    """Scenario: Named invocation authorizes one server directly.

    The whole feature, end to end and unmocked: real discovery, real dynamic
    registration, a real authorization code redeemed at the real token
    endpoint, and a token that afterwards gets the *aggregator* connected.
    Only the human at the browser is stood in for — by an HTTP client that
    follows the redirect the way a browser would.
    """
    import threading

    import httpx

    port = free_port()
    token_dir = tmp_path / "oauth"

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

    cfg = write_config(tmp_path / "c.json", {"gated": {"url": oauth_url}})
    server = load_config(cfg)[0]
    authorize_server(server, token_dir=token_dir, port=port)

    # The proof is not "a file appeared" but "the server now lets us in".
    async def run():
        agg = Aggregator(cfg, token_dir=token_dir)
        await agg.start()
        try:
            return sorted(t.name for t in agg.catalog.tools), dict(agg.catalog.failed)
        finally:
            await agg.stop()

    tools, failed = asyncio.run(run())
    assert not failed
    assert "get_weather" in tools


def test_headless_run_prints_the_port_forward_it_needs(tmp_path, oauth_url, monkeypatch, capsys):
    """Scenario: No local browser available.

    The redirect can only come back to localhost *on this machine*, so a
    headless box needs the tunnel spelled out — with the fixed port, which is
    the whole reason the port is fixed (D7).
    """
    import threading

    import httpx

    port = free_port()

    def no_browser_here(*a, **k):
        raise RuntimeError("no runnable browser")

    def open_in_background(url: str) -> bool:
        def visit():
            for _ in range(100):
                try:
                    httpx.get(url, follow_redirects=True, timeout=5)
                    return
                except httpx.HTTPError:
                    time.sleep(0.1)

        threading.Thread(target=visit, daemon=True).start()
        return True

    monkeypatch.setattr("webbrowser.get", no_browser_here)
    monkeypatch.setattr("webbrowser.open", open_in_background)

    cfg = write_config(tmp_path / "c.json", {"gated": {"url": oauth_url}})
    authorize_server(load_config(cfg)[0], token_dir=tmp_path / "oauth", port=port)

    out = capsys.readouterr().out
    assert f"ssh -L {port}:localhost:{port}" in out
    # Guidance, not a dead end: with the tunnel up the flow still completes.
    assert "gated authorized." in out


def test_a_missing_config_is_a_message_not_a_traceback(tmp_path, capsys):
    """A bad config is reported, never raised (D19's spirit, as elsewhere)."""
    code = main([], config_path=tmp_path / "nope.json", token_dir=tmp_path / "oauth")

    assert code == 1
    assert "nope.json" in capsys.readouterr().err


def test_env_var_urls_agree_on_where_the_token_lives(tmp_path, oauth_url, monkeypatch):
    """A ${VAR} url must resolve identically in the CLI and the aggregator.

    Tokens are keyed by server URL, so if one side keys on the raw template
    and the other on the expanded value, a server authorizes successfully and
    then still reads as unauthorized — forever.
    """
    monkeypatch.setenv("TS_TEST_OAUTH_URL", oauth_url)
    token_dir = tmp_path / "oauth"
    cfg = write_config(tmp_path / "c.json", {"gated": {"url": "${TS_TEST_OAUTH_URL}"}})

    assert unauthorized_servers(cfg, token_dir) == ["gated"]

    # Seed under the *expanded* url, the way the aggregator stores it.
    seed_token(oauth_url, token_dir)
    assert unauthorized_servers(cfg, token_dir) == []

    # And clearing through the CLI must find that same entry again.
    clear_tokens(load_config(cfg)[0], token_dir, env_overrides={})
    assert unauthorized_servers(cfg, token_dir) == ["gated"]
