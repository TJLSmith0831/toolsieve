"""One check per spec scenario, for the oauth-authentication capability.

Same no-mocks rule as test_toolsieve.py: the OAuth-gated backend is a real
MCP server over a real transport that really returns 401 + WWW-Authenticate,
because the whole point is that toolsieve speaks the real spec flow.
"""

from __future__ import annotations

import asyncio
import json
import logging
import stat
import sys
from pathlib import Path

import pytest
from mcp.shared.auth import OAuthToken

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from conftest import FAKE, browser_stand_in, free_port  # noqa: E402
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
            "sse": {"url": "https://example.test/sse"},
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

    # SSE is the other half of `is_http`, and the transport it picks is a
    # different class — one that has to accept the provider too. Nothing else
    # covers an OAuth-gated `/sse` url.
    sse_auth = agg._auth_for(servers["sse"])
    assert isinstance(sse_auth, NonInteractiveOAuth)
    Aggregator._transport(servers["sse"])._set_auth(sse_auth)  # must not raise


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
    port = free_port()
    token_dir = tmp_path / "oauth"
    browser_stand_in(monkeypatch)

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


def test_signing_in_reconnects_a_live_server_with_no_restart(tmp_path, oauth_url, monkeypatch):
    """Scenario: sign in from a second terminal while toolsieve is running.

    This is the exact sentence the failure message hands the user — "run
    `toolsieve-auth gated` to sign in. It reconnects on its own afterwards,
    with no restart." It was not true: the watcher polls config.json and .env
    mtimes, and a sign-in writes to the token store instead, so the user ran
    the command they were told to, saw "gated authorized.", and watched their
    tools stay missing — being told again, by the same message, that it should
    have worked.

    Driven through `main` rather than `authorize_server`, because the fix
    lives on the CLI's path and the point is that a real invocation is enough.
    """
    browser_stand_in(monkeypatch)
    token_dir = tmp_path / "oauth"
    cfg = write_config(tmp_path / "c.json", {"gated": {"url": oauth_url}})

    async def run():
        agg = Aggregator(cfg, token_dir=token_dir)
        await agg.start()
        try:
            assert agg.catalog.failed, "must start out unauthorized, or this proves nothing"

            # A thread, not an await: this is a second process in real life,
            # and the aggregator's watcher has to keep polling meanwhile.
            code = await asyncio.to_thread(
                main,
                ["gated"],
                config_path=cfg,
                token_dir=token_dir,
                authorize=lambda server, **kw: authorize_server(
                    server, token_dir=token_dir, port=free_port()
                ),
            )
            assert code == 0

            for _ in range(40):  # generous: the watcher polls once a second
                await asyncio.sleep(0.1)
                if not agg.catalog.failed:
                    return sorted(t.name for t in agg.catalog.tools)
            return None
        finally:
            await agg.stop()

    tools = asyncio.run(run())
    assert tools is not None, "still unavailable after signing in — no restart-free reconnect"
    assert "get_weather" in tools


def test_headless_run_prints_the_port_forward_it_needs(tmp_path, oauth_url, monkeypatch, capsys):
    """Scenario: No local browser available.

    The redirect can only come back to localhost *on this machine*, so a
    headless box needs the tunnel spelled out — with the fixed port, which is
    the whole reason the port is fixed (D7).
    """
    port = free_port()

    def no_browser_here(*a, **k):
        raise RuntimeError("no runnable browser")

    monkeypatch.setattr("webbrowser.get", no_browser_here)
    browser_stand_in(monkeypatch)

    cfg = write_config(tmp_path / "c.json", {"gated": {"url": oauth_url}})
    authorize_server(load_config(cfg)[0], token_dir=tmp_path / "oauth", port=port)

    out = capsys.readouterr().out
    assert f"ssh -L {port}:localhost:{port}" in out
    # Guidance, not a dead end: with the tunnel up the flow still completes.
    assert "gated authorized." in out


