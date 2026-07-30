"""TDD for Codex CLI (TOML) support in toolsieve/setup.py. Run: uv run pytest -q"""

from __future__ import annotations

import json
import tomllib

import pytest

from toolsieve import setup as ts_setup


def load_module(monkeypatch, tmp_path):
    """Point the module's HOME/config at tmp_path; monkeypatch reverts per test."""
    monkeypatch.setattr(ts_setup, "HOME", tmp_path)
    monkeypatch.setattr(ts_setup, "TOOLSIEVE_CONFIG", tmp_path / ".toolsieve/config.json")
    return ts_setup


def test_targets_includes_codex(monkeypatch, tmp_path):
    module = load_module(monkeypatch, tmp_path)
    codex = next(t for t in module.targets() if t.key == "codex")
    assert codex.path == tmp_path / ".codex/config.toml"
    assert codex.fmt == "toml"


def test_servers_in_reads_codex_toml(monkeypatch, tmp_path):
    module = load_module(monkeypatch, tmp_path)
    codex_path = tmp_path / ".codex/config.toml"
    codex_path.parent.mkdir(parents=True)
    codex_path.write_text('[mcp_servers.foo]\ncommand = "uv"\nargs = ["run", "foo"]\n')

    servers = module.servers_in(next(t for t in module.targets() if t.key == "codex"))

    assert servers == {"foo": {"command": "uv", "args": ["run", "foo"]}}


def test_cmd_setup_apply_writes_codex_toml(monkeypatch, tmp_path):
    module = load_module(monkeypatch, tmp_path)
    codex_path = tmp_path / ".codex/config.toml"
    codex_path.parent.mkdir(parents=True)
    codex_path.write_text(
        '# codex config, hand-edited\n'
        '[mcp_servers.foo]\n'
        'command = "uv"\n'
        'args = ["run", "foo"]\n'
    )

    result = module.cmd_setup("codex", apply=True)
    assert result == 0

    written = codex_path.read_text()
    assert "# codex config, hand-edited" in written  # tomlkit preserved the comment
    data = tomllib.loads(written)
    assert "foo" not in data["mcp_servers"]  # migrated server removed
    # Portable, not pinned to a checkout — a migrated config outlives its machine (D7).
    assert data["mcp_servers"]["toolsieve"]["command"] == "uvx"
    assert data["mcp_servers"]["toolsieve"]["args"] == ["toolsieve"]

    ts_config = json.loads(module.TOOLSIEVE_CONFIG.read_text())
    assert ts_config["mcpServers"]["foo"] == {"command": "uv", "args": ["run", "foo"]}

    backup = codex_path.with_suffix(".toml.toolsieve-bak")
    assert backup.exists()
    assert "foo" in backup.read_text()


def test_cmd_setup_apply_writes_project_codex_toml(monkeypatch, tmp_path):
    """Codex reads .codex/config.toml per directory, closest to cwd winning.

    chdir is mandatory here, unlike every other test in this file: load_module()
    only patches HOME, and a cwd-relative target would otherwise resolve against
    this repo's own working directory. Into a subdir of tmp_path so cwd != HOME
    — same reason as test_setup_flows.isolate().
    """
    module = load_module(monkeypatch, tmp_path)
    project = tmp_path / "project"
    (project / ".codex").mkdir(parents=True)
    monkeypatch.chdir(project)
    codex_path = project / ".codex/config.toml"
    codex_path.write_text('# project-local codex config\n[mcp_servers.foo]\ncommand = "uv"\n')

    assert module.cmd_setup("codex-project", apply=True) == 0

    written = codex_path.read_text()
    assert "# project-local codex config" in written
    data = tomllib.loads(written)
    assert "foo" not in data["mcp_servers"]
    assert data["mcp_servers"]["toolsieve"]["command"] == "uvx"
    # The user-level ~/.codex/config.toml is a different file and stays untouched.
    assert not (tmp_path / ".codex/config.toml").exists()


def test_cmd_setup_dry_run_does_not_write_codex_toml(monkeypatch, tmp_path):
    module = load_module(monkeypatch, tmp_path)
    codex_path = tmp_path / ".codex/config.toml"
    codex_path.parent.mkdir(parents=True)
    original = '[mcp_servers.foo]\ncommand = "uv"\nargs = ["run", "foo"]\n'
    codex_path.write_text(original)

    result = module.cmd_setup("codex", apply=False)

    assert result == 0
    assert codex_path.read_text() == original
    assert not module.TOOLSIEVE_CONFIG.exists()


# --- OAuth wizard hook after --apply (task 4.1, D4/D10) ------------------------


def _codex_with(monkeypatch, tmp_path, servers_toml: str):
    """A Codex client config holding `servers_toml`, ready for --apply."""
    module = load_module(monkeypatch, tmp_path)
    codex_path = tmp_path / ".codex/config.toml"
    codex_path.parent.mkdir(parents=True)
    codex_path.write_text(servers_toml)
    return module


def test_apply_offers_the_wizard_for_flagged_servers(monkeypatch, tmp_path):
    """Scenario: Flagged servers offered after a successful apply.

    A migration is the one moment a human is already at the terminal, so the
    servers that may need signing in are offered there instead of described
    in help text the user has to act on later (D4).
    """
    module = _codex_with(
        monkeypatch, tmp_path, '[mcp_servers.gated]\nurl = "https://example.test/mcp"\n'
    )
    called = {}
    monkeypatch.setattr(
        module, "run_auth_wizard", lambda names: called.setdefault("names", list(names))
    )

    assert module.cmd_setup("codex", apply=True) == 0
    assert called["names"] == ["gated"]


def test_apply_does_not_prompt_when_nothing_is_flagged(monkeypatch, tmp_path):
    """Scenario: No flagged servers."""
    module = _codex_with(
        monkeypatch,
        tmp_path,
        '[mcp_servers.local]\ncommand = "uv"\nargs = ["run", "x"]\n',
    )
    monkeypatch.setattr(
        module,
        "run_auth_wizard",
        lambda names: pytest.fail("prompted with nothing flagged"),
    )

    assert module.cmd_setup("codex", apply=True) == 0
