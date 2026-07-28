"""Configure toolsieve into a coding agent's MCP setup.

Finds the client's existing MCP config, migrates its stdio `mcpServers` entries
into toolsieve's own config, and registers toolsieve with the client in their
place — so the agent sees toolsieve's three tools instead of every server's.

    uvx toolsieve-setup --list
    uvx toolsieve-setup --client claude-code --dry-run
    uvx toolsieve-setup --client claude-code --apply
    uvx toolsieve-setup --verify

Nothing is written without --apply, and every file it edits is backed up first.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import tomlkit
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

HOME = Path.home()
TOOLSIEVE_CONFIG = Path(os.environ.get("TOOLSIEVE_CONFIG", HOME / ".toolsieve/config.json"))


@dataclass(frozen=True)
class ClientTarget:
    key: str
    label: str
    path: Path
    # Where mcpServers live inside the file: () = top level, ("projects", "<cwd>") = nested
    section: tuple[str, ...] = ()
    # Key holding the servers table under `section`. None means `section` itself
    # is the servers table (Codex's `[mcp_servers.<name>]` has no wrapper key).
    servers_key: str | None = "mcpServers"
    fmt: str = "json"  # "json" or "toml"


def targets() -> list[ClientTarget]:
    """Every known client config location, whether or not it exists yet."""
    return [
        ClientTarget("claude-code", "Claude Code (user)", HOME / ".claude.json"),
        ClientTarget("claude-code-project", "Claude Code (this project)", Path.cwd() / ".mcp.json"),
        ClientTarget(
            "claude-desktop",
            "Claude Desktop",
            HOME / "Library/Application Support/Claude/claude_desktop_config.json",
        ),
        ClientTarget("cursor", "Cursor", HOME / ".cursor/mcp.json"),
        ClientTarget("windsurf", "Windsurf", HOME / ".codeium/windsurf/mcp_config.json"),
        ClientTarget("vscode", "VS Code", HOME / ".vscode/mcp.json"),
        ClientTarget(
            "codex", "Codex CLI", HOME / ".codex/config.toml",
            section=("mcp_servers",), servers_key=None, fmt="toml",
        ),
    ]


def read_config(path: Path, fmt: str) -> dict:
    try:
        text = path.read_text()
    except FileNotFoundError:
        return {}
    try:
        data = tomllib.loads(text) if fmt == "toml" else json.loads(text)
    except (json.JSONDecodeError, tomllib.TOMLDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def servers_in(target: ClientTarget) -> dict:
    node = read_config(target.path, target.fmt)
    for key in target.section:
        node = node.get(key, {}) if isinstance(node, dict) else {}
    servers = node if target.servers_key is None else node.get(target.servers_key)
    return servers if isinstance(servers, dict) else {}


def classify(entry: object) -> tuple[str, str]:
    """Sort a client's server entry into (kind, note). Kinds: stdio, http, skip.

    Both transports move behind toolsieve now (issue #1). The interesting case is
    an HTTP entry with no `headers`: that is either a genuinely open server or one
    whose token the *client* holds via OAuth, and the config cannot tell them
    apart. Migrating it is still right — it just may need a token added, so it is
    flagged rather than moved silently or refused outright.
    """
    if not isinstance(entry, dict):
        return "skip", "unrecognized entry"
    if entry.get("command") and not entry.get("url"):
        return "stdio", "stdio — moves"
    if not entry.get("url"):
        return "skip", "no 'command' and no 'url'"
    if entry.get("headers"):
        return "http", "http, has auth headers — moves"
    return "http", "http, no auth headers — moves; may need a token"


def literal_secrets(name: str, entry: dict) -> list[str]:
    """Header/url values that carry a secret inline instead of via ${VAR}.

    Copying these into toolsieve's config would fan a plaintext credential out to
    a second file on disk. Worth naming, not worth blocking on.
    """
    suspect = []
    for header, value in (entry.get("headers") or {}).items():
        if isinstance(value, str) and value.strip() and "${" not in value:
            suspect.append(f"{name}: header '{header}' holds a literal value")
    url = entry.get("url")
    if isinstance(url, str) and re.search(r"(?i)(key|token|secret)=(?!\$\{)[^&\s]+", url):
        suspect.append(f"{name}: 'url' holds a literal key in its query string")
    return suspect


AUTH_HELP = """\
Servers marked ! are HTTP with no auth headers in the client config.
  - If the server is open (many doc servers are), nothing to do.
  - If it uses OAuth, --apply offers to sign you in at the end. You can also
    run it any time:

      toolsieve-auth            # pick from the servers that need it
      toolsieve-auth myserver   # just this one

    The browser opens once; the token is stored under ~/.toolsieve/oauth/ and
    refreshed automatically after that.
  - If it takes a bearer token or API key instead, put it in a header and keep
    the value in an environment variable:

      "myserver": {
        "url": "https://example.com/mcp",
        "headers": { "Authorization": "Bearer ${MYSERVER_TOKEN}" }
      }

    ${VAR} is read from the environment toolsieve runs in. If it is unset, that
    one server fails with an error naming the variable — never a silent
    unauthenticated call, and never at the expense of your other servers.
    Keep the token in your shell profile or secret manager, not in this file.

  Either way, verify with:
      toolsieve-setup --verify"""


def toolsieve_entry() -> dict:
    # No path to a checkout: the entry has to work on any machine with uv, since
    # a migrated client config outlives the machine it was written on (D7).
    return {
        "command": "uvx",
        "args": ["toolsieve"],
        "env": {"TOOLSIEVE_CONFIG": str(TOOLSIEVE_CONFIG)},
    }


def discover() -> list[tuple[ClientTarget, dict]]:
    found = []
    for target in targets():
        if not target.path.exists():
            continue
        servers = {k: v for k, v in servers_in(target).items() if k != "toolsieve"}
        found.append((target, servers))
    return found


def cmd_list() -> int:
    found = discover()
    if not found:
        print("No MCP client configs found in any known location.")
        return 1
    needs_token = False
    for target, servers in found:
        print(f"\n{target.label}  [--client {target.key}]")
        print(f"  {target.path}")
        if not servers:
            print("  no MCP servers configured")
        for name, entry in servers.items():
            kind, note = classify(entry)
            if kind == "skip":
                print(f"  – {name}  ({note} — left as-is)")
                continue
            mark = "!" if "may need a token" in note else "✓"
            needs_token = needs_token or mark == "!"
            print(f"  {mark} {name}  ({note})")
    if needs_token:
        print(f"\n{AUTH_HELP}")
    return 0


def write_client_config(target: ClientTarget, movable: dict) -> None:
    """Remove `movable` servers from the client config and register toolsieve.

    TOML (Codex) goes through tomlkit so a hand-edited config keeps its
    comments and formatting — tomllib, used for reading, is read-only.
    """
    if target.fmt == "toml":
        doc = tomlkit.parse(target.path.read_text()) if target.path.exists() else tomlkit.document()
        node = doc
        for key in target.section:
            if key not in node:
                node[key] = tomlkit.table()
            node = node[key]
        for name in movable:
            node.pop(name, None)
        entry = tomlkit.table()
        for k, v in toolsieve_entry().items():
            entry[k] = v
        node["toolsieve"] = entry
        target.path.write_text(tomlkit.dumps(doc))
        return

    config = read_config(target.path, "json")
    node = config
    for key in target.section:
        node = node.setdefault(key, {})
    client_servers = node.setdefault(target.servers_key, {})
    for name in movable:
        client_servers.pop(name, None)
    client_servers["toolsieve"] = toolsieve_entry()
    target.path.write_text(json.dumps(config, indent=2) + "\n")


def cmd_setup(client: str, apply: bool) -> int:
    match = [(t, s) for t, s in discover() if t.key == client]
    if not match:
        print(f"No config found for --client {client}. Run --list to see what exists.")
        return 1
    target, servers = match[0]

    kinds = {k: classify(v) for k, v in servers.items()}
    movable = {k: v for k, v in servers.items() if kinds[k][0] != "skip"}
    if not movable:
        print(f"{target.label} has no servers to move behind toolsieve.")
        print("toolsieve will still be registered; add servers to its config later.")

    existing = read_config(TOOLSIEVE_CONFIG, "json").get("mcpServers", {})
    # `type` is a client-side hint; toolsieve infers transport from command/url.
    merged = {**existing, **{k: {kk: vv for kk, vv in v.items() if kk != "type"} for k, v in movable.items()}}

    print(f"\nClient:    {target.label}")
    print(f"           {target.path}")
    print(f"toolsieve: {TOOLSIEVE_CONFIG}")
    print(f"\nMoving {len(movable)} server(s) behind toolsieve:")
    for name in movable:
        print(f"  {'!' if 'may need a token' in kinds[name][1] else '✓'} {name}  ({kinds[name][1]})")
    kept = [k for k in servers if k not in movable]
    if kept:
        print(f"Leaving {len(kept)} unrecognized server(s) untouched: {', '.join(kept)}")
    print(f"\ntoolsieve config will hold {len(merged)} server(s).")

    flagged = [n for n in movable if "may need a token" in kinds[n][1]]
    if flagged:
        print(f"\n{AUTH_HELP}")
    exposed = [s for n, v in movable.items() for s in literal_secrets(n, v)]
    if exposed:
        print("\nHeads up — these carry a credential inline, which this copies to a second file:")
        for line in exposed:
            print(f"  {line}")
        print("  Consider replacing the value with ${VAR} and exporting it instead.")

    if not apply:
        print("\nDry run — nothing written. Re-run with --apply to make these changes.")
        return 0

    TOOLSIEVE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    if TOOLSIEVE_CONFIG.exists():
        shutil.copy2(TOOLSIEVE_CONFIG, TOOLSIEVE_CONFIG.with_suffix(".json.bak"))
    TOOLSIEVE_CONFIG.write_text(json.dumps({"mcpServers": merged}, indent=2) + "\n")

    backup = target.path.with_suffix(target.path.suffix + ".toolsieve-bak")
    shutil.copy2(target.path, backup)

    write_client_config(target, movable)

    print(f"\nDone. Backup of the client config: {backup}")

    # The one moment a human is already here (D4). Servers that were flagged
    # may just be open, so the wizard asks each one before offering it.
    if flagged:
        run_auth_wizard(flagged)

    print(f"Restart {target.label} to pick up the change.")
    print("Verify the catalog is non-empty first:  toolsieve-setup --verify")
    return 0


def run_auth_wizard(flagged: list[str]) -> None:
    """Offer to sign in to the just-migrated servers that turn out to need it.

    Delegates to `toolsieve-auth`'s own wizard rather than reimplementing it,
    so the migration path and the standalone command stay one behavior (D5).
    """
    from .auth_cli import authorize_server, env_for, unauthorized_servers
    from .config import load_config

    names = [n for n in unauthorized_servers(TOOLSIEVE_CONFIG) if n in flagged]
    if not names:
        return

    import questionary

    print()
    picked = questionary.checkbox("Authorize which servers now?", choices=names).ask()
    env_overrides = env_for(TOOLSIEVE_CONFIG)
    for name in picked or []:
        server = next(s for s in load_config(TOOLSIEVE_CONFIG) if s.name == name)
        authorize_server(server, env_overrides=env_overrides)


async def _verify() -> int:
    """Talk to toolsieve over stdio the way a real client would, report the catalog.

    demo.py covers this for a checkout; an installed user has no demo.py, and a
    silently empty catalog is the failure this check exists to catch (D13).
    """
    if not TOOLSIEVE_CONFIG.exists():
        print(f"No toolsieve config at {TOOLSIEVE_CONFIG}.")
        print("Run --list to find your client's servers, then --client <key> --apply.")
        return 1

    servers = read_config(TOOLSIEVE_CONFIG, "json").get("mcpServers", {})
    print(f"toolsieve config: {TOOLSIEVE_CONFIG}  ({len(servers)} server(s))")

    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "toolsieve"],
        env={**os.environ, "TOOLSIEVE_CONFIG": str(TOOLSIEVE_CONFIG)},
    )
    async with Client(transport) as client:
        report = (await client.call_tool("get_savings_report", {})).data

    unavailable = report.get("unavailable_servers") or {}
    print(f"  {report['tools_aggregated']} tools aggregated "
          f"across {len(servers) - len(unavailable)} server(s)")
    for name, reason in unavailable.items():
        print(f"  ⚠ {name} unavailable, its tools are missing: {reason}")
    # No savings number here: it is a cumulative counter, zero until something
    # actually routes, so printing it would read as "toolsieve saves nothing"
    # (D13 amended). demo.py routes first, which is why it can report one.

    if not report["tools_aggregated"]:
        print("\nEmpty catalog — find_tools will match nothing. Check the server "
              "entries above and any ${VAR} tokens they reference.")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="show every client config found")
    parser.add_argument("--client", help="client key from --list")
    parser.add_argument("--apply", action="store_true", help="write changes (default is dry run)")
    parser.add_argument("--dry-run", action="store_true", help="explicit no-op default")
    parser.add_argument("--verify", action="store_true",
                        help="connect to toolsieve and report its aggregated catalog")
    args = parser.parse_args()

    if args.verify:
        return asyncio.run(_verify())
    if args.list or not args.client:
        return cmd_list()
    return cmd_setup(args.client, apply=args.apply and not args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
