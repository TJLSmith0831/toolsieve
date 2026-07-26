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

This prints every client config found and how each of its servers will be
handled. Both stdio and HTTP/SSE servers move behind toolsieve. Entries are
marked:

- `✓ stdio — moves`
- `✓ http, has auth headers — moves`
- `! http, no auth headers — moves; may need a token` — **handle these, step 3.5**
- `– ... left as-is` — has neither a `command` nor a `url`; nothing to move

**3. Pick the client.** If exactly one client has servers, use it and say which
you picked. If several do, ask the user which to configure — do not configure
all of them silently.

**3.5. Walk the user through auth for every `!` server.** Do not skip this and
do not guess. A `!` means the entry is HTTP with no auth headers, which is
ambiguous by construction: it is either an open server or one the *client*
authenticated via OAuth, holding the token in its own credential store where
toolsieve cannot reach it. Migrating an OAuth-backed server without a token
produces a server that aggregates zero tools, or 401s on every call.

For each `!` server, ask the user which it is:

- **Open / no auth** (many docs servers, e.g. `mcp.mintlify.com`) — nothing to do.
- **Needs auth** — it needs a bearer token or API key of its own. Tell the user
  to issue one from that service, export it, and reference it from the config:

  ```json
  "linear": {
    "url": "https://mcp.linear.app/mcp",
    "headers": { "Authorization": "Bearer ${LINEAR_TOKEN}" }
  }
  ```

  `${VAR}` is read from the environment toolsieve runs in. If it is unset, that
  one server fails with an error naming the variable — never a silent
  unauthenticated call, and never at the expense of the user's other servers.

- **OAuth-only, no token available** — say so plainly and leave it in the client
  config. toolsieve cannot proxy an OAuth session it does not hold.

**Never write a token into the config file literally**, and never ask the user to
paste one to you. Reference an environment variable and let them set it. If the
script's output flags a header or URL that already holds a literal credential,
pass that warning on.

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
mode this skill exists to prevent. A migrated HTTP server that now shows as
failed almost always means step 3.5 was skipped or its token is wrong.

**7. Tell them to restart the client.** MCP servers are launched at client
startup; the change is not live until then.

## Notes

- **Never edit a client config by hand.** Use the script — it handles the
  per-client file layouts and makes the backups.
- **Tokens live in the environment, never in the config file.** `${VAR}` is the
  only supported way to supply one.
- **OAuth is not supported.** If a server only authenticates via an OAuth flow
  the client performed, it cannot move behind toolsieve. Say so plainly rather
  than migrating it into a permanently failing entry.
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
