---
name: toolsieve-setup
description: Set up toolsieve in the user's coding agent — find their existing MCP servers, move them behind toolsieve's semantic router, and register toolsieve with the client. Use when the user says "set up toolsieve", "configure toolsieve", "install toolsieve", "add my MCP servers to toolsieve", or asks why toolsieve shows no tools.
---

# toolsieve setup

Configures toolsieve into whichever coding agent the user runs (Claude Code,
Claude Desktop, Cursor, Devin Desktop/Windsurf, VS Code, Codex CLI, Devin CLI):
it migrates that client's existing stdio `mcpServers` entries into toolsieve's
own config, then registers toolsieve with the client in their place.

Several clients keep both a user-level and a project-level config; `--list`
shows those as separate keys (`cursor` vs `cursor-project`). Pick the one whose
path matches where the user's servers actually are — don't assume user-level.

The result: the agent loads three tools instead of every server's full catalog,
and reaches the rest through `find_tools` / `call_tool`.

## Before anything else

toolsieve with an empty config is useless — it aggregates nothing and every
`find_tools` returns no matches. **The point of this skill is getting the user's
real servers behind it.** Do not finish with an empty catalog.

## Steps

**1. Show what exists.** No checkout needed — `uvx` fetches toolsieve on demand.

```bash
uvx toolsieve-setup --list
```

This prints every client config found and how each of its servers will be
handled. Both stdio and HTTP/SSE servers move behind toolsieve. Entries are
marked:

- `✓ stdio — moves`
- `✓ http, has auth headers — moves`
- `! http, no auth headers — moves; may need a token` — **handle these, step 3**
- `– ... left as-is` — has neither a `command` nor a `url`; nothing to move

**2. Pick the client.** If exactly one client has servers, use it and say which
you picked. If several do, ask the user which to configure — do not configure
all of them silently.

**3. Handle auth for every `!` server.** A `!` means the entry is HTTP with no
auth headers, which is ambiguous by construction: it is either an open server
or one that authenticates with OAuth. You do not have to resolve that
ambiguity yourself — at the end of `--apply`, toolsieve asks each flagged
server directly and reports which ones actually need signing in. Run from a
terminal, that is a checkbox for the user to tick; run by you, there is no tty
to draw one on, so it prints the exact commands instead. Relay those to the
user and let them run them — a sign-in is theirs to complete, in their own
browser.

The same wizard is available any time, so nothing is lost if they skip it:

```bash
toolsieve-auth
```

Two cases still need you:

- **Open / no auth** (many docs servers, e.g. `mcp.mintlify.com`) — nothing to
  do; it will not appear in the wizard.
- **Takes a bearer token or API key** rather than OAuth — tell the user to
  issue one from that service, export it, and reference it from the config:

  ```json
  "linear": {
    "url": "https://mcp.linear.app/mcp",
    "headers": { "Authorization": "Bearer ${LINEAR_TOKEN}" }
  }
  ```

  `${VAR}` is read from the environment toolsieve runs in. If it is unset, that
  one server fails with an error naming the variable — never a silent
  unauthenticated call, and never at the expense of the user's other servers.

**Never write a token into the config file literally**, and never ask the user to
paste one to you. Reference an environment variable and let them set it. If the
script's output flags a header or URL that already holds a literal credential,
pass that warning on.

**4. Dry run, and show the user the plan.**

```bash
uvx toolsieve-setup --client <key> --dry-run
```

Report exactly which servers move and which stay. This is a change to the user's
working tool setup — they should see it before it happens.

**5. Apply only after the user agrees.**

```bash
uvx toolsieve-setup --client <key> --apply
```

The script backs up both files it touches (`*.toolsieve-bak`) before writing.

**6. Verify, don't assume.** Confirm the catalog is non-empty:

```bash
uvx toolsieve-setup --verify
```

Report the tool count. If the count is 0 or a server shows as failed, say so and
diagnose it — a silent empty catalog is the failure mode this skill exists to
prevent. A migrated HTTP server that now shows as failed almost always means
step 3 was skipped or its token is wrong.

`--verify` deliberately reports no savings percentage: savings accumulate per
routing call, so the number is zero until the user's agent actually calls
`find_tools`. Point them at `get_savings_report` after a session's real work.

**7. Tell them to restart the client.** MCP servers are launched at client
startup; the change is not live until then.

## Notes

- **Never edit a client config by hand.** Use the script — it handles the
  per-client file layouts and makes the backups.
- **Static tokens live in the environment, never in the config file.** `${VAR}`
  is the only supported way to supply one.
- **OAuth servers need no config at all** — just a `url`. Auth is detected from
  the server's own `401`/`WWW-Authenticate` response, so never add an
  `"auth"` key or invent one; there isn't one. Point the user at
  `toolsieve-auth` and let the browser flow do it.
- **Never run `toolsieve-auth` for the user unattended.** It opens a browser
  for them to sign in to their own account; it is theirs to run.
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
