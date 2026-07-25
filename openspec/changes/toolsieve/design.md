## Context

day-13-smart-tool-selector proved semantic pre-filtering of tool catalogs cuts token cost ~58% at equal accuracy versus binding every tool to every call — but that finding lives inside a one-off benchmark harness wired to the OpenAI Agents SDK, hand-authored example utterances, and a fixed fake 8-tool catalog. It isn't something anyone can install.

Separately, a scan of github.com/punkpeye/awesome-mcp-servers found the MCP-aggregation/gateway space already has real, active competitors (`mcpproxy-go`, `toolfunnel`, `mcp-gateway`, `mcp-orchestrator`, `pluggedin-mcp-proxy`) — but all of them filter lexically (BM25) or structurally, not semantically. **toolsieve's gap in the market is embedding-based matching plus a live, self-reported token-savings receipt** — nobody surveyed does both.

toolsieve ships as a new, standalone Python repository (not a folder in this monorepo), aggregating real downstream MCP servers rather than a fabricated catalog, with day-08-docs-mcp and day-09-cached-weather-mcp as its demo backends.

## Goals / Non-Goals

**Goals:**
- Aggregate real downstream stdio MCP servers declared in a `mcpServers`-shaped config file.
- Route tool selection semantically (embedding match on each tool's own name + description) instead of dumping every aggregated tool into the client's context.
- Proxy real tool calls through to the owning downstream server.
- Report token savings live — per `find_tools` call and as a running session total.
- Isolate a downstream server's failure to that server; the rest of the aggregated catalog keeps working.
- Live-reload the aggregated catalog when the config file changes, without restarting toolsieve.
- Work with any MCP client; add a thin Claude Code plugin wrapper for one-command install there specifically.
- Ship as a new public GitHub repo, MIT-licensed, meeting this challenge's standard Ship DoD.

**Non-Goals:**
- Remote/HTTP downstream servers — v1 is stdio-only.
- Any auth/security layer beyond local trust (unlike `mcpproxy-go`, which bundles security).
- Publishing to PyPI/npm as part of Ship DoD — a public GitHub repo with clean install instructions is sufficient; registry publish is a later stretch goal.
- Hosted/multi-tenant operation — single client, single user.
- Auto-discovery of MCP servers not already in toolsieve's config — the user adds an entry; toolsieve doesn't scan for other clients' configs or running processes.
- Hand-authored per-tool example utterances or LLM-generated synthetic phrasing for matching — the match target is only the tool's own published name + description.
- A single "do everything" meta-tool that infers call arguments from free text, or relying on `tools/list_changed` dynamic injection — the client always sees real schemas via `find_tools` before calling `call_tool`.
- A throwaway architecture spike — the server-and-simultaneous-multi-client pattern is standard practice across the MCP-aggregator category, so this is built directly rather than prototyped first.

## Decisions

### Server/aggregation architecture: `fastmcp`, split responsibilities
Use the standalone `fastmcp` package (jlowin/fastmcp — the project the official SDK's bundled `mcp.server.fastmcp.FastMCP` was upstreamed from, still developed separately, more full-featured) for two *separate* jobs:
1. **Backend connection plumbing** — its proxy/client machinery (`create_proxy()`/`ProxyClient` internals) manages stdio subprocess connections to each configured downstream server, collects their real tool lists, and forwards calls. Its own config format is already `mcpServers`-shaped, matching this project's config choice.
2. **The outward-facing server** — a custom `FastMCP` server instance exposing exactly three tools (`find_tools`, `call_tool`, `get_savings_report`) to the AI client.

**Alternatives considered:**
- Bare `mcp` SDK with hand-rolled `ClientSession`/`stdio_client` juggling for N backends — rejected: reinvents subprocess/session management `fastmcp` already solves, more surface area for bugs.
- `fastmcp`'s own auto-exposing composite proxy (`create_proxy(config)` used directly as the top-level server) — rejected: by default it re-exposes every backend tool to the client (with name-prefixing), which is exactly the context-flooding problem this project exists to solve.

### Config format and live reload
Config is a `mcpServers`-shaped file (same shape as Claude Desktop/Code's own MCP config), so entries are often copy-pasteable from what a user already has. toolsieve watches this file; on change, it reconnects to every configured downstream server and re-pulls tool lists, atomically swapping in the new aggregated catalog only once all reachable connections succeed.

**Alternatives considered:**
- Require a full toolsieve restart to pick up config changes — rejected: bad UX for the realistic common case of a user's MCP setup growing over time.
- Auto-discover any MCP server present on the system — rejected: fragile (would require scanning other clients' configs/processes), out of scope.

### Embedding/match target
Each aggregated tool's own `name` + `description` (as published in its JSON schema by the downstream server) is embedded directly as the match target — no hand-authored `Route` objects like day-13, no LLM call to synthesize example phrasing.

**Alternatives considered:**
- Hand-authored example utterances per tool (day-13's approach) — rejected: doesn't scale to arbitrary downstream servers the user didn't write themselves.
- LLM-generated synthetic utterances for weakly-described tools — rejected: adds a hosted-LLM/API-key dependency just to build the routing index, breaking the provider-agnostic goal.
- Trade-off accepted: match quality is bounded by how well-written the downstream tool's own description is. A weak description routes weakly; toolsieve surfaces this via low-confidence logging rather than silently returning bad matches.

### Meta-tool interface: `find_tools` + `call_tool`
Two explicit tools exposed to the client. `find_tools(query, k=3)` returns the top-k matches (name, owning server, description, input schema) or an explicit empty result if nothing clears a similarity floor. `call_tool(server, tool_name, args)` proxies the real invocation.

**Alternatives considered:**
- A single tool that both finds and executes from a free-text query — rejected: the router can't reliably invent valid arguments; schemas exist precisely to prevent that guess.
- Dynamically injecting matched tools into the client's live tool list via `tools/list_changed` — rejected: only works on clients that support that notification, breaking the "works with any MCP client" goal.

### Savings reporting
Every `find_tools` response carries a metadata block (`tokens_if_naive`, `tokens_actual`, `saved_pct`) computed from the aggregated catalog's total schema size versus what was actually returned. `get_savings_report()` returns the running session total.

### Failure isolation
A downstream server that fails to connect (at startup or reload) logs a warning and is skipped; the rest of the catalog still aggregates. A `call_tool` against a server that's since failed returns a clear per-server error. toolsieve as a whole never goes down because one of N downstream servers did.

**Alternatives considered:**
- Fail-fast: refuse to start unless every configured server is reachable — rejected: too brittle for the realistic case of a partially-broken or still-starting environment.

## Risks / Trade-offs

- **[Risk]** Match quality is entirely dependent on downstream tools' own description quality (a third party's writing, not toolsieve's) → **Mitigation**: explicit low-confidence/no-match signaling instead of silently returning irrelevant tools; call this dependency out in the README.
- **[Risk]** `fastmcp` is a fast-moving third-party dependency; breaking changes between versions are plausible → **Mitigation**: pin the exact version in `uv.lock`, per the Ship DoD's pinned-deps requirement.
- **[Risk]** Config-reload racing an in-flight `call_tool` against a server being removed → **Mitigation**: catalog swap is atomic (new catalog only replaces the old one once all reachable backend connections succeed); a call that lands on a since-removed server hits the same per-server error path as any other failure.
- **[Risk]** Single process acting as an MCP server and a simultaneous multi-backend MCP client is new to this specific repo's Python code, even though standard elsewhere → **Mitigation**: build directly on `fastmcp`'s proven proxy/client machinery (see architecture decision above) rather than hand-rolling the concurrency, which was the whole reason to prefer `fastmcp` over the bare SDK.
- **[Risk]** Crowded competitive space — several existing MCP gateway/proxy/aggregator projects → **Mitigation**: the README and positioning lead with the two things the competitive scan found nobody else does: embedding-based semantic matching and a live, self-reported savings receipt.

## Migration Plan

N/A — greenfield new repository, nothing existing to migrate from or roll back.

## Open Questions

None outstanding — every design-level question surfaced during grilling was resolved and is logged in `decisions.md` (D1–D15, D6 amended).
