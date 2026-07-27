![toolsieve banner](assets/toolsieve-banner.png)

# toolsieve

![CI](https://github.com/TJLSmith0831/toolsieve/actions/workflows/ci.yml/badge.svg) ![version](https://img.shields.io/badge/version-0.2.0-blue) ![license](https://img.shields.io/badge/license-MIT-green)

**Semantic tool routing for MCP.** Point it at the MCP servers you already run.
It aggregates every tool they publish, then exposes exactly **three** tools to
your client — and tells you how many tokens that saved.

![toolsieve running in Claude Code](assets/demo.gif)

*Claude Code (Sonnet) against 4 real MCP servers — 15 tools aggregated, 3 exposed.
It routes "search my notes" and "read library docs" to the right tools, calls the
weather tool for real, and reports **4,286 tokens saved (79.7%)** across the session.
One recorded session, not the headline claim — see [Benchmarks](#benchmarks) for
the measured numbers.*

## Why

Every MCP server you connect dumps its full tool list — names, descriptions, and
JSON schemas — into your model's context on every single request. Five servers in,
you're spending thousands of tokens per call describing tools you won't use.

Existing MCP gateways and proxies solve this by filtering **lexically** (BM25
keyword matching) or **structurally** (manual allow-lists). toolsieve matches
**semantically**: it embeds each tool's own name and description, embeds your
query, and returns the closest matches by cosine similarity. Ask for "what's the
weather" and it finds `get_weather` — no keyword overlap required.

And it shows its work. Every response carries a token-savings receipt.

## Benchmarks

**181 tools from 25 real MCP servers** (GitHub, Slack, Notion, Linear, Stripe,
Supabase, Playwright, Postgres and more), 159 queries with a known correct tool.

> **Up to 25 tools, routing is free.** toolsieve finds exactly what loading the
> whole catalog would — same 100% — on **12% of the tokens**. At 50 tools it
> still finds the right tool **98%** as often, on **6% of the tokens**.

### Savings climb fast. Accuracy barely moves.

Both charts are on the same scale, so you can read one against the other:

```
  Tokens saved — climbs steeply with catalog size
     10 tools   ██████████████████████████▋                66.7%
     25 tools   ███████████████████████████████████        87.7%
     50 tools   █████████████████████████████████████▌     94.0%
    100 tools   ██████████████████████████████████████▊    96.9%
    181 tools   ███████████████████████████████████████▎   98.3%

  Right tool still found — barely moves
     10 tools   ████████████████████████████████████████    100%
     25 tools   ████████████████████████████████████████    100%
     50 tools   ███████████████████████████████████████▎     98%
    100 tools   ████████████████████████████████████▉        92%
    181 tools   █████████████████████████████████▉           85%
                ├─────────┬─────────┬─────────┬─────────┤
                0%       25%       50%       75%     100%
```

That second chart is measured against loading every tool into context, which is
the ceiling — it scores 100% by definition, because it never chooses. Getting to
94% savings costs you two points of that. Getting to 98.3% costs fifteen.

In absolute terms: at 50 tools a `find_tools` call carries **241 tokens instead
of 4,010**. At 181, **244 instead of 14,418**.

### Why not just keyword matching?

Because it falls apart on the queries real users actually type:

```
  Right tool found, 50-tool catalog
    toolsieve   ███████████████████████████████████████▎     98%
    BM25        ██████████████████████████████▌              76%

  …when the query shares no wording with the tool
    toolsieve   █████████████████████████████████████▉       95%
    BM25        ██████████████████▉                          47%
                ├─────────┬─────────┬─────────┬─────────┤
                0%       25%       50%       75%     100%
```

*"Remember for later that Alice works at Acme"* against **Create multiple new
entities in the knowledge graph** — not one word in common. BM25 has nothing to
match on. Semantic matching doubles its accuracy on queries like these, and does
it for fewer tokens per call, not more.

### What a correct answer costs

Tokens alone don't settle it — a cheap call that routes to the wrong tool isn't a
saving. Divide tokens per call by how often the method actually finds the tool,
and you get the real unit price:

```
  Tokens spent per correctly routed query, 50-tool catalog
    naive       ████████████████████████████████████████   4,010
    BM25        ███▎                                         328
    toolsieve   ██▍                                          246
                ├───────────────────────────────────────┤
                0                                   4,010
```

**toolsieve is the most accurate *and* the cheapest of the three** — 25% less per
correct answer than BM25, 16× less than loading the catalog. It wins on both axes
at once, which is the whole claim in one bar chart.

*(Derived: tokens-per-call ÷ recall. The raw columns behind it are in the results
table.)*

Full per-size table, difficulty breakdown, methodology, and how to reproduce:
**[`benchmarks/RESULTS.md`](benchmarks/RESULTS.md)**.

## How it works

```
   your MCP client
         │  sees only: find_tools, call_tool, get_savings_report
         ▼
    ┌─────────────┐
    │  toolsieve  │  embeds each tool's name + description once
    └─────────────┘  matches your query by cosine similarity
       │    │    │
       ▼    ▼    ▼     real MCP servers — local stdio or remote HTTP/SSE,
     docs notes linear   connections held open
```

1. **`find_tools(query, k=3)`** — returns the closest matching tools, each with
   its owning server, description, and full input schema, plus the savings receipt.
2. **`call_tool(server, tool_name, args)`** — proxies the real call to the server
   that owns it and returns the real result.
3. **`get_savings_report()`** — running session total.

Two steps rather than one, deliberately: a router can't reliably invent valid
arguments from free text. You see the real schema before you call.

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/TJLSmith0831/toolsieve
cd toolsieve
uv sync
```

### Configure

#### Migrate an existing client config

Already have `mcpServers` configured in Claude Code, Claude Desktop, Cursor,
Windsurf, VS Code, or Codex CLI? `scripts/setup_toolsieve.py` moves those
entries behind toolsieve for you instead of hand-copying config:

```bash
uv run python scripts/setup_toolsieve.py --list                       # discover configs across every known client
uv run python scripts/setup_toolsieve.py --client claude-code --dry-run  # preview the migration
uv run python scripts/setup_toolsieve.py --client claude-code --apply    # write it
```

Nothing is written without `--apply`. Every file the script edits is backed
up first (`*.toolsieve-bak` for the client config, `*.json.bak` for an
existing toolsieve config), and servers it doesn't recognize are left
untouched. HTTP servers with no auth headers are flagged with `!` rather than
silently migrated, since that's either an open server or one whose token
lives in the client via OAuth — a case toolsieve can't resolve for you (see
[Authenticating a remote server](#authenticating-a-remote-server)).

Or configure it by hand:

Create `toolsieve.config.json`. It's the same `mcpServers` shape Claude Desktop
and Claude Code use, so entries are usually copy-pasteable from a config you
already have:

```json
{
  "mcpServers": {
    "docs":     { "command": "node", "args": ["/path/to/docs-mcp/dist/index.js"] },
    "notes":    { "command": "uv",   "args": ["run", "--directory", "/path/to/notes-mcp", "python", "src/index.py"] },
    "mintlify": { "url": "https://mcp.mintlify.com" },
    "linear":   { "url": "https://mcp.linear.app/mcp",
                  "headers": { "Authorization": "Bearer ${LINEAR_TOKEN}" } }
  }
}
```

Transport is inferred from the entry: `command` means a local stdio process,
`url` means a remote HTTP/SSE server. A URL ending in `/sse` uses SSE, anything
else uses Streamable HTTP. Both kinds land in one catalog — `find_tools` and
`call_tool` don't distinguish.

Edit this file while toolsieve is running and it re-aggregates automatically —
no restart.

#### Authenticating a remote server

Put the credential in a header and reference it with `${VAR}`, expanded from the
environment toolsieve runs in. It works in `headers` values and in the `url`, for
servers that want their key in a query string:

```json
"ref": { "url": "https://api.ref.tools/mcp?apiKey=${REF_API_KEY}" }
```

If the variable is unset, that one server fails with an error naming it —
toolsieve will not substitute an empty string and fire off an unauthenticated
request, and your other servers are unaffected. **Keep tokens in your shell
profile or secret manager, not in this file.**

**OAuth is not supported.** If your client authenticated a server through an
OAuth flow, that token lives in the client's own credential store and toolsieve
can't reuse it — you need a bearer token or API key from that service instead.
`scripts/setup_toolsieve.py` flags such entries with a `!` rather than silently
migrating them into something that 401s on every call.

### Run

As an MCP server, from any client:

```json
{
  "mcpServers": {
    "toolsieve": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/toolsieve", "python", "-m", "toolsieve"],
      "env": { "TOOLSIEVE_CONFIG": "/path/to/toolsieve.config.json" }
    }
  }
}
```

Or see it work end to end. With no config, this runs against two real stdio MCP
servers the repo ships, so it works on a fresh clone with nothing else installed:

```bash
uv run python demo.py
```

Savings look modest on that 4-tool demo catalog — routing 3 of 4 tools can't save
much. Point `TOOLSIEVE_CONFIG` at your own servers to see the real number.

### Claude Code

```
/plugin marketplace add TJLSmith0831/toolsieve
/plugin install toolsieve
```

The plugin reads its server list from `~/.toolsieve/config.json` — a home-directory
path, so it survives plugin updates.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `TOOLSIEVE_CONFIG` | `toolsieve.config.json` | Path to the `mcpServers` config |
| `TOOLSIEVE_CONFIDENCE_THRESHOLD` | `0.70` | Below this, matches are flagged `confidence: "low"` |
| `TOOLSIEVE_LOG_LEVEL` | `WARNING` | Set `INFO` to see aggregation and match logging |

## Behavior worth knowing

**Match quality depends on your downstream tools' descriptions.** toolsieve embeds
what each server publishes — it does not rewrite it. A tool described as `"runs the
thing"` will match poorly, and that's a property of the tool, not the router.
toolsieve warns at startup about tools with no description at all.

**A weak match is flagged, not withheld.** Measured against a real catalog,
on-topic queries score roughly 0.56–0.83 and off-topic ones 0.38–0.55 — the ranges
nearly touch, so no threshold cleanly separates them. Rather than tell you "nothing
matched" while a perfectly good tool exists, toolsieve returns its best match and
tags anything under the threshold `confidence: "low"`. You see the score and the
schema, so you can judge. If a match is wrong, call again with
`exclude=["server/tool_name"]`.

**One server going down doesn't take toolsieve with it.** A server that fails to
connect is logged and skipped; the rest of the catalog still works. A call to a
failed server returns an error naming that server. A missing or broken config
starts toolsieve with an empty catalog rather than crashing — fix the file and it
loads with no restart.

**Remote servers get one retry, in both directions.** A remote endpoint that
doesn't answer at startup is retried once before being dropped, so a momentary
blip doesn't silently cost you a whole server until you next edit the config. And
because idle timeouts, proxies, and redeploys quietly kill long-lived HTTP
sessions, a call that fails on a dead session reconnects and retries once before
erroring. Neither applies to stdio: a bad command is deterministic, so retrying
it only adds latency to a failure you're getting anyway.

**The receipt's token counts are estimates; its percentage is not.** Absolute
counts use ~4 chars/token, but `saved_pct` is exact — both sides are measured
identically, so the estimator cancels out. The [benchmark](#benchmarks) uses a
real tokenizer instead, because an absolute number quoted in docs shouldn't come
from an estimate. Routing k=3 out of 3 tools saves nothing, which is why the
savings curve is reported across catalog sizes rather than as one number.

## Development

```bash
uv run pytest -q
```

Tests run against real MCP servers over both transports — stdio subprocesses and
a real HTTP server on localhost — not mocks. No network egress required.

The benchmark's scoring, baseline, and wiring tests run in that sweep too (with a
fake embedder, so no model download). The full benchmark is a manual step, since
it downloads a real embedding model and scores 3 methods × 5 catalog sizes × 159
queries:

```bash
uv sync --group bench
uv run --group bench python benchmarks/run_benchmark.py
uv run python benchmarks/render_results.py
```

## Changelog

Releases follow [Semantic Versioning](https://semver.org/). See
[CHANGELOG.md](CHANGELOG.md) for what changed in each release.

## License

[MIT](LICENSE)
