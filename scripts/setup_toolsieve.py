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
import re
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
  - If your client authenticated it via OAuth, that token lives in the client,
    not in this file — toolsieve cannot reuse it. Issue the server a bearer
    token or API key and reference it from an environment variable:

      "myserver": {
        "url": "https://example.com/mcp",
        "headers": { "Authorization": "Bearer ${MYSERVER_TOKEN}" }
      }

    ${VAR} is read from the environment toolsieve runs in. An unset variable is
    a startup error naming the variable — never a silent unauthenticated call.
    Keep the token in your shell profile or secret manager, not in this file.

  After adding a token, verify with:
      TOOLSIEVE_CONFIG=%s uv run python demo.py""" % TOOLSIEVE_CONFIG


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

    existing = read_json(TOOLSIEVE_CONFIG).get("mcpServers", {})
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
