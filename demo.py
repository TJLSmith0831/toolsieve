"""One-command demo: aggregate real MCP servers, route, call, print the receipt.

    uv run python demo.py

Reads TOOLSIEVE_CONFIG (default toolsieve.config.json) — the same config the
server uses. Talks to toolsieve as a real MCP client over stdio, exactly the way
Claude Code or Cursor would.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

ROOT = Path(__file__).resolve().parent
# Falls back to the bundled two-server demo so a fresh clone runs with nothing
# else installed. Savings are small on a 4-tool catalog — point TOOLSIEVE_CONFIG
# at your own servers for the real number.
CONFIG = Path(os.environ.get("TOOLSIEVE_CONFIG", ROOT / "toolsieve.config.json"))
if not CONFIG.exists() and "TOOLSIEVE_CONFIG" not in os.environ:
    CONFIG = ROOT / "toolsieve.config.demo.json"

QUERIES = [
    "what is the weather in Boston right now",
    "search my notes for a phrase",
    "look up documentation for a library",
]


def rule(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m\n" + "─" * 64)


async def main() -> int:
    if not CONFIG.exists():
        print(f"No config at {CONFIG}.")
        print("Copy toolsieve.config.example.json and point it at your MCP servers.")
        return 1

    servers = json.loads(CONFIG.read_text()).get("mcpServers", {})
    print(f"toolsieve demo — {len(servers)} server(s) configured in {CONFIG.name}")
    if CONFIG.name == "toolsieve.config.demo.json":
        print("  (bundled demo catalog — set TOOLSIEVE_CONFIG to route across your own servers)")

    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "toolsieve"],
        cwd=str(ROOT),
        env={**os.environ, "TOOLSIEVE_CONFIG": str(CONFIG)},
    )
    async with Client(transport) as client:
        exposed = sorted(t.name for t in await client.list_tools())
        report = (await client.call_tool("get_savings_report", {})).data

        rule("1. What the client actually sees")
        # Count what actually connected, not what the config asked for. Partial
        # failure is a normal, survivable state — a remote server can be down or
        # holding an expired token — so a green line here would be a lie.
        unavailable = report.get("unavailable_servers") or {}
        connected = len(servers) - len(unavailable)
        print(f"  {report['tools_aggregated']} tools aggregated across {connected} servers")
        print(f"  {len(exposed)} tools exposed: {', '.join(exposed)}")
        for name, reason in unavailable.items():
            print(f"  ⚠ {name} unavailable, its tools are missing: {reason}")

        rule("2. Semantic routing")
        for query in QUERIES:
            found = (await client.call_tool("find_tools", {"query": query})).data
            if not found["matches"]:
                print(f"  {query!r} → no matches ({found.get('message', '')})")
                continue
            top = found["matches"][0]
            flag = "  ⚠ low confidence" if top.get("confidence") == "low" else ""
            print(f"  {query!r}")
            print(f"    → {top['server']}/{top['name']}  (score {top['score']}){flag}")

        rule("3. Calling one for real")
        found = (await client.call_tool("find_tools", {"query": QUERIES[0]})).data
        if not found["matches"]:
            # An empty catalog is the normal shape of a misconfigured run — a bad
            # config, an unset ${VAR}, every backend down. Say which, don't crash.
            print(f"  nothing to call: {found.get('message', 'the catalog is empty')}")
            if found.get("config_error"):
                print(f"  config error: {found['config_error']}")
            for name, reason in (found.get("unavailable_servers") or {}).items():
                print(f"  server {name!r} unavailable: {reason}")
            return 1
        top = found["matches"][0]
        # The schema comes back with the match — that is why call_tool takes args
        # instead of guessing them from the query (D10).
        required = top["input_schema"].get("required", [])
        args = {required[0]: "Boston"} if required else {}
        print(f"  call_tool({top['server']!r}, {top['name']!r}, {args})")
        called = (await client.call_tool("call_tool", {
            "server": top["server"], "tool_name": top["name"], "args": args,
        })).data
        preview = str(called.get("result") if called["ok"] else called.get("error"))
        print(f"    ok={called['ok']}  {preview[:200].strip()}")

        rule("4. The receipt")
        final = (await client.call_tool("get_savings_report", {})).data
        print(f"  naive (every tool in context): {final['tokens_if_naive']:>7,} tokens")
        print(f"  actual (routed):               {final['tokens_actual']:>7,} tokens")
        print(f"  saved:                         {final['tokens_saved']:>7,} tokens"
              f"  → \033[1m{final['saved_pct']}%\033[0m")
        print(f"\n  {final['note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
