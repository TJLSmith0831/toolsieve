"""Turn `results.json` into `RESULTS.md` — the table that gets quoted.

    uv run python benchmarks/render_results.py

Deliberately plain: no chart, no template engine. The output has to survive
being pasted into a README and read correctly on GitHub.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

HERE = Path(__file__).resolve().parent
RESULTS_PATH = HERE / "results.json"
MARKDOWN_PATH = HERE / "RESULTS.md"

METHOD_LABELS = {
    "naive": "naive (no routing)",
    "bm25": "BM25 (`rank-bm25`)",
    "toolsieve-v0.2": "toolsieve v0.2",
    "toolsieve": "**toolsieve**",
}
TIERS = ("exact", "paraphrase", "ambiguous")


def _row_cells(row: dict[str, Any]) -> list[str]:
    tiers = row.get("recall_by_difficulty", {})
    return [
        str(row["catalog_size"]),
        METHOD_LABELS.get(row["method"], row["method"]),
        f"{row['k']:g}",
        f"{row['recall_at_k']:.2f}",
        f"{row.get('recall_visible', row['recall_at_k']):.2f}",
        *[f"{tiers[t]:.2f}" if t in tiers else "—" for t in TIERS],
        f"{row.get('tokens_per_call', 0):,}",
        f"{row.get('expected_calls', 1):.2f}",
        f"{row.get('tokens_per_resolution', 0):,}",
    ]


def headline(results: dict[str, Any]) -> list[str]:
    """The two sentences the README is allowed to quote.

    Pulled from the largest catalog row so the claim is always backed by the
    row directly below it, rather than hand-copied and left to drift.
    """
    rows = results["rows"]
    biggest = max(r["catalog_size"] for r in rows)
    at_size = {r["method"]: r for r in rows if r["catalog_size"] == biggest}
    sieve, bm25 = at_size.get("toolsieve"), at_size.get("bm25")
    old = at_size.get("toolsieve-v0.2")
    if not sieve or not bm25 or "paraphrase" not in sieve.get("recall_by_difficulty", {}):
        # A partial run still renders its table; it just gets no quotable claim.
        return []
    catalog_tokens = sieve["tokens_if_naive"] // sieve["queries"]
    claims = [
        f"On a catalog of {biggest} tools from {results['catalog_servers']} real MCP "
        f"servers, toolsieve resolves a tool lookup in "
        f"**~{sieve['tokens_per_resolution']:,} tokens against the "
        f"{catalog_tokens:,} it would cost to load the catalog into context**. The "
        f"right tool arrives callable **{sieve['recall_at_k']:.0%}** of the time and is "
        f"at least *named* **{sieve['recall_visible']:.0%}** of the time, so a client "
        "that misses does one cheap exact-name lookup instead of guessing what else "
        "might exist.",
        "",
        f"On queries that share no vocabulary with the tool's own description — the "
        f"`paraphrase` tier, and the case lexical search is worst at — it finds the "
        f"tool **{sieve['recall_by_difficulty']['paraphrase']:.0%}** of the time "
        f"against BM25's **{bm25['recall_by_difficulty']['paraphrase']:.0%}**.",
    ]
    if old and old.get("repriced") and sieve.get("repriced"):
        # Quote the comparison at real schema sizes, and say plainly where it
        # does not hold. On this catalog's unusually small schemas v0.2 is
        # cheaper, and rendering that as a negative "% less" would be a lie of
        # presentation — see the sensitivity section for why the sizes differ.
        parts = []
        for label, repriced in sieve["repriced"].items():
            delta = saved_between(old["repriced"][label], repriced)
            parts.append(f"**{delta:.0f}% less** at {label}")
        here = saved_between(old["tokens_per_resolution"], sieve["tokens_per_resolution"])
        verdict = (
            f"{abs(here):.0f}% *more* on this catalog's unusually small schemas"
            if here < 0
            else f"{here:.0f}% less on this catalog"
        )
        claims += [
            "",
            f"Against toolsieve's own v0.2 shape — same embeddings, same ranking, only "
            f"the response redesigned — resolving a lookup costs "
            f"{', '.join(parts)}, and {verdict}. The odds the right tool is visible at "
            f"all rise from **{old['recall_visible']:.0%} to "
            f"{sieve['recall_visible']:.0%}**, and it is failed lookups, not successful "
            "ones, that dominate the bill.",
        ]
    claims += [
        "",
        "> **Two honest caveats.** (1) This catalog averages "
        f"{catalog_tokens // biggest} tokens per tool; live public MCP servers measure "
        "~154 and a GitHub-heavy set ~269, and the saving *grows* with schema size, so "
        "these rows are the conservative end — see Schema-size sensitivity. (2) Loading "
        "a catalog is a one-off while routing is charged per lookup — toolsieve is ahead "
        f"for the first **{sieve['break_even_lookups']:.0f} lookups** of a session at "
        "this catalog size, after which the up-front load would have been cheaper on "
        "tokens alone (though not on context window, nor on selection accuracy past "
        "~30-50 tools).",
    ]
    return claims


def saved_between(before: int, after: int) -> float:
    return (before - after) / before * 100 if before else 0.0


def crossover(old: dict[str, Any], new: dict[str, Any], base: int, sizes: dict[str, int]) -> float | None:
    """Schema size at which the shipped shape overtakes v0.2, by linear fit.

    Both costs are linear in schema size — each ships a fixed number of schemas
    — so two points determine the line and the intersection is exact, not fitted.
    """
    label = next((k for k in sizes if k in old.get("repriced", {})), None)
    if not label:
        return None
    size = sizes[label]
    slope_old = (old["repriced"][label] - old["tokens_per_resolution"]) / (size - base)
    slope_new = (new["repriced"][label] - new["tokens_per_resolution"]) / (size - base)
    if slope_old <= slope_new:
        return None
    gap = new["tokens_per_resolution"] - old["tokens_per_resolution"]
    return base + gap / (slope_old - slope_new)


def sensitivity_section(results: dict[str, Any]) -> list[str]:
    """Re-price the biggest catalog's rows at real, measured schema sizes.

    This section exists because the table above nearly produced the wrong
    decision. Judged on this catalog alone, the v0.2 response shape is cheaper —
    but this catalog's schemas are unusually small, and the schema term is
    precisely what the two shapes trade against. Re-pricing settles it.
    """
    rows = results["rows"]
    biggest = max(r["catalog_size"] for r in rows)
    at_size = [r for r in rows if r["catalog_size"] == biggest and r.get("repriced")]
    if not at_size:
        return []
    labels = list(at_size[0]["repriced"])
    base = round(at_size[0]["schema_tokens"] / max(1, at_size[0]["schema_count"]))

    lines = [
        "",
        "## Schema-size sensitivity",
        "",
        f"This catalog's schemas average **{base} tokens per tool**. Live public MCP "
        "servers measure **154** (`server-everything` + `filesystem` + `memory` + "
        "`sequential-thinking`, dumped over stdio) and a GitHub-heavy set measures "
        "**269**. That gap matters more than it looks: a response shape is mostly a bet "
        "on how much a schema costs, so the same table can rank two shapes differently "
        "at different sizes. Below, only the schema term is re-priced — descriptions, "
        "names and roster stay exactly as measured, and recall is untouched because the "
        "router ranks on name + description and never reads a schema.",
        "",
        f"Tokens per **resolved** query, {biggest}-tool catalog:",
        "",
        "| method | "
        + " | ".join(
            [f"{base} tok/tool (this catalog)"]
            + [f"{results.get('schema_sizes', {}).get(l, '?')} tok/tool ({l})" for l in labels]
        )
        + " |",
        "|" + "|".join(["---"] * (len(labels) + 2)) + "|",
    ]
    for row in at_size:
        cells = [METHOD_LABELS.get(row["method"], row["method"]), f"{row['tokens_per_resolution']:,}"]
        cells += [f"{row['repriced'][l]:,}" for l in labels]
        lines.append("| " + " | ".join(cells) + " |")

    old = next((r for r in at_size if r["method"] == "toolsieve-v0.2"), None)
    new = next((r for r in at_size if r["method"] == "toolsieve"), None)
    if old and new:
        point = crossover(old, new, base, results.get("schema_sizes", {}))
        if point:
            lines += [
                "",
                f"The two shapes cross at **~{point:.0f} tokens per tool**. Below that, "
                "shipping three schemas and no roster is cheaper; above it, shipping one "
                "schema and a roster of names is — and every real catalog measured here "
                "sits above it. The benchmark catalog is the exception, not the case to "
                "optimise for.",
            ]
        lines += [
            "",
            "### What this table cannot see",
            "",
            "Every query in the set has a correct answer. The case that actually cost "
            "the most in the pre-release smoke test does not: the client was hunting a "
            "`get_repository` that **does not exist** on the GitHub MCP server, and no "
            "ranking can retrieve a tool that is not there. A ranked list has no way to "
            "say so, so the client rewords and searches again; a roster of names does, "
            "and that difference is invisible here because it never arises.",
            "",
            "So treat these rows as the *conservative* estimate of the roster's value, "
            "and the live A/B as the measurement of it: against real GitHub and Context7 "
            "servers the same task went from 4 `find_tools` calls to 3 with no variance "
            "across three runs, and `find_tools` token cost fell 62%.",
        ]
    return lines


def render(results: dict[str, Any]) -> str:
    header = [
        "catalog",
        "method",
        "schemas",
        "callable",
        "visible",
        *TIERS,
        "tokens/call",
        "calls",
        "tokens/resolved",
    ]
    lines = [
        "# Routing benchmark results",
        "",
        *headline(results),
        "",
        "Generated by `benchmarks/run_benchmark.py`. Re-run it after any material "
        "change to the catalog or the router — these numbers are quoted in the README.",
        "",
        "## Setup",
        "",
        f"- **Catalog**: {results['catalog_tools']} tools across "
        f"{results['catalog_servers']} real MCP servers (`{results['sources']['catalog']}`)",
        f"- **Queries**: {results['queries']} with ground-truth expected tools, "
        f"tagged exact / paraphrase / ambiguous (`{results['sources']['queries']}`)",
        f"- **Tokenizer**: `{results['tokenizer']}` — a real tokenizer, not "
        "toolsieve's runtime chars/4 estimate",
        f"- **Embedding model**: `{results['embed_model']}` (toolsieve's shipped default)",
        f"- **k**: {results['top_k']} for the ranked methods.",
        "",
        "### Reading the columns",
        "",
        "- **schemas** — tools handed over with a full input schema, i.e. callable "
        "without a further lookup. This is where the tokens go: a schema is roughly "
        "ten times a bare name.",
        "- **callable** — the right tool arrived with its schema. The task proceeds "
        "on this call; nothing more is spent on discovery.",
        "- **visible** — the right tool was at least *named* in the response. Worst "
        "case one exact-name lookup follows, which is cheap and deterministic. The gap "
        "between `callable` and `visible` is the share of queries rescued from a blind "
        "reformulation — the failure that made v0.2 cost four searches to make three "
        "calls. For methods that only return schemas, the two columns are identical by "
        "construction.",
        "- **tokens/call** — one response, counted whole: schemas, descriptions and "
        "roster alike. **Do not read this as the headline.** A shape that answers "
        "cheaply but misses often looks best here and is worst in the next column.",
        "- **calls** — expected `find_tools` calls to actually resolve a query: 1 when "
        "the schema arrived, 2 when only the name did, 4 when neither (the rate "
        "observed in the pre-release smoke test).",
        "- **tokens/resolved** — `tokens/call` x `calls`. **This is the column to "
        "compare shapes on**, and the one that reverses the apparent verdict against "
        "toolsieve's own v0.2.",
        "",
        "## Results",
        "",
        "| " + " | ".join(header) + " |",
        "|" + "|".join(["---"] * len(header)) + "|",
    ]
    for row in results["rows"]:
        lines.append("| " + " | ".join(_row_cells(row)) + " |")

    lines += sensitivity_section(results)
    lines += ["", "## How to reproduce", "", "```sh", "uv sync --group bench",
              "uv run --group bench python benchmarks/run_benchmark.py",
              "uv run python benchmarks/render_results.py", "```", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=RESULTS_PATH)
    parser.add_argument("--out", type=Path, default=MARKDOWN_PATH)
    args = parser.parse_args()

    results = json.loads(args.results.read_text())
    args.out.write_text(render(results))
    print(f"wrote {args.out} ({len(results['rows'])} rows)")


if __name__ == "__main__":
    main()
