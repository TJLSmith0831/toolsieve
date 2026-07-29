"""User-flow integration tests for toolsieve-setup: migration, idempotency,
backups, --verify, --list, and the full new-user journey (D9, D13).

Same no-mocks rule as the rest of the suite: real client dotfiles on disk,
real stdio/HTTP fake servers, real OAuth round trips. `fake_home` (see
conftest.py) isolates every scenario under tmp_path the same way manual
testing does with `HOME=/tmp/... toolsieve-setup`.

Every scenario that calls discover()/cmd_list()/cmd_setup() must also
monkeypatch.chdir(tmp_path) — claude-code-project resolves off Path.cwd(),
not HOME, so without it a test picks up this *repo's own* .mcp.json.
Confirmed the hard way: skipping chdir() surfaced this project's real dev
config in a supposedly-empty fixture.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from toolsieve import setup as ts_setup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from conftest import browser_stand_in, free_port  # noqa: E402

FAKE = str(Path(__file__).resolve().parent / "fake_server.py")

JSON_CLIENT_KEYS = [
    "claude-code",
    "claude-code-project",
    "claude-desktop",
    "cursor",
    "windsurf",
    # vscode: exercises whatever shape targets() currently assumes
    # (top-level "mcpServers" in ~/.vscode/mcp.json). Not verified against
    # VS Code's actual current config format — that's changed across
    # versions and nothing before this suite exercised this target at all.
    # A green test here proves internal consistency, not that the shape is
    # still correct; check VS Code's current docs before trusting it.
    "vscode",
]


def isolate(monkeypatch, fake_home):
    """chdir alongside fake_home — required by every test that touches
    discover()/cmd_list()/cmd_setup() (see module docstring).

    Also sets the real $HOME env var, not just the in-process ts_setup.HOME
    attribute: _verify() spawns `python -m toolsieve` as a subprocess and
    only forwards TOOLSIEVE_CONFIG into its env, so without this the
    subprocess's own oauth.DEFAULT_TOKEN_DIR would resolve against the
    real machine's home instead of the fake one, missing whatever token a
    same-test authorize_server() call just stored.
    """
    monkeypatch.chdir(fake_home)
    monkeypatch.setenv("HOME", str(fake_home))
    return ts_setup


def write_client_json(target, servers: dict, extra: dict | None = None) -> None:
    target.path.parent.mkdir(parents=True, exist_ok=True)
    target.path.write_text(json.dumps({**(extra or {}), "mcpServers": servers}))


def target_for(key: str):
    return next(t for t in ts_setup.targets() if t.key == key)


# --- classify(): the core routing decision, tested directly -------------------


def test_classify_http_without_headers_is_flagged():
    """An HTTP entry with no headers might be open, might be OAuth-gated —
    the config can't tell them apart, so it moves but is flagged, never
    silently skipped or refused (D6)."""
    kind, note = ts_setup.classify({"url": "https://example.test/mcp"})
    assert kind == "http"
    assert "may need a token" in note


def test_classify_unrecognized_entry_is_skipped():
    kind, note = ts_setup.classify({"notes": "nothing usable here"})
    assert kind == "skip"


# --- first-time migration, per client format -----------------------------------


@pytest.mark.parametrize("client_key", JSON_CLIENT_KEYS)
def test_first_time_migration_moves_server_and_preserves_siblings(
    monkeypatch, fake_home, client_key
):
    """Scenario: fresh client, one stdio server, unrelated sibling keys.

    Siblings matter most for Claude Desktop and VS Code, which are full
    app-settings files, not MCP-only — a migration must not touch anything
    it wasn't asked to move.
    """
    module = isolate(monkeypatch, fake_home)
    target = target_for(client_key)
    write_client_json(
        target,
        {"fs": {"command": "npx", "args": ["-y", "server-filesystem"]}},
        extra={"unrelatedSetting": "keep-me"},
    )

    assert module.cmd_setup(client_key, apply=True) == 0

    written = json.loads(target.path.read_text())
    assert written["unrelatedSetting"] == "keep-me"
    assert "fs" not in written["mcpServers"]
    assert written["mcpServers"]["toolsieve"]["command"] == "uvx"

    ts_config = json.loads(module.TOOLSIEVE_CONFIG.read_text())
    assert ts_config["mcpServers"]["fs"] == {"command": "npx", "args": ["-y", "server-filesystem"]}


@pytest.mark.parametrize("client_key", JSON_CLIENT_KEYS)
def test_dry_run_writes_nothing(monkeypatch, fake_home, client_key):
    module = isolate(monkeypatch, fake_home)
    target = target_for(client_key)
    write_client_json(target, {"fs": {"command": "npx", "args": ["x"]}})
    original = target.path.read_text()

    assert module.cmd_setup(client_key, apply=False) == 0

    assert target.path.read_text() == original
    assert not module.TOOLSIEVE_CONFIG.exists()


def test_missing_client_config_is_reported_not_created(monkeypatch, fake_home, capsys):
    """Scenario: client has no config file at all.

    discover() only considers paths that already exist — toolsieve-setup
    migrates an existing client config, it does not scaffold one from
    nothing. Confirmed against the real code before writing this: the
    naive assumption ("creates one cleanly") was wrong.
    """
    module = isolate(monkeypatch, fake_home)

    assert module.cmd_setup("claude-code", apply=True) == 1
    assert "No config found for --client claude-code" in capsys.readouterr().out


# --- idempotency ----------------------------------------------------------------


def test_second_apply_is_a_clean_noop(monkeypatch, fake_home):
    module = isolate(monkeypatch, fake_home)
    target = target_for("claude-code")
    write_client_json(target, {"fs": {"command": "npx", "args": ["x"]}})

    module.cmd_setup("claude-code", apply=True)
    result = module.cmd_setup("claude-code", apply=True)

    assert result == 0
    assert list(json.loads(target.path.read_text())["mcpServers"].keys()) == ["toolsieve"]
    assert list(json.loads(module.TOOLSIEVE_CONFIG.read_text())["mcpServers"].keys()) == ["fs"]


def test_two_clients_merge_into_one_toolsieve_config(monkeypatch, fake_home):
    """Scenario: two clients on one machine, migrated independently.

    Both end up pointed at the same ~/.toolsieve/config.json (GH #6) —
    neither migration should clobber the other's servers.
    """
    module = isolate(monkeypatch, fake_home)
    write_client_json(target_for("claude-code"), {"a": {"command": "npx", "args": ["A"]}})
    write_client_json(target_for("cursor"), {"b": {"command": "npx", "args": ["B"]}})

    module.cmd_setup("claude-code", apply=True)
    module.cmd_setup("cursor", apply=True)

    merged = json.loads(module.TOOLSIEVE_CONFIG.read_text())["mcpServers"]
    assert set(merged) == {"a", "b"}


def test_name_collision_across_clients_last_applied_wins(monkeypatch, fake_home):
    """Scenario: two clients each configure a server under the same name.

    Pins the *current* actual behavior (cmd_setup's merge is
    `{**existing, **movable}`, so whichever client applies last wins) —
    this was an open question in the test plan, resolved by reading the
    code rather than assuming. Flag if this silent-overwrite isn't the
    intended semantics; this test exists so a change to it is a deliberate
    decision, not a silent regression.
    """
    module = isolate(monkeypatch, fake_home)
    write_client_json(target_for("claude-code"), {"filesystem": {"command": "npx", "args": ["A"]}})
    write_client_json(target_for("cursor"), {"filesystem": {"command": "npx", "args": ["B"]}})

    module.cmd_setup("claude-code", apply=True)
    module.cmd_setup("cursor", apply=True)

    merged = json.loads(module.TOOLSIEVE_CONFIG.read_text())["mcpServers"]
    assert merged["filesystem"]["args"] == ["B"]


# --- backups ----------------------------------------------------------------


def test_client_backup_is_byte_identical_to_pre_apply(monkeypatch, fake_home):
    module = isolate(monkeypatch, fake_home)
    target = target_for("claude-code")
    write_client_json(target, {"fs": {"command": "npx", "args": ["x"]}})
    original = target.path.read_text()

    module.cmd_setup("claude-code", apply=True)

    backup = target.path.with_suffix(target.path.suffix + ".toolsieve-bak")
    assert backup.read_text() == original


def test_second_apply_overwrites_the_backup_with_latest_pre_write_state(monkeypatch, fake_home):
    """Scenario: --apply run twice, with a manual edit in between.

    Pins the current behavior — write_client_config's backup uses
    shutil.copy2, which overwrites on every apply. The *original*,
    pre-toolsieve backup does not survive a second run; only the most
    recent pre-write state does. Open question from the test plan: is
    that acceptable, or should the first backup be preserved instead? This
    test documents what happens today either way.
    """
    module = isolate(monkeypatch, fake_home)
    target = target_for("claude-code")
    write_client_json(target, {"fs": {"command": "npx", "args": ["x"]}})
    module.cmd_setup("claude-code", apply=True)

    # Manual edit after the first migration, then a second server added.
    written = json.loads(target.path.read_text())
    written["mcpServers"]["extra"] = {"command": "npx", "args": ["y"]}
    target.path.write_text(json.dumps(written))
    before_second_apply = target.path.read_text()

    module.cmd_setup("claude-code", apply=True)

    backup = target.path.with_suffix(target.path.suffix + ".toolsieve-bak")
    assert backup.read_text() == before_second_apply


def test_toolsieve_config_itself_is_backed_up_before_overwrite(monkeypatch, fake_home):
    """Scenario: toolsieve's own config, not just the client's, is backed up.

    A different backup than the client's .toolsieve-bak — this one guards
    the merged multi-server config that a second client's migration writes
    over.
    """
    module = isolate(monkeypatch, fake_home)
    write_client_json(target_for("claude-code"), {"a": {"command": "npx", "args": ["A"]}})
    write_client_json(target_for("cursor"), {"b": {"command": "npx", "args": ["B"]}})

    module.cmd_setup("claude-code", apply=True)
    before_second = module.TOOLSIEVE_CONFIG.read_text()
    module.cmd_setup("cursor", apply=True)

    backup = module.TOOLSIEVE_CONFIG.with_suffix(".json.bak")
    assert backup.read_text() == before_second


# --- --verify ---------------------------------------------------------------


def test_verify_reports_missing_config(monkeypatch, fake_home, capsys):
    module = isolate(monkeypatch, fake_home)

    assert asyncio_run(module._verify()) == 1
    assert "No toolsieve config" in capsys.readouterr().out


def test_verify_reports_a_non_empty_catalog(monkeypatch, fake_home, capfd):
    """capfd, not capsys: _verify() spawns a real subprocess whose stderr
    defaults to sys.stderr (StdioTransport's log_file default) — capsys
    replaces sys.stderr with an object with no real fd, which the child
    process can't inherit, and the connection fails with a bare "fileno"
    error. capfd captures at the OS fd level instead, leaving sys.stderr a
    real file the subprocess can still write to.
    """
    module = isolate(monkeypatch, fake_home)
    write_client_json(
        target_for("claude-code"),
        {"local": {"command": sys.executable, "args": [FAKE, "local"]}},
    )
    module.cmd_setup("claude-code", apply=True)

    assert asyncio_run(module._verify()) == 0
    out = capfd.readouterr().out
    assert "tools aggregated" in out


def test_verify_names_an_unavailable_server(monkeypatch, fake_home, capfd):
    module = isolate(monkeypatch, fake_home)
    write_client_json(
        target_for("claude-code"),
        {"broken": {"command": sys.executable, "args": ["-c", "import sys; sys.exit(1)"]}},
    )
    module.cmd_setup("claude-code", apply=True)

    result = asyncio_run(module._verify())
    out = capfd.readouterr().out
    assert "broken unavailable" in out
    assert result == 1  # zero real tools aggregated


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


# --- --list -------------------------------------------------------------------


def test_list_reports_nothing_found(monkeypatch, fake_home, capsys):
    module = isolate(monkeypatch, fake_home)

    assert module.cmd_list() == 1
    assert "No MCP client configs found" in capsys.readouterr().out


def test_list_shows_mixed_present_and_absent_clients(monkeypatch, fake_home, capsys):
    module = isolate(monkeypatch, fake_home)
    write_client_json(target_for("claude-code"), {"fs": {"command": "npx", "args": ["x"]}})

    assert module.cmd_list() == 0
    out = capsys.readouterr().out
    assert "Claude Code (user)" in out
    assert "fs" in out


# --- full new-user journey ------------------------------------------------------


def test_full_new_user_journey(monkeypatch, fake_home, oauth_url, capfd):
    """Scenario: migrate a client with an OAuth-gated server, sign in, verify.

    The end-to-end replacement for the manual "migrate, add Linear, run
    toolsieve-auth, restart, check it worked" loop — driven against a real
    (fake, local) OAuth-gated MCP server instead of the real Linear.
    capfd, not capsys — see test_verify_reports_a_non_empty_catalog.
    """
    module = isolate(monkeypatch, fake_home)
    # Real migration would offer the interactive wizard here (D4) — stubbed
    # out since questionary needs a TTY; authorize_server is called directly
    # below instead, same as the manual "sign in later" path.
    monkeypatch.setattr(module, "run_auth_wizard", lambda names: None)
    browser_stand_in(monkeypatch)
    write_client_json(
        target_for("claude-code"),
        {
            "local": {"command": sys.executable, "args": [FAKE, "local"]},
            "gated": {"url": oauth_url},
        },
    )

    assert module.cmd_setup("claude-code", apply=True) == 0
    # Not authorized yet — the catalog is missing the gated server's tools.
    assert asyncio_run(module._verify()) == 0
    assert "gated unavailable" in capfd.readouterr().out

    from toolsieve.auth_cli import authorize_server
    from toolsieve.config import load_config

    server = next(s for s in load_config(module.TOOLSIEVE_CONFIG) if s.name == "gated")
    # A free port, not the default fixed CALLBACK_PORT — a test must not
    # depend on 8765 being free on whatever machine runs it.
    authorize_server(
        server, token_dir=module.TOOLSIEVE_CONFIG.parent / "oauth", port=free_port()
    )

    assert asyncio_run(module._verify()) == 0
    out = capfd.readouterr().out
    assert "unavailable" not in out