def test_expired_access_token_refreshes_without_a_human(tmp_path, oauth_url, monkeypatch):
    """Scenario: expired access token, valid refresh token.

    The path every long-lived toolsieve session takes an hour after signing
    in, and the one D16 leans hardest on: `NonInteractiveOAuth` refuses the
    two interactive steps, so if refresh did not come through inherited and
    intact, every server would degrade to "needs authorization" on the hour.
    Nothing else in this file exercises it — a fresh token never expires
    inside a test.
    """
    port = free_port()
    token_dir = tmp_path / "oauth"
    browser_stand_in(monkeypatch)
    cfg = write_config(tmp_path / "c.json", {"gated": {"url": oauth_url}})
    authorize_server(load_config(cfg)[0], token_dir=token_dir, port=port)

    async def expire() -> str:
        """Age the stored access token out, keeping the refresh token."""
        auth = NonInteractiveOAuth(mcp_url=oauth_url, token_storage=token_store(token_dir))
        tokens = await auth.token_storage_adapter.get_tokens()
        assert tokens.refresh_token, "the fake provider must issue refresh tokens"
        await auth.token_storage_adapter.set_tokens(
            tokens.model_copy(update={"expires_in": -1})
        )
        return tokens.access_token

    stale = asyncio.run(expire())

    async def run():
        agg = Aggregator(cfg, token_dir=token_dir)
        await agg.start()
        try:
            return sorted(t.name for t in agg.catalog.tools), dict(agg.catalog.failed)
        finally:
            await agg.stop()

    tools, failed = asyncio.run(run())
    assert not failed  # no browser, no AuthorizationRequiredError
    assert "get_weather" in tools

    async def stored() -> str:
        auth = NonInteractiveOAuth(mcp_url=oauth_url, token_storage=token_store(token_dir))
        return (await auth.token_storage_adapter.get_tokens()).access_token

    # Really refreshed, not merely tolerated: a new token was persisted, so the
    # next process starts from it rather than repeating the exchange.
    assert asyncio.run(stored()) != stale


def no_terminal(monkeypatch) -> None:
    """Make the picker behave the way it does with stdin not a tty.

    `EOFError` is questionary's real answer there — verified by hand against
    `questionary.checkbox(...).ask() < /dev/null`. Raised directly rather than
    driven through the real prompt because prompt_toolkit caches one global
    output object: letting it render into pytest's capture binds it to that
    test's buffer, and the *next* test to prompt writes into a closed file.
    """
    import questionary

    def refuse(*args, **kwargs):
        raise EOFError()

    monkeypatch.setattr(questionary, "checkbox", refuse)


def test_no_terminal_prints_the_commands_instead_of_crashing(
    tmp_path, oauth_url, monkeypatch, capsys
):
    """Scenario: the wizard runs where there is no tty to draw a checkbox on.

    A pipe, a provisioning script, CI. questionary raises a bare `EOFError`
    there, which is the least useful thing a user can be handed — and worse
    from `toolsieve-setup --apply`, where the migration has already succeeded
    by the time the prompt is reached. Degrade to the commands themselves.
    """
    cfg = write_config(tmp_path / "c.json", {"gated": {"url": oauth_url}})
    no_terminal(monkeypatch)

    code = main([], config_path=cfg, token_dir=tmp_path / "oauth",
                authorize=lambda s, **kw: pytest.fail("authorized without being asked"))

    assert code == 0
    assert "toolsieve-auth gated" in capsys.readouterr().out


def test_bare_force_reauthorizes_an_already_authorized_server(tmp_path, oauth_url, monkeypatch):
    """Scenario: `toolsieve-auth --force` with no server named.

    Switching accounts on every server is the only reason to pass `--force`
    without a name, and the servers it applies to are precisely the ones the
    wizard otherwise filters out for being fine. Dropping the flag silently
    left the user with "nothing to do" and no way to say what they meant.
    """
    import questionary

    token_dir = tmp_path / "oauth"
    cfg = write_config(tmp_path / "c.json", {"gated": {"url": oauth_url}})
    seed_token(oauth_url, token_dir)
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
        ["--force"],
        config_path=cfg,
        token_dir=token_dir,
        authorize=lambda server, **kw: authorized.append(server.name),
    )

    assert code == 0
    assert offered["choices"] == ["gated"]  # offered despite being authorized
    assert authorized == ["gated"]
    # Cleared first, same as the named --force path: otherwise a re-auth keeps
    # the old identity and the switch the user asked for never happens.
    assert unauthorized_servers(cfg, token_dir) == ["gated"]


