"""TDD for Codex CLI (TOML) support in toolsieve/setup.py. Run: uv run pytest -q"""

from __future__ import annotations

import json
import tomllib

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
