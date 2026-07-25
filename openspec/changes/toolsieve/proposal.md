## Why

Agents connected to several MCP servers get every tool from every server dumped into context on every call, wasting tokens and risking wrong picks — day-13-smart-tool-selector proved a semantic pre-filter cuts that cost 58% at equal accuracy, but that build's routing logic is bolted to a one-off OpenAI-Agents-SDK benchmark, not something anyone can actually install. This is the 50-days-of-dev Day 14 Ship Day: take that finding and ship it as a real, standalone open-source tool.

## What Changes

- New standalone MCP server, **toolsieve**, that aggregates a user's real downstream MCP servers (not a fabricated tool catalog) and semantically routes tool selection instead of dumping every tool into the client's context.
- Downstream servers are declared in a `mcpServers`-shaped config file (same shape Claude Desktop/Code already use); toolsieve watches that file and live-reloads the aggregated catalog on change — no toolsieve restart needed to pick up a newly-configured server.
- Exposes exactly two tools to the AI client: `find_tools(query, k=3)` (embedding match over each aggregated tool's own name+description, returns matches or an explicit empty result below a similarity floor) and `call_tool(server, tool_name, args)` (proxies the real call through to the owning downstream server).
- A third tool, `get_savings_report()`, plus a metadata block on every `find_tools` response, report token savings (naive all-tools cost vs. actual routed cost) live — turning day-13's one-time benchmark finding into a self-demonstrating feature.
- A failure in one downstream server (connect failure or a failed call) is isolated to that server; the rest of the aggregated catalog keeps working.
- A thin Claude Code plugin manifest wraps the MCP server for a one-command install in Claude Code specifically, in addition to working with any MCP client.
- **Ships in a new, separate GitHub repository** — not a folder inside the 50-days-of-dev monorepo — with its own MIT license, README, pinned deps, and demo GIF aggregating day-08-docs-mcp and day-09-cached-weather-mcp as the real downstream servers.

## Capabilities

### New Capabilities
- `mcp-aggregation`: connects to downstream stdio MCP servers declared in a `mcpServers`-shaped config, collects their real tool catalogs, isolates per-server connection/call failures, watches the config file and live-reloads the aggregated catalog on change.
- `smart-tool-router`: exposes `find_tools`/`call_tool`/`get_savings_report` to the AI client; embeds each aggregated tool's own name+description as the match target (no hand-authored routes, no LLM call), returns top-k matches or an explicit empty result below a similarity floor, proxies real calls through to the owning server, and tracks/reports token savings per-call and per-session.
- `claude-code-plugin`: a thin plugin manifest bundling the toolsieve MCP server for one-command install inside Claude Code.

### Modified Capabilities
(none — greenfield new repository, no existing specs' requirements change)

## Impact

- New standalone repository, Python, using the standalone `fastmcp` package (jlowin/fastmcp) for the server and for backend-connection/proxy plumbing to downstream servers, plus `semantic-router`/`fastembed` for embedding-based matching (adapted from day-13-smart-tool-selector, not its OpenAI-Agents-SDK harness).
- References day-08-docs-mcp and day-09-cached-weather-mcp from this monorepo as read-only demo downstream servers — no changes made to those days.
- No impact on existing 50-days-of-dev specs (`agent-loop`, `claude-code-handoff`, `context-management`, `grilling-integration`, `repl-interface`, `repo-doctor`, `session-management`, `tool-system`) — unrelated prior work, untouched.
- Day 14's tracker entry and README update in this monorepo point at the new repo once shipped.
