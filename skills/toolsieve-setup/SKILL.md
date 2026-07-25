---
name: toolsieve-setup
description: Set up toolsieve in the user's coding agent — find their existing MCP servers, move them behind toolsieve's semantic router, and register toolsieve with the client. Use when the user says "set up toolsieve", "configure toolsieve", "install toolsieve", "add my MCP servers to toolsieve", or asks why toolsieve shows no tools.
---

# toolsieve setup

Configures toolsieve into whichever coding agent the user runs (Claude Code,
Claude Desktop, Cursor, Windsurf, VS Code): it migrates that client's existing
stdio `mcpServers` entries into toolsieve's own config, then registers toolsieve
with the client in their place.

The result: the agent loads three tools instead of every server's full catalog,
and reaches the rest through `find_tools` / `call_tool`.

## Before anything else

toolsieve with an empty config is useless — it aggregates nothing and every
`find_tools` returns no matches. **The point of this skill is getting the user's
real servers behind it.** Do not finish with an empty catalog.

## Steps

**1. Find the repo.** The setup script lives at `scripts/setup_toolsieve.py` in
the toolsieve checkout. If you don't know where that is, ask — don't guess a path.

**2. Show what exists.**

```bash
uv run python scripts/setup_toolsieve.py --list
```

This prints every client config found, and for each one which servers are stdio
(movable behind toolsieve) and which are HTTP/SSE (left alone — v1 is stdio-only,
per the project's non-goals).

**3. Pick the client.** If exactly one client has stdio servers, use it and say
which you picked. If several do, ask the user which to configure — do not
configure all of them silently.

**4. Dry run, and show the user the plan.**

```bash
uv run python scripts/setup_toolsieve.py --client <key> --dry-run
```

Report exactly which servers move and which stay. This is a change to the user's
working tool setup — they should see it before it happens.

**5. Apply only after the user agrees.**

```bash
uv run python scripts/setup_toolsieve.py --client <key> --apply
```

The script backs up both files it touches (`*.toolsieve-bak`) before writing.

**6. Verify, don't assume.** Confirm the catalog is non-empty:

```bash
TOOLSIEVE_CONFIG=~/.toolsieve/config.json uv run python demo.py
```

Report the tool count and the savings percentage. If the count is 0 or a server
shows as failed, say so and diagnose it — a silent empty catalog is the failure
mode this skill exists to prevent.

**7. Tell them to restart the client.** MCP servers are launched at client
startup; the change is not live until then.

## Notes

- **Never edit a client config by hand.** Use the script — it handles the
  per-client file layouts and makes the backups.
- **HTTP/SSE servers stay where they are.** toolsieve v1 aggregates stdio servers
  only. Say this plainly rather than appearing to have missed them.
- **Savings scale with catalog size.** Routing 3 of 4 tools saves nothing;
  3 of 15 saves ~80%. If the user migrates only one small server and is
  unimpressed, that is the reason — tell them.
- **Re-running is safe.** The script merges into any existing toolsieve config
  rather than overwriting it.

## Undo

Restore the backups the script wrote:

```bash
mv ~/.claude.json.toolsieve-bak ~/.claude.json     # or the relevant client file
```

Then restart the client.