def test_a_failed_sign_in_is_reported_and_does_not_stop_the_others(
    tmp_path, oauth_url, monkeypatch, capsys
):
    """Scenario: one ticked server's sign-in fails.

    Sign-ins fail for ordinary reasons — the callback port is taken, the
    browser tab was closed, the provider 500s. Each is a sentence, not a
    traceback, and each is that server's own problem: aborting the loop would
    cost the servers after it in the list a sign-in they asked for.
    """
    import questionary

    cfg = write_config(
        tmp_path / "c.json", {"gated": {"url": oauth_url}, "other": {"url": oauth_url + "/x"}}
    )
    monkeypatch.setattr(
        questionary, "checkbox", lambda *a, **k: type("P", (), {"ask": lambda s: ["gated", "other"]})()
    )
    attempted = []

    def flaky(server, **kwargs):
        attempted.append(server.name)
        if server.name == "gated":
            raise RuntimeError("callback port 8765 already in use")

    code = main([], config_path=cfg, token_dir=tmp_path / "oauth", authorize=flaky)
    captured = capsys.readouterr()

    assert attempted == ["gated", "other"]  # the failure did not abort the rest
    assert code == 1  # ...but the command still reports that something failed
    assert "callback port 8765 already in use" in captured.err
    assert "Traceback" not in captured.err


def test_named_sign_in_failure_exits_nonzero(tmp_path, capsys):
    """The same courtesy on the named path, which has no loop to protect."""
    cfg = write_config(tmp_path / "c.json", {"down": {"url": dead_url()}})

    def boom(server, **kwargs):
        raise RuntimeError("the server hung up")

    code = main(["down", "--force"], config_path=cfg, token_dir=tmp_path / "oauth", authorize=boom)

    assert code == 1
    assert "down was not authorized: the server hung up" in capsys.readouterr().err


def dead_url() -> str:
    """A url nothing is listening on — the "server is down" case.

    free_port() binds and releases, so the port is known-closed rather than
    merely unlikely to be in use.
    """
    return f"http://127.0.0.1:{free_port()}/mcp"


def test_named_run_on_an_unreachable_server_does_not_claim_authorized(tmp_path, capsys):
    """Scenario: named invocation on a server that is down.

    Three states exist — authorized, refusing us, and not answering at all —
    and only the server can tell them apart. Collapsing the third into the
    first tells someone their auth is fine at the exact moment they are
    debugging why it isn't, sending them off in the wrong direction. No
    browser opens either: a sign-in is not known to be the missing piece.
    """
    cfg = write_config(tmp_path / "c.json", {"down": {"url": dead_url()}})
    authorized = []

    code = main(
        ["down"],
        config_path=cfg,
        token_dir=tmp_path / "oauth",
        authorize=lambda server, **kw: authorized.append(server.name),
    )
    captured = capsys.readouterr()

    assert authorized == []
    assert code == 1
    assert "already authorized" not in captured.out
    assert "did not answer" in captured.err
    assert "--force" in captured.err  # the escape hatch, named where it is needed


def test_force_still_authorizes_an_unreachable_server(tmp_path):
    """`--force` stays the escape hatch: the user overrides the diagnosis."""
    cfg = write_config(tmp_path / "c.json", {"down": {"url": dead_url()}})
    authorized = []

    code = main(
        ["down", "--force"],
        config_path=cfg,
        token_dir=tmp_path / "oauth",
        authorize=lambda server, **kw: authorized.append(server.name),
    )

    assert code == 0
    assert authorized == ["down"]


@pytest.mark.parametrize("force", [[], ["--force"]])
@pytest.mark.parametrize(
    "entry",
    [
        {"command": sys.executable, "args": [FAKE, "local"]},
        {"url": "https://example.test/mcp", "headers": {"Authorization": "Bearer static"}},
    ],
    ids=["stdio", "has-headers"],
)
def test_a_server_that_does_not_use_oauth_says_so_even_under_force(
    tmp_path, capsys, entry, force
):
    """A stdio server has no OAuth to do — say that, don't imply a token exists.

    True under `--force` too: whether a server speaks OAuth at all is read
    from the config, not decided by a probe, so there is no verdict for
    `--force` to override. Skipping the check with it got as far as
    "This transport does not support auth" — a stdio transport being handed
    an OAuth provider — before telling the user anything.
    """
    cfg = write_config(tmp_path / "c.json", {"srv": entry})
    authorized = []

    code = main(
        ["srv", *force],
        config_path=cfg,
        token_dir=tmp_path / "oauth",
        authorize=lambda server, **kw: authorized.append(server.name),
    )

    assert code == 0
    assert authorized == []
    assert "does not use OAuth" in capsys.readouterr().out


