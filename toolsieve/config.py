"""Config loading (D8).

The config file is `mcpServers`-shaped — the same shape Claude Desktop/Code use —
so entries are usually copy-pasteable from a setup the user already has.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("toolsieve.config.json")

# ${VAR} references inside `url` and `headers` values — the same convention
# Claude Code's own .mcp.json uses, so an entry stays copy-pasteable.
ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True)
class ServerConfig:
    """One downstream server: stdio if it has a `command`, HTTP/SSE if a `url`."""

    name: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] | None = None

    @property
    def is_http(self) -> bool:
        return self.url is not None


class ConfigError(ValueError):
    """The config file is missing, unparseable, or structurally wrong."""


def expand_env(
    value: str, *, server: str, where: str, overrides: dict[str, str] | None = None
) -> str:
    """Substitute ${VAR} from the environment, falling back to `overrides` (a `.env`).

    A real exported variable always wins over `.env` — `.env` only fills gaps
    (GH #6), so nothing that already works via `export` changes behavior.
    An unset variable is a hard error naming both the server and the variable.
    Substituting an empty string instead would send an unauthenticated request to
    an authenticated endpoint, surfacing as a confusing 401 far from the cause.
    """

    def replace(match: re.Match[str]) -> str:
        var = match.group(1)
        resolved = os.environ.get(var)
        if resolved is None and overrides:
            resolved = overrides.get(var)
        if resolved is None:
            raise ConfigError(
                f"server '{server}': {where} references ${{{var}}}, which is not set. "
                f"Set {var} in a .env file next to the config, or export it in the "
                f"environment toolsieve runs in."
            )
        return resolved

    return ENV_REF.sub(replace, value)


def load_dotenv_file(path: str | os.PathLike[str]) -> dict[str, str]:
    """Parse a minimal `KEY=VALUE` `.env` file (GH #6).

    Missing file is not an error — same D19 spirit as a missing config; toolsieve
    just has nothing to add. ponytail: no quoting/multiline/escape support beyond
    stripping one pair of matching quotes — reach for `python-dotenv` if that ever
    turns out to matter.
    """
    path = Path(path)
    try:
        text = path.read_text()
    except FileNotFoundError:
        return {}

    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        out[key] = val
    return out


def load_config(path: str | os.PathLike[str]) -> list[ServerConfig]:
    """Read a `mcpServers`-shaped config file into ServerConfigs.

    Transport is inferred from key presence (issue #1): `command` means stdio,
    `url` means HTTP/SSE. An entry with neither is rejected rather than silently
    skipped, so a typo surfaces instead of quietly shrinking the catalog.
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
        command, url = entry.get("command"), entry.get("url")
        if command and url:
            raise ConfigError(
                f"server '{name}': has both 'command' and 'url' — pick one transport"
            )
        if not command and not url:
            raise ConfigError(f"server '{name}': needs a 'command' (stdio) or a 'url' (HTTP/SSE)")

        if url:
            headers = entry.get("headers") or {}
            if not isinstance(headers, dict):
                raise ConfigError(f"server '{name}': 'headers' must be a JSON object")
            # ${VAR} is left unexpanded here on purpose. Expansion happens at
            # connect time (aggregator._transport) so an unset variable fails
            # that one server through the existing isolation path (D13). Doing
            # it here would raise for the whole file, and one stale token would
            # blank a catalog of servers that never referenced it.
            out.append(
                ServerConfig(
                    name=name,
                    url=str(url),
                    headers={str(k): str(v) for k, v in headers.items()} or None,
                )
            )
            continue

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
