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

## D4: Demo/downstream servers (amended)
- **Decision**: The demo aggregates day-08-docs-mcp and day-09-cached-weather-mcp (already-built, real MCP servers from the 50-days-of-dev repo) as the downstream servers being routed to, rather than a fabricated tool catalog. **Amended: also aggregate day-10-subagent-mcp and `tolaria`, a real third-party stdio MCP server already installed on the demo machine.**
- **Why**: Real, working tools give an honest demo, and it's a natural callback to earlier challenge days.
- **Why (amendment)**: day-08 + day-09 publish only **3 tools between them**, and `find_tools` defaults to k=3 — so the demo returned the entire catalog and the savings receipt honestly read **0.0%**. Routing cannot save anything until the catalog is larger than k, so D4's original backends made the headline differentiator (D7, D14) undemonstrable. Adding day-10 (4 tools) and tolaria brings the catalog to roughly 10–15 tools, the range where the pain toolsieve exists to solve actually begins. Including a third-party server the author did not write also strengthens the demo: it shows toolsieve aggregating arbitrary servers, not just ones built to suit it.
- **Source**: user (amended at implementation time after measuring 0% savings on the original demo catalog)

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

## D9: Embedding/match target for real MCP tools (amended)
- **Decision**: Embed each aggregated tool's own `name` + `description` (from its JSON schema, as published by the downstream server) directly as the match target — no hand-authored example utterances (unlike day-13's `Route` objects), no LLM call to generate synthetic phrasing. **Amendment (implementation): the `semantic-router` package is dropped from the dependency set.** Matching is `fastembed` for embeddings + a `numpy` cosine top-k (~5 lines), which `fastembed` already pulls in transitively.
- **Why**: Zero manual authoring per tool, fully automatic off whatever the downstream server already publishes, keeps the dependency footprint provider-agnostic (no LLM/API key needed just to build the routing index). Tradeoff accepted: match quality depends on how well-written the downstream tool's description already is — a weakly-described tool routes weakly, which toolsieve should surface (e.g. low-confidence-match logging) rather than silently paper over.
- **Why (amendment)**: `semantic-router`'s entire API is `Route(name, utterances=[...])` — the utterance model this decision explicitly rejects. Keeping it would mean bending a library to a use case it wasn't built for, at a measured cost of **25 extra transitive packages** (116 resolved vs. 91 without). `fastembed` is and always was the actual embedding engine inherited from day-13; the router wrapper contributed nothing once utterances were off the table. Affects D6's dependency list and task 1.3.
- **Source**: user (amended at implementation time after measuring the resolved dependency footprint)


## D10: Meta-tool interface shape
- **Decision**: Two explicit tools exposed to the client: `find_tools(query, k=3)` returns the top-k matching real tools (name, owning server, description, input schema); `call_tool(server, tool_name, args)` proxies the actual invocation straight through to the real downstream server and returns its result.
- **Why**: The router can't reliably invent valid arguments from a free-text query — that's what tool schemas exist to prevent, so the client needs to see the schema before calling. Two explicit tools also work with any MCP client regardless of support for dynamic tool-list updates (`tools/list_changed`), keeping it universally compatible per D5, versus a slicker but narrower dynamic-injection approach.
- **Source**: user

## D11: find_tools defaults and low-confidence behavior (amended)
- **Decision**: Default `k=3` (adjustable per-call). ~~If no aggregated tool clears a similarity floor, `find_tools` returns an empty match list.~~ **Amended: the similarity floor is a confidence signal, not a rejection gate.** `find_tools` always returns the best available matches; any match scoring below **0.70** is returned but tagged `confidence: "low"` with a message saying the match is uncertain. A match is only withheld when the client explicitly says it was wrong, via the `exclude` parameter (`["server/tool_name", ...]`). An empty match list now means only "the catalog is empty" or "the query was blank."
- **Why**: k=3 gives headroom over day-13's top-2 since a real aggregated catalog (day-08 + day-09 combined) is larger than day-13's 8-tool set. Returning zero matches on a genuinely off-topic query makes D9's tradeoff (match quality depends on downstream description quality) visible and debuggable instead of silently returning irrelevant tools.
- **Why (amendment)**: The original decision assumed a floor cleanly separates relevant from irrelevant. Measured over 20 queries against a real aggregated catalog on `BAAI/bge-small-en-v1.5`, it does not — on-topic queries scored **0.5604–0.8281**, off-topic **0.3836–0.5500**, a margin of only **+0.0104**. Any floor placed high enough to reject the off-topic cluster also rejects genuinely relevant queries (0.70 rejected 6 of 12, including *"how hot is it in Denver"* against a weather tool). BGE's documented query-instruction prefix was tested as a fix and made separation *worse* (margin −0.0054), so the overlap is inherent to matching short free-text queries against short tool descriptions, not a bug in the encoder usage. Given that, a false negative (client told "nothing matched" when a perfectly good tool exists) is strictly worse than a flagged marginal match the client can evaluate for itself — it sees the score, description, and schema. This also delivers what D9 already asked for in its own words: surface low confidence rather than paper over it.
- **Source**: user (amended at implementation time after measuring real score distributions)

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