def test_an_unset_url_variable_is_a_message_not_a_traceback(tmp_path, capsys):
    """An unset `${VAR}` reaches this command from deeper than the config load.

    `load_config` accepts the raw template; only `expand_env` — reached from
    inside `--force`'s token clearing and from the sign-in itself — discovers
    the variable is missing. Wrapping just the load left that as a traceback.
    """
    cfg = write_config(tmp_path / "c.json", {"gated": {"url": "${TS_UNSET_IN_THIS_TEST}"}})

    code = main(["gated", "--force"], config_path=cfg, token_dir=tmp_path / "oauth",
                authorize=lambda s, **kw: None)

    assert code == 1
    assert "TS_UNSET_IN_THIS_TEST" in capsys.readouterr().err


def test_the_refusal_does_not_print_a_library_traceback(tmp_path, oauth_url, capfd):
    """An unauthorized server is one line of ours, not a stack trace of theirs.

    `NonInteractiveOAuth`'s refusal travels out through the mcp SDK's
    `logger.exception("OAuth flow error")`, which dumped a full traceback
    directly above toolsieve's own "run `toolsieve-auth gated`" — at every
    startup, and again on every reload. Only that record is dropped, so a
    genuine OAuth failure still logs.
    """
    cfg = write_config(tmp_path / "c.json", {"gated": {"url": oauth_url}})

    async def run():
        agg = Aggregator(cfg, token_dir=tmp_path / "oauth")
        await agg.start()
        try:
            return dict(agg.catalog.failed)
        finally:
            await agg.stop()

    failed = asyncio.run(run())
    captured = capfd.readouterr()

    assert "toolsieve-auth gated" in failed["gated"]  # still diagnosed
    assert "OAuth flow error" not in captured.err + captured.out
    assert "AuthorizationRequiredError" not in captured.err + captured.out


def test_wizard_names_the_servers_that_did_not_answer(tmp_path, oauth_url, monkeypatch, capsys):
    """Scenario: wizard with one gated server and one that is down.

    Same root cause as the named case: an unreachable server must not drop
    out of the list silently, leaving the user to wonder why it was never
    offered. It is still not *offered* — we don't know it needs a sign-in —
    but it is named.
    """
    import questionary

    cfg = write_config(
        tmp_path / "c.json", {"gated": {"url": oauth_url}, "down": {"url": dead_url()}}
    )
    offered = {}

    class FakePrompt:
        def ask(self):
            return ["gated"]

    def fake_checkbox(message, choices, **kwargs):
        offered["choices"] = list(choices)
        return FakePrompt()

    monkeypatch.setattr(questionary, "checkbox", fake_checkbox)

    code = main([], config_path=cfg, token_dir=tmp_path / "oauth", authorize=lambda s, **kw: None)
    out = capsys.readouterr().out

    assert code == 0
    assert offered["choices"] == ["gated"]  # only the one actually refusing us
    assert "down" in out  # but the unreachable one is accounted for


def test_unauthorized_server_is_not_retried(tmp_path, oauth_url, caplog):
    """Scenario: an unauthorized server fails fast, without a pointless retry.

    The auth error surfaces wrapped in an anyio group, so a bare isinstance
    check misses it and the connection is retried — a second full round of
    OAuth discovery against the downstream server, on every startup and
    every reload, for a failure that is deterministic by definition.
    `find_auth_error` exists for exactly this wrapping.
    """
    cfg = write_config(tmp_path / "c.json", {"gated": {"url": oauth_url}})

    async def run():
        agg = Aggregator(cfg, token_dir=tmp_path / "oauth")
        await agg.start()
        await agg.stop()

    with caplog.at_level(logging.INFO, logger="toolsieve"):
        asyncio.run(run())

    assert "retrying once" not in caplog.text


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
