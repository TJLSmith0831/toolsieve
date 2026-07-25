# toolsieve — Decisions

Carried forward from `openspec/explore/day-14-ship-it.md` (grill-explore), continued here (grill-propose).

## D1: What is this project?
- **Decision**: Not a straight portfolio-polish of day-12-acp or day-13-smart-tool-selector. A new, standalone package that generalizes day-13's semantic-router pre-filtering technique into reusable middleware, rather than shipping day-13's one-off OpenAI-Agents-SDK benchmark as-is.
- **Why**: Genuine open-source stars/awareness is the goal. Day-13's router technique (embed query, embed tool example-utterances, top-k pre-filter) is the reusable, provider-agnostic core; the benchmark harness around it (OpenAI Agents SDK, `RunHooks` call-cap, etc.) is not. Day-12's ACP demo has a narrower audience and its protocol's canonical repo was recently archived/folded into A2A, undercutting long-term star growth.
- **Source**: user

## D2: Core differentiator
- **Decision**: Not a thin wrapper around the `semantic-router` package. It aggregates real downstream MCP servers (connects out to each, pulls their real `list_tools`) rather than a hardcoded fake tool catalog. Exposes a single `find_tool`-style meta-tool to the client; on a query it embeds the request, matches against the aggregated tool set using the semantic-router/fastembed approach adapted from day-13 (not day-13's OpenAI-Agents-SDK harness), returns the top-k real tools, and proxies the actual call through to whichever downstream server owns it. Tracks and reports token savings live (naive all-tools cost vs. actual routed cost).
- **Why**: A bare semantic-router wrapper is undifferentiated. The real, common pain is too many connected MCP servers flooding a client's context with every tool; routing across a *real* aggregated set (not a fabricated catalog) solves that directly and turns day-13's "58% token savings" finding into a live, self-demonstrating feature.
- **Source**: user

## D3: New repository
- **Decision**: Ships as its own new GitHub repository, not a `day-14-slug/` folder inside the 50-days-of-dev monorepo.
- **Why**: Standalone open-source package meant to gain its own stars/visibility, not a challenge-day artifact.
- **Source**: user

## D4: Demo/downstream servers
- **Decision**: The demo aggregates day-08-docs-mcp and day-09-cached-weather-mcp (already-built, real MCP servers from the 50-days-of-dev repo) as the downstream servers being routed to, rather than a fabricated tool catalog.
- **Why**: Real, working tools give an honest demo, and it's a natural callback to earlier challenge days.
- **Source**: user

## D5: Distribution shape
- **Decision**: Primary deliverable is a standalone MCP server usable by any MCP client (Claude Desktop, Claude Code, Cursor, etc.). A thin Claude Code plugin manifest wraps it on top for a one-command install specifically in Claude Code.
- **Why**: Standalone MCP server maximizes audience/star potential; the Claude Code plugin wrapper is near-zero extra cost since Claude Code plugins can bundle an MCP server (precedent: day-11-audited-agent), and taps a second ecosystem for free.
- **Source**: user

## D6: Language / SDK (amended)
- **Decision**: Python, using the standalone **`fastmcp`** package (jlowin/fastmcp — industry-standard, "the FastAPI of MCP," the project the official SDK's bundled `mcp.server.fastmcp.FastMCP` was originally upstreamed from and still developed separately, more full-featured) — not the bare `mcp` SDK directly. Use `fastmcp`'s client-side proxy/connection machinery (its `create_proxy()`/`ProxyClient` internals, whose config format is already `mcpServers`-shaped, matching D8) to manage stdio subprocess connections to each configured downstream server, collect their real tool lists, and forward calls — but build a **custom outward-facing server** exposing only `find_tools`/`call_tool`/`get_savings_report` (D10). Do **not** use `fastmcp`'s own auto-exposing composite proxy as the top-level architecture — by default it re-exposes every backend tool directly to the client (with name-prefixing), which is exactly the context-flooding problem toolsieve exists to solve.
- **Why**: `semantic-router` + `fastembed` (the routing engine adapted from day-13) is Python-only. `fastmcp` removes real, hard-to-get-right plumbing (stdio subprocess management, tool-list collection, call forwarding to N backends) that would otherwise be hand-rolled on top of the bare `mcp` SDK's `ClientSession`/`stdio_client`, while its config format happens to already match the `mcpServers` shape D8 chose independently. The downstream servers being aggregated (day-08, day-09) are TypeScript, which is fine — MCP is a wire protocol, not language-bound.
- **Source**: user (amending prior codebase-sourced decision after researching gofastmcp.com docs)

## D7: Project name — competitive landscape check
- **Decision**: Name is **`toolsieve`**.
- **Why**: A scan of github.com/punkpeye/awesome-mcp-servers found the space already crowded with tool-aggregation/gateway/proxy projects (`smart-mcp-proxy/mcpproxy-go` — BM25 lexical tool filtering + security; `Rendeverance/toolfunnel` — zero-dep funneling gateway; `MikkoParkkola/mcp-gateway` and `TheLunarCompany/lunar#mcpx` — multi-server multiplexing at scale; `rupinder2/mcp-orchestrator` — aggregation hub). None of the surveyed projects advertise **embedding-based semantic matching** (they're lexical/BM25 or purely structural aggregation) — that, plus the live token-savings receipt, remains this project's real differentiator. `toolsieve` avoids colliding with the "gateway/proxy/router" naming cluster while still signaling filtering behavior.
- **Source**: user (from options informed by codebase research — awesome-mcp-servers scan)

## D8: Non-goals, config format, and reload behavior
- **Decision**: Non-goals: no remote/HTTP downstream servers in v1 (stdio only), no auth/security layer beyond local trust, no PyPI/npm registry publish as part of Ship DoD, single client/single user (not hosted/multi-tenant), **no auto-discovery of unconfigured MCP servers**. Config file is a `mcpServers`-shaped file matching Claude Desktop/Code's own MCP config format (so entries are often copy-pasteable from what the user already has). toolsieve **watches this config file** and re-aggregates (reconnects to every configured downstream server, re-pulls tool lists) live on change — no toolsieve process restart required to pick up a newly-added downstream server.
- **Why**: Initial "no hot-reload" was too blunt — it conflated "toolsieve discovering brand-new servers on its own" (genuinely out of scope: fragile, requires scanning other clients' configs/processes) with "picking up a config edit for an already-known server" (the actual common workflow as a user's MCP setup grows, and cheap to support via a file watcher + re-run-the-aggregation-step). Splitting them keeps the real friction point in scope without taking on auto-discovery complexity.
- **Source**: user

## D9: Embedding/match target for real MCP tools
- **Decision**: Embed each aggregated tool's own `name` + `description` (from its JSON schema, as published by the downstream server) directly as the match target — no hand-authored example utterances (unlike day-13's `Route` objects), no LLM call to generate synthetic phrasing.
- **Why**: Zero manual authoring per tool, fully automatic off whatever the downstream server already publishes, keeps the dependency footprint provider-agnostic (no LLM/API key needed just to build the routing index). Tradeoff accepted: match quality depends on how well-written the downstream tool's description already is — a weakly-described tool routes weakly, which toolsieve should surface (e.g. low-confidence-match logging) rather than silently paper over.
- **Source**: user


## D10: Meta-tool interface shape
- **Decision**: Two explicit tools exposed to the client: `find_tools(query, k=3)` returns the top-k matching real tools (name, owning server, description, input schema); `call_tool(server, tool_name, args)` proxies the actual invocation straight through to the real downstream server and returns its result.
- **Why**: The router can't reliably invent valid arguments from a free-text query — that's what tool schemas exist to prevent, so the client needs to see the schema before calling. Two explicit tools also work with any MCP client regardless of support for dynamic tool-list updates (`tools/list_changed`), keeping it universally compatible per D5, versus a slicker but narrower dynamic-injection approach.
- **Source**: user

## D11: find_tools defaults and low-confidence behavior
- **Decision**: Default `k=3` (adjustable per-call). If no aggregated tool clears a similarity floor, `find_tools` returns an empty match list with an explicit "no tool matched, below threshold" message rather than forcing back the top-3 regardless of relevance.
- **Why**: k=3 gives headroom over day-13's top-2 since a real aggregated catalog (day-08 + day-09 combined) is larger than day-13's 8-tool set. Returning zero matches on a genuinely off-topic query makes D9's tradeoff (match quality depends on downstream description quality) visible and debuggable instead of silently returning irrelevant tools.
- **Source**: user

## D12: Token-savings receipt presentation
- **Decision**: Every `find_tools` response includes a metadata block (`tokens_if_naive`, `tokens_actual`, `saved_pct`) computed from the aggregated catalog's total schema size vs. what was actually returned. A third tool, `get_savings_report()`, returns the running session total.
- **Why**: Per-call visibility makes the savings tangible in every interaction, not buried in a session-end summary; the session-total tool gives a clean number for the demo GIF. Cheap to compute — schema-size arithmetic, same accounting day-13 already validated, no extra API calls needed.
- **Source**: user

## D13: Downstream server failure handling
- **Decision**: Failures are isolated per-server. A server that fails to connect at startup/reload logs a warning and is skipped; the rest of the catalog still aggregates (partial catalog beats total failure). A `call_tool` against a server that's since gone down returns a clear error naming which server failed. toolsieve as a whole never goes down because one of N downstream servers did.
- **Why**: User's choice — matches the spirit of D8's live-reload (a growing/changing MCP setup shouldn't be all-or-nothing) and keeps the tool usable even in a partially-broken environment, which is the realistic common case once someone has several MCP servers configured.
- **Source**: user

## D14: Ship DoD and license
- **Decision**: Reuses this monorepo's own Ship DoD, mapped onto the new standalone repo: clean-clone one-command run (starts toolsieve aggregating day-08 + day-09), demo GIF/screenshot (find_tools landing correctly, call_tool executing for real, savings receipt printed), real README, pinned deps (`uv.lock` committed), LinkedIn post as the artifact. License: MIT.
- **Why**: Consistency with every other Ship Day's definition of "done" in this challenge; MIT matches the open-source-stars goal and is the dominant license across the awesome-mcp-servers competitive scan (D7).
- **Source**: user

## Reminder: separate new repository (reaffirmed)
- Reaffirming D3 — toolsieve ships as its own new GitHub repository, not a folder inside 50-days-of-dev. User flagged this a second time; treat as binding for tasks.md (repo bootstrap is a real task, not "add a day-14 folder").

## D15: No spike — build directly
- **Decision**: Skip the throwaway spike task. Go straight to building the real server+client architecture.
- **Why**: The server+simultaneous-multi-client pattern is the standard, well-established architecture for the entire MCP-aggregator category (mcp-gateway, mcpproxy-go, toolfunnel, mcp-orchestrator, pluggedin-mcp-proxy all work this way) — not a novel bet worth a separate throwaway task, even though it's untested in this specific repo's Python `mcp` SDK usage before now.
- **Source**: user
