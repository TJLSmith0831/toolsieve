## 1. Repository bootstrap

- [x] 1.1 Create new public GitHub repository `toolsieve` (separate from 50-days-of-dev — D3, D7)
- [x] 1.2 Add MIT `LICENSE` file (D14)
- [x] 1.3 Scaffold `pyproject.toml` with `[tool.uv] package = false`, deps: `fastmcp`, `fastembed` (D6, D9 amended — `semantic-router` dropped; matching is fastembed + numpy cosine)
- [x] 1.4 Add `.gitignore` (Python + any local data/index files)
- [x] 1.5 Add skeleton `README.md` (filled in during Ship packaging, section 6)

## 2. Downstream server aggregation (`mcp-aggregation` capability)

- [x] 2.1 Define `mcpServers`-shaped config schema/loader (D8)
- [x] 2.2 Connect to each configured downstream server via `fastmcp`'s proxy/client machinery and collect its published tool list (design.md architecture decision)
- [x] 2.3 Isolate per-server connection failures: log a warning, exclude that server's tools, keep aggregating the rest — verify against `mcp-aggregation` spec scenario "One of several configured servers is unreachable" (D13)
- [x] 2.4 Watch the config file for changes; on change, reconnect to all configured servers and atomically swap in the newly aggregated catalog — verify against spec scenario "Adding a new downstream server while running" (D8)

## 3. Smart tool router (`smart-tool-router` capability)

- [x] 3.1 Build the embedding index: embed each aggregated tool's own `name` + `description` directly, no hand-authored utterances (D9)
- [x] 3.2 Implement `find_tools(query, k=3)`: embed query, return top-k matches with owning server/description/input schema — verify against spec scenario "Query matches an aggregated tool"
- [x] 3.3 Implement the low-confidence flag + `exclude` path — verify against spec scenarios "Query matches only weakly" and "Client rejects a returned tool" (D11 amended: the floor flags, it does not reject)
- [x] 3.4 Implement `call_tool(server, tool_name, args)`: proxy the call to the owning downstream server, return its real result — verify against spec scenario "Successful proxied call" (D10)
- [x] 3.5 Implement per-call error handling when the target server has failed since the last `find_tools` — verify against spec scenario "Target server has failed" (D13)

## 4. Live savings reporting (`smart-tool-router` capability, continued)

- [x] 4.1 Implement schema-size token accounting: naive (all aggregated tool schemas) vs. actual (only the k returned) (D12)
- [x] 4.2 Attach `tokens_if_naive` / `tokens_actual` / `saved_pct` metadata to every `find_tools` response — verify against spec scenario "Per-call savings metadata"
- [x] 4.3 Implement `get_savings_report()` returning the running session total — verify against spec scenario "Session-total savings report"

## 5. Claude Code plugin wrapper (`claude-code-plugin` capability)

- [x] 5.1 Add `.claude-plugin/plugin.json` + `marketplace.json` + root `.mcp.json` bundling the toolsieve MCP server entrypoint (D5, D20 — Claude Code reads plugin MCP servers from `.mcp.json`, not inline in plugin.json)
- [x] 5.2 Verify one-command install in Claude Code surfaces the toolsieve MCP server without manual config — verify against spec scenario "Installing via Claude Code"
- [x] 5.3 Bundle a setup skill in the plugin that configures toolsieve into the user's coding agent — detect the client, migrate its existing `mcpServers` entries into toolsieve's config, register toolsieve with the client (D21)

## 6. Ship Day packaging (Ship DoD — D14)

- [x] 6.1 Build a demo config aggregating day-08-docs-mcp and day-09-cached-weather-mcp as real downstream servers (D4)
- [x] 6.2 Verify clean-clone, one-command run end to end (fresh clone → install → start → `find_tools` → `call_tool` → savings receipt)
- [x] 6.3 Commit `uv.lock` for pinned/reproducible deps
- [x] 6.4 Write the real README: what it is, why (semantic matching + live savings, positioned against the competitive scan's lexical/structural tools — D7), install, config format, example, savings receipt sample
- [x] 6.5 Record the demo GIF as a **live Claude Code session on Sonnet with auto-accepted permissions** (not `demo.py`): Claude calls `find_tools`, lands on the right tool, `call_tool` executes it for real, savings receipt printed (D22)
- [x] 6.6 Write the Day 14 LinkedIn post (the artifact itself, per this challenge's Ship DoD)
- [x] 6.7 Update this monorepo's `README.md` Day 14 tracker row to point at the new `toolsieve` repo
