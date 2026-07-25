# toolsieve — Demo Brief

**Status: pre-build.** This is written from the locked plan (`openspec/changes/toolsieve/decisions.md`, `specs/`), before implementation. Numbers marked `[TBD — measure]` do not exist yet — fill them in from a real run once `tasks.md` is complete. Do not record until every item in "Checks before recording" passes for real.

**Hook:** Every MCP tool-aggregation project I found (`mcpproxy-go`, `toolfunnel`, `mcp-gateway`, `mcp-orchestrator`) filters lexically and tells you nothing about what it saved you. This one matches by meaning, points at real MCP servers instead of a fake catalog, and prints the receipt on every call.

---

## What this demo shows (once built)

1. **Real aggregation, not a fabricated catalog.** toolsieve fronts two genuinely different, already-built MCP servers (day-08-docs-mcp, day-09-cached-weather-mcp) — not hand-picked toy tools built to make the demo look good.
2. **Zero manual routing setup.** Unlike day-13's hand-authored `Route` objects, toolsieve embeds each tool's own published name + description directly. Point it at any MCP server and it routes with no per-tool authoring.
3. **The savings receipt is live, not a one-time benchmark finding.** Every `find_tools` call returns `tokens_if_naive` / `tokens_actual` / `saved_pct` in the response itself — `[TBD — measure]`% on the day-08+day-09 combined catalog, not a number carried over from day-13's different catalog and shouldn't be quoted as if it were.
4. **It doesn't fall over.** Kill the weather server mid-session — the docs server keeps answering (per-server failure isolation, `mcp-aggregation` spec). Add a new server to the config live — it's routable within seconds, no toolsieve restart (live reload, same spec).

**Do not claim:** a specific accuracy or savings percentage until it's actually been run and measured against toolsieve's own aggregated catalog. Day-13's 54–61% figure is a different project, a different 8-tool catalog, and does not transfer — quoting it here without a fresh measurement would be exactly the kind of unearned claim day-13's own brief warned against.

---

## Why this matters (the employer story)

- **Found the actual gap before writing code.** A scan of `awesome-mcp-servers` turned up a crowded field of lexical/BM25 tool-filtering gateways — none doing embedding-based semantic matching, none reporting live savings. That's the two-sentence differentiator, arrived at by checking the competition, not assuming there wasn't any.
- **Refused to ship a thin wrapper.** The obvious version of this project is `semantic-router` in an MCP shell. The shipped version aggregates real downstream servers instead of a fake catalog specifically because that's the actual pain point, not the easy demo.
- **15 decisions logged before implementation** (`decisions.md`) — config format, failure isolation, match-target source, interface shape, all resolved and traceable, not improvised mid-build.
- **Reused day-10's proof, corrected it, and moved on.** Early plan assumed the bare `mcp` SDK was sufficient because day-10 proved server+client work separately; checking `fastmcp` properly found purpose-built proxy machinery that removes real plumbing work, and the plan changed before code was written, not after.

---

## Setup (once tasks.md is complete)

```bash
cd toolsieve
uv sync
cp toolsieve.config.example.json toolsieve.config.json   # points at day-08-docs-mcp + day-09-cached-weather-mcp
uv run toolsieve
```

Point the config at the sibling `50-days-of-dev/day-08-docs-mcp` and `day-09-cached-weather-mcp` checkouts — real servers, not stubs.

---

## Demo scenario

Single terminal, MCP client (Claude Code or a raw client script) issuing calls against toolsieve:

1. Start toolsieve, show the aggregated catalog log line (N tools across 2 servers).
2. `find_tools("what's the weather in Boston")` — show it landing on day-09's weather tool, with the savings metadata block in the response.
3. `call_tool(...)` the matched tool — real weather data comes back, not a stub.
4. Kill the weather server process. `find_tools("what's the weather in Boston")` again — either no match or a clear per-server error, docs-related queries still work.
5. Restart the weather server, edit the config to bump something trivial (or add a third server if one exists by then) — show the catalog picking it up without restarting toolsieve.
6. `get_savings_report()` — hold on the session total.

---

## Shot list (~45–60s, plan — revise once real timings exist)

1. **Cold open (5–10s):** the crowded-field problem — a caption listing 3–4 competing gateway names and "none of them do this." Sets up why this exists.
2. **Startup (5s):** terminal, toolsieve boots, aggregated catalog line visible. Caption: *two real MCP servers, one router.*
3. **find_tools live (10–15s):** the query, the match, the savings metadata block in the raw response. Caption: *matched by meaning — no hand-written routes, just the tool's own description.*
4. **call_tool → real result (10s):** the actual weather data or doc content coming back. Caption: *not a mock. A real call, proxied through.*
5. **Kill-and-recover (15s):** kill a server, show isolation; bring it back / add a new one, show live reload. Caption: *one server dies, the rest keep working. Add a new one, no restart.*
6. **The receipt (5–10s):** `get_savings_report()`, session total held on screen.

---

## What NOT to demo

- **Any specific savings percentage that hasn't actually been measured this session.** Compute it live or don't show it.
- **The Claude Code plugin install as the headline.** It's a convenience wrapper (D5) on top of the real deliverable (a standalone MCP server), not the story — lead with the router working against any client.
- **A "confused the model" framing.** Day-13 already falsified that premise at small catalog sizes; this project's story is context cost and real multi-server UX, not agent accuracy.
- **A stale config or leftover data from prior runs** — reset to a clean aggregated state before recording, same discipline as day-13's `rm -rf data`.

---

## LinkedIn post draft (fill in the bracketed numbers after a real run)

> Day 14 — Ship Day. I took day-13's finding (semantic tool-filtering cuts token cost ~58% without hurting accuracy) and shipped it as something installable, not just a benchmark result.
>
> toolsieve is an MCP server that aggregates your *real* MCP servers and routes tool selection by matching the query's meaning against each tool's own description — no hand-written routing rules, no fake tool catalog. Point it at what you already have.
>
> Checked the field first: every MCP gateway/proxy project I found filters lexically. None report what they actually saved you. toolsieve does both — routes by embedding, and prints the token receipt on every call: `[TBD]`% saved on a real two-server catalog.
>
> It doesn't fall over either — kill one downstream server and the rest keep answering; add a new one and it's routable without a restart.
>
> New repo, MIT licensed: github.com/TJLSmith0831/toolsieve
>
> Day 14 of 50. #AIEngineering #MCP #OpenSource

---

## Checks before recording

1. `uv run pytest` (or equivalent) green — no test-suite failures.
2. Clean-clone, one-command run verified end to end (tasks.md 6.2) — fresh clone, not the dev checkout with cached state.
3. `find_tools` on a real query lands on the correct tool, savings metadata present and non-zero.
4. `call_tool` returns a genuine result from the real downstream server, not an error or stub.
5. Killing one downstream server leaves the other's tools answerable — confirms `mcp-aggregation` failure isolation actually works, not just on paper.
6. Editing the config while running picks up a change without a toolsieve restart — confirms live reload actually works.
7. `get_savings_report()` returns a non-zero, sane-looking session total.
8. Every number that appears on camera was produced by the run being recorded, not copied from an earlier session or from day-13.
