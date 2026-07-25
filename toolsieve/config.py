"""Config loading (D8).

The config file is `mcpServers`-shaped — the same shape Claude Desktop/Code use —
so entries are usually copy-pasteable from a setup the user already has.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("toolsieve.config.json")


@dataclass(frozen=True)
class ServerConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None


class ConfigError(ValueError):
    """The config file is missing, unparseable, or structurally wrong."""


def load_config(path: str | os.PathLike[str]) -> list[ServerConfig]:
    """Read a `mcpServers`-shaped config file into ServerConfigs.

    v1 is stdio-only (D8): an entry without a `command` is rejected rather than
    silently skipped, so a typo surfaces instead of quietly shrinking the catalog.
    """
    path = Path(path)
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config file is not valid JSON: {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"config root must be a JSON object: {path}")

    servers = raw.get("mcpServers")
    if servers is None:
        raise ConfigError(f"config is missing the 'mcpServers' key: {path}")
    if not isinstance(servers, dict):
        raise ConfigError(f"'mcpServers' must be a JSON object: {path}")

    out: list[ServerConfig] = []
    for name, entry in servers.items():
        if not isinstance(entry, dict):
            raise ConfigError(f"server '{name}': entry must be a JSON object")
        command = entry.get("command")
        if not command:
            raise ConfigError(
                f"server '{name}': missing 'command' (v1 supports stdio servers only)"
            )
        args = entry.get("args", [])
        if not isinstance(args, list):
            raise ConfigError(f"server '{name}': 'args' must be a list")
        out.append(
            ServerConfig(
                name=name,
                command=str(command),
                args=[str(a) for a in args],
                env=entry.get("env"),
                cwd=entry.get("cwd"),
            )
        )
    return out
