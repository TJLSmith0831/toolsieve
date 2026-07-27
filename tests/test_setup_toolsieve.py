"""TDD for Codex CLI (TOML) support in scripts/setup_toolsieve.py. Run: uv run pytest -q"""

from __future__ import annotations

import importlib.util
import json
import sys
import tomllib
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "setup_toolsieve.py"


def load_module(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("setup_toolsieve", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass annotation resolution needs this registered
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "HOME", tmp_path)
    monkeypatch.setattr(module, "TOOLSIEVE_CONFIG", tmp_path / ".toolsieve/config.json")
    return module


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
    assert data["mcp_servers"]["toolsieve"]["command"] == "uv"

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
