"""Configure toolsieve into a coding agent's MCP setup.

Finds the client's existing MCP config, migrates its stdio `mcpServers` entries
into toolsieve's own config, and registers toolsieve with the client in their
place — so the agent sees toolsieve's three tools instead of every server's.

    uv run python scripts/setup_toolsieve.py --list
    uv run python scripts/setup_toolsieve.py --client claude-code --dry-run
    uv run python scripts/setup_toolsieve.py --client claude-code --apply

Nothing is written without --apply, and every file it edits is backed up first.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

HOME = Path.home()
REPO = Path(__file__).resolve().parents[1]
TOOLSIEVE_CONFIG = Path(os.environ.get("TOOLSIEVE_CONFIG", HOME / ".toolsieve/config.json"))


@dataclass(frozen=True)
class ClientTarget:
    key: str
    label: str
    path: Path
    # Where mcpServers live inside the file: () = top level, ("projects", "<cwd>") = nested
    section: tuple[str, ...] = ()


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
    ]


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def servers_in(target: ClientTarget) -> dict:
    node = read_json(target.path)
    for key in target.section:
        node = node.get(key, {}) if isinstance(node, dict) else {}
    servers = node.get("mcpServers") if isinstance(node, dict) else None
    return servers if isinstance(servers, dict) else {}


def is_stdio(entry: object) -> bool:
    """toolsieve v1 aggregates stdio servers only — HTTP/SSE entries stay put."""
    return isinstance(entry, dict) and bool(entry.get("command")) and not entry.get("url")


def toolsieve_entry() -> dict:
    return {
        "command": "uv",
        "args": ["run", "--directory", str(REPO), "python", "-m", "toolsieve"],
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
    for target, servers in found:
        stdio = {k: v for k, v in servers.items() if is_stdio(v)}
        other = {k: v for k, v in servers.items() if not is_stdio(v)}
        print(f"\n{target.label}  [--client {target.key}]")
        print(f"  {target.path}")
        if not servers:
            print("  no MCP servers configured")
        for name in stdio:
            print(f"  ✓ {name}  (stdio — will move behind toolsieve)")
        for name in other:
            print(f"  – {name}  (not stdio — left as-is, v1 is stdio-only)")
    return 0


def cmd_setup(client: str, apply: bool) -> int:
    match = [(t, s) for t, s in discover() if t.key == client]
    if not match:
        print(f"No config found for --client {client}. Run --list to see what exists.")
        return 1
    target, servers = match[0]

    movable = {k: v for k, v in servers.items() if is_stdio(v)}
    if not movable:
        print(f"{target.label} has no stdio servers to move behind toolsieve.")
        print("toolsieve will still be registered; add servers to its config later.")

    existing = read_json(TOOLSIEVE_CONFIG).get("mcpServers", {})
    merged = {**existing, **movable}

    print(f"\nClient:    {target.label}")
    print(f"           {target.path}")
    print(f"toolsieve: {TOOLSIEVE_CONFIG}")
    print(f"\nMoving {len(movable)} server(s) behind toolsieve:")
    for name in movable:
        print(f"  {name}")
    kept = [k for k in servers if k not in movable]
    if kept:
        print(f"Leaving {len(kept)} non-stdio server(s) untouched: {', '.join(kept)}")
    print(f"\ntoolsieve config will hold {len(merged)} server(s).")

    if not apply:
        print("\nDry run — nothing written. Re-run with --apply to make these changes.")
        return 0

    TOOLSIEVE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    if TOOLSIEVE_CONFIG.exists():
        shutil.copy2(TOOLSIEVE_CONFIG, TOOLSIEVE_CONFIG.with_suffix(".json.bak"))
    TOOLSIEVE_CONFIG.write_text(json.dumps({"mcpServers": merged}, indent=2) + "\n")

    backup = target.path.with_suffix(target.path.suffix + ".toolsieve-bak")
    shutil.copy2(target.path, backup)

    config = read_json(target.path)
    node = config
    for key in target.section:
        node = node.setdefault(key, {})
    client_servers = node.setdefault("mcpServers", {})
    for name in movable:
        client_servers.pop(name, None)
    client_servers["toolsieve"] = toolsieve_entry()
    target.path.write_text(json.dumps(config, indent=2) + "\n")

    print(f"\nDone. Backup of the client config: {backup}")
    print(f"Restart {target.label} to pick up the change.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="show every client config found")
    parser.add_argument("--client", help="client key from --list")
    parser.add_argument("--apply", action="store_true", help="write changes (default is dry run)")
    parser.add_argument("--dry-run", action="store_true", help="explicit no-op default")
    args = parser.parse_args()

    if args.list or not args.client:
        return cmd_list()
    return cmd_setup(args.client, apply=args.apply and not args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