## D16: Similarity-floor value (implementation)
- **Decision**: D11 specified the empty-result behavior but never a number. The floor is **0.70 cosine**, overridable per-instance via `TOOLSIEVE_SIMILARITY_FLOOR`.
- **Why**: Measured against a real aggregated catalog on `BAAI/bge-small-en-v1.5` (fastembed's default): on-topic queries scored 0.729–0.756, off-topic queries 0.372–0.481. 0.70 sits in a wide empty band between the two clusters rather than on a knife edge. Kept as an env-tunable knob because the separation is model-dependent — swapping the embedding model requires re-checking it.
- **Source**: recommended-accepted

## D17: Token counts are estimates; the percentage is exact (implementation)
- **Decision**: `tokens_if_naive` / `tokens_actual` are computed with a `len(text) // 4` heuristic over the JSON payloads, not a real tokenizer. Every savings response and `get_savings_report()` carries an explicit note saying so.
- **Why**: Dropping `semantic-router` (D9 amended) also dropped `tiktoken` from the resolved dep set, and adding a tokenizer back for this would be a dependency bought for cosmetic precision. `saved_pct` — the number the receipt actually leads with, and the one D12 exists to make tangible — is **exact regardless of the estimator**, since naive and actual are measured the same way and the scale factor cancels. Any real tokenizer would also be the wrong one anyway (it would not be the client LLM's). Labelling beats a false-precision absolute count.
- **Source**: recommended-accepted

## D18: `call_tool` returns an envelope (implementation)
- **Decision**: `call_tool` returns `{ok, server, tool_name, result}` on success and `{ok: false, server, tool_name, error}` on failure, rather than returning the downstream result bare.
- **Why**: Forced by MCP, not preference. A bare `-> Any` return gives FastMCP no output schema, so the structured payload is dropped and the caller sees `result.data is None` with the value only reachable as raw text content — verified end-to-end. A concrete `dict` return restores structured output. The envelope also makes D13's per-server error path the same shape as the success path, so a client checks one field (`ok`) instead of sniffing types.
- **Source**: recommended-accepted

## D19: A missing or broken config must not stop the server from starting (implementation)
- **Decision**: If the config file is absent or unparseable, toolsieve **still starts**, with an empty catalog. The problem is reported through `find_tools`/`get_savings_report` (and a stderr warning) instead of a startup crash, and the config watcher keeps running so creating or fixing the file hot-loads it with no restart.
- **Why**: Found while verifying the Claude Code plugin (task 5.2): a fresh plugin install has no `toolsieve.config.json`, so the server crashed on startup and the client saw no toolsieve tools at all — directly violating the `claude-code-plugin` spec scenario ("available without manual MCP server configuration"). Crashing also contradicts D13's principle that toolsieve never goes down because of a downstream/config problem, and it made D8's live reload unreachable in exactly the case it helps most: the user creating the config for the first time after install. Surfacing the error through the tools keeps it loud without making it fatal.
- **Source**: recommended-accepted

## D20: Plugin config lives in `~/.toolsieve/config.json` (implementation)
- **Decision**: The Claude Code plugin points `TOOLSIEVE_CONFIG` at `${HOME}/.toolsieve/config.json`, not at a file inside the plugin directory. Run standalone (not via the plugin), the default stays `toolsieve.config.json` in the working directory.
- **Why**: A plugin is installed into Claude Code's versioned plugin cache (`~/.claude/plugins/cache/<plugin>/<version>/`), so a config stored at the plugin root would be discarded on every plugin update — the user would silently lose their server list. A home-directory path survives updates and matches where MCP clients keep their own config. Also settled the manifest mechanics: Claude Code declares plugin MCP servers in a **root `.mcp.json`**, with `.claude-plugin/plugin.json` carrying metadata only (verified against the installed `railway` and `posthog` plugins, which both ship an MCP server this way) — an inline `mcpServers` block in `plugin.json` is the Codex-plugin format, not Claude Code's.
- **Source**: recommended-accepted

## D21: The plugin bundles a setup skill
- **Decision**: The plugin ships a skill that installs and configures toolsieve into whichever coding agent the user runs (Claude Code, Claude Desktop, Cursor, Windsurf, VS Code) — locating that client's existing MCP config, migrating its `mcpServers` entries into toolsieve's config, and registering toolsieve itself with the client.
- **Why**: User request. It closes the loop on D8's rationale: the config was chosen to be `mcpServers`-shaped precisely because entries are copy-pasteable from what the user already has, and a skill turns "copy-pasteable" into "already done." It also removes the main adoption barrier — the value of toolsieve is proportional to how many servers you point it at (D4 amended showed 3 tools saves nothing, 15 saves 80%), so a user who hand-migrates two servers never sees the benefit that would make them keep it.
- **Source**: user

## D22: The demo is a live Claude Code session, not `demo.py`
- **Decision**: The Ship-Day demo GIF (D14) records a **real Claude Code session on Sonnet with permissions auto-accepted**, driving toolsieve as an actual MCP server — Claude itself calling `find_tools`, then `call_tool`, then reading the savings receipt. `demo.py` stays in the repo as a scriptable smoke test and clean-clone check, but it is not what gets recorded.
- **Why**: User request. `demo.py` is toolsieve talking to itself through a client we wrote, which proves the plumbing but begs the question the product is actually making — *does a real coding agent pick the right tool from a routed catalog?* A live Claude Code session answers that on camera, and it is the same surface a viewer would install into, so the demo doubles as the install proof. Auto-accepted permissions keep the recording free of approval prompts that would otherwise dominate the frame.
- **Source**: user

## D15: No spike — build directly
- **Decision**: Skip the throwaway spike task. Go straight to building the real server+client architecture.
- **Why**: The server+simultaneous-multi-client pattern is the standard, well-established architecture for the entire MCP-aggregator category (mcp-gateway, mcpproxy-go, toolfunnel, mcp-orchestrator, pluggedin-mcp-proxy all work this way) — not a novel bet worth a separate throwaway task, even though it's untested in this specific repo's Python `mcp` SDK usage before now.
- **Source**: user
