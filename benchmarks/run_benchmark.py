"""Run every method over every catalog size and write `results.json`.

Not part of CI (D5) — a real `fastembed` model download plus 3 methods x 5 sizes
x 159 queries is too slow to gate a PR on. Run it by hand when the catalog or
the router changes materially:

    uv run --group bench python benchmarks/run_benchmark.py

Two things this file is careful about, because the output is quotable:

- Token counts are recomputed here with `tiktoken` (D4) rather than read out of
  `Router`'s own `savings` dict, which uses the runtime chars/4 estimator. All
  three methods therefore get counted by the same tokenizer.
- Recall is scored at `k = len(matches)` — the number of tools the method
  actually handed over (D7). That is one uniform rule, not a naive special
  case: it happens to be the whole catalog for naive and 3 for the rankers.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Protocol

# Absolute imports below, plus this, so the file runs both as
# `python benchmarks/run_benchmark.py` and as `python -m benchmarks.run_benchmark`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.baselines import BM25Router, LegacyToolsieve, NaiveRouter  # noqa: E402
from benchmarks.data import CATALOG_PATH, QUERIES_PATH, Query, load_catalog, load_queries  # noqa: E402
from benchmarks.scoring import real_tokens, recall_at_k  # noqa: E402
from toolsieve.aggregator import AggregatedTool  # noqa: E402
from toolsieve.router import EMBED_MODEL, Router, saved_pct, tool_payload  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "results.json"
CATALOG_SIZES = [10, 25, 50, 100, None]  # None = the full catalog
TOP_K = 3
PAYLOAD_KEYS = ("server", "name", "description", "input_schema")

# How many find_tools calls a query costs when the right tool is not even named
# in the response, so the client can only reword and search again. Observed in
# the pre-release smoke test: four searches to land three downstream calls.
#
# This is the one modelled number here, and it is the reason `tokens_per_call`
# must not be read as the headline: a shape that answers cheaply but misses
# often looks best per call and is worst per resolved query. Sensitivity is
# small — the ranking of shapes is unchanged for any value from 2 upward.
MISS_CALLS = 4

# Measured tokens per tool schema, for repricing (see `sensitivity`). The
# benchmark catalog's own schemas are condensed from public docs and come out
# far smaller than the servers people actually connect, which matters because
# the schema term is exactly what a response shape trades against. Repricing it
# is not a hypothetical: both larger figures were measured on real servers.
SCHEMA_SIZES = {
    "this catalog": None,  # whatever catalog.json actually carries
    "live public MCP servers": 154,  # everything + filesystem + memory + sequential-thinking
    "GitHub-heavy catalog": 269,  # 61 tools, tiktoken, the pre-release smoke test
}


class RouterLike(Protocol):
    def find(self, query: str, k: int = 3, exclude: list[str] | None = None) -> dict[str, Any]: ...


def subsample(tools: list[AggregatedTool], size: int | None) -> list[AggregatedTool]:
    """Take `size` tools spread round-robin across servers, deterministically.

    Truncating the list head-first would hand a size-10 catalog ten GitHub tools
    and nothing else, making the small-catalog rows a test of one server's
    vocabulary rather than of routing. Round-robin keeps every size realistic.
    """
    if size is None or size >= len(tools):
        return list(tools)
    by_server: dict[str, list[AggregatedTool]] = defaultdict(list)
    for tool in tools:
        by_server[tool.server].append(tool)
    rounds = itertools.zip_longest(*by_server.values())
    flat = [t for group in rounds for t in group if t is not None]
    return flat[:size]


def as_payloads(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip toolsieve's own `score`/`confidence` annotations before counting.

    A naive client would never have loaded those, so counting them would
    understate the saving — the same correction `Router` makes internally.
    """
    return [{k: m[k] for k in PAYLOAD_KEYS} for m in matches]


def delivered(result: dict[str, Any]) -> tuple[list[dict[str, Any]], set[tuple[str, str]]]:
    """Split a response into what the client can *call* and what it can *see*.

    These are the two things that decide how many find_tools round trips a task
    costs, and they are not the same number (D20). A schema in hand means the
    next call is the real work; a bare name means one cheap exact lookup first;
    neither means reformulating a query blind, which is what the pre-release
    smoke test caught costing four searches to make three calls.

    Baselines return `matches` — every tool they deliver carries a schema, so
    for them the two sets coincide.
    """
    if "matches" in result:
        payloads = as_payloads(result["matches"])
        return payloads, {(m["server"], m["name"]) for m in payloads}

    tool = result.get("tool")
    payloads = [{k: tool[k] for k in PAYLOAD_KEYS}] if tool else []
    visible = {(t["server"], t["name"]) for t in result.get("alternatives", [])}
    for server, names in result.get("also_available", {}).items():
        visible |= {(server, name) for name in names}
    if tool:
        visible.add((tool["server"], tool["name"]))
    return payloads, visible


def response_tokens(result: dict[str, Any]) -> int:
    """Every token the response puts in the client's context.

    Counts the roster and the headlines, not just the schemas. Charging only for
    schemas would let a method hide real cost in the parts that are cheap per
    item but not free — and the receipt is the product's actual claim.
    """
    if "matches" in result:
        return real_tokens(as_payloads(result["matches"]))
    body = [result.get("tool"), result.get("alternatives"), result.get("also_available")]
    return len(_json_tokens(body))


def _json_tokens(obj: Any) -> list[int]:
    from benchmarks.scoring import _encoder

    return _encoder().encode(json.dumps(obj))


def score_method(
    router: RouterLike, queries: list[Query], naive_tokens: int
) -> dict[str, Any]:
    """One results row: what the method found, and what the response cost."""
    callable_hits: list[float] = []
    visible_hits: list[float] = []
    by_difficulty: dict[str, list[float]] = defaultdict(list)
    sizes: list[int] = []
    actual_tokens = 0
    # Tracked so the response can be re-priced for a different catalog's tools
    # without re-running. Counted as the *whole* delivered payload — server,
    # name, description and schema — because that is how the comparison sizes in
    # SCHEMA_SIZES were measured. Swapping a whole-payload figure in for a
    # schema-only one would silently shift the crossover.
    schema_tokens = 0
    schema_count = 0

    for query in queries:
        result = router.find(query.text, k=TOP_K)
        payloads, visible = delivered(result)
        # k = tools delivered with a schema (D7). Uniform across methods.
        hit = recall_at_k(payloads, query.expected, k=len(payloads))
        callable_hits.append(hit)
        visible_hits.append(1.0 if query.expected in visible else 0.0)
        by_difficulty[query.difficulty].append(hit)
        sizes.append(len(payloads))
        actual_tokens += response_tokens(result)
        schema_tokens += real_tokens(payloads)
        schema_count += len(payloads)

    naive_total = naive_tokens * len(queries)
    per_call = actual_tokens / max(1, len(queries))
    # Expected find_tools calls to actually resolve a query: one if the schema
    # arrived, two if only the name did (a deterministic exact-name lookup
    # follows), MISS_CALLS if neither.
    callable_r, visible_r = mean(callable_hits), mean(visible_hits)
    expected_calls = (
        callable_r + (visible_r - callable_r) * 2 + (1 - visible_r) * MISS_CALLS
    )
    return {
        "queries": len(queries),
        "k": round(mean(sizes), 1),
        # Right tool, schema in hand, on call #1 — the task proceeds immediately.
        "recall_at_k": round(callable_r, 4),
        # Right tool at least *nameable* from call #1 — worst case one exact
        # lookup, never a blind reformulation.
        "recall_visible": round(visible_r, 4),
        "recall_by_difficulty": {
            tier: round(mean(scores), 4) for tier, scores in sorted(by_difficulty.items())
        },
        "tokens_if_naive": naive_total,
        "tokens_actual": actual_tokens,
        "tokens_per_call": round(per_call),
        "expected_calls": round(expected_calls, 3),
        # The number to compare shapes on. See MISS_CALLS.
        "tokens_per_resolution": round(per_call * expected_calls),
        # Loading the catalog is a one-off; routing is charged per query. So the
        # comparison is not "cheaper", it is "cheaper for how long" — this is how
        # many tool lookups a session can make before the sieve has cost as much
        # as the catalog it avoided. Reported because it is the honest limit of
        # the token claim, and because it is the question the pre-release smoke
        # test raised and could not answer.
        "break_even_lookups": round(naive_tokens / max(1.0, per_call * expected_calls), 1),
        "tokens_saved_pct": saved_pct(naive_total, actual_tokens),
        "schema_tokens": schema_tokens,
        "schema_count": schema_count,
        "repriced": {
            label: reprice(
                actual_tokens, schema_tokens, schema_count, len(queries), expected_calls, size
            )
            for label, size in SCHEMA_SIZES.items()
            if size is not None
        },
    }


def reprice(
    actual_tokens: int,
    schema_tokens: int,
    schema_count: int,
    queries: int,
    expected_calls: float,
    schema_size: int,
) -> int:
    """Tokens per resolved query if each delivered tool cost `schema_size` tokens.

    Swaps only the delivered-payload term. Headlines and roster names are left
    exactly as measured, and recall is untouched because the router ranks on
    name + description and never reads a schema. So this is a change of price,
    not of behaviour — and price is the axis on which the shipped shape and the
    v0.2 shape actually trade against each other.
    """
    rest = actual_tokens - schema_tokens
    at_size = rest + schema_count * schema_size
    return round(at_size / max(1, queries) * expected_calls)


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def run(
    catalog: list[AggregatedTool],
    queries: list[Query],
    sizes: list[int | None] = CATALOG_SIZES,
    embedder: Any = None,
    verbose: bool = False,
) -> list[dict[str, Any]]:
    """Score every method at every size. `embedder` is passed straight to
    `Router`, which is how the unit test avoids a model download (D5)."""
    rows: list[dict[str, Any]] = []

    for size in sizes:
        tools = subsample(catalog, size)
        # Queries whose ground-truth tool was sampled out cannot be answered by
        # any method, so scoring them would drag every row toward zero and say
        # nothing about routing. Each size is scored on its answerable subset,
        # and the row records how many that was.
        present = {(t.server, t.name) for t in tools}
        answerable = [q for q in queries if q.expected in present]
        naive_tokens = real_tokens([tool_payload(t) for t in tools])

        methods: dict[str, RouterLike] = {
            "naive": NaiveRouter(tools),
            "bm25": BM25Router(tools),
            "toolsieve-v0.2": LegacyToolsieve(tools, embedder=embedder),
            "toolsieve": Router(tools, embedder=embedder),
        }
        for name, router in methods.items():
            started = time.monotonic()
            row = {"catalog_size": len(tools), "method": name, **score_method(router, answerable, naive_tokens)}
            row["seconds"] = round(time.monotonic() - started, 2)
            rows.append(row)
            if verbose:
                print(
                    f"  {name:<15} callable={row['recall_at_k']:.3f} "
                    f"visible={row['recall_visible']:.3f} "
                    f"{row['tokens_per_call']:>6}tok/call ({row['seconds']}s)"
                )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=RESULTS_PATH)
    args = parser.parse_args()

    catalog = load_catalog()
    queries = load_queries()
    print(f"catalog: {len(catalog)} tools / {len({t.server for t in catalog})} servers")
    print(f"queries: {len(queries)}")

    rows = run(catalog, queries, verbose=True)
    payload = {
        "top_k": TOP_K,
        "catalog_tools": len(catalog),
        "catalog_servers": len({t.server for t in catalog}),
        "queries": len(queries),
        "tokenizer": "tiktoken/cl100k_base",
        "embed_model": EMBED_MODEL,
        "schema_sizes": {k: v for k, v in SCHEMA_SIZES.items() if v is not None},
        "miss_calls": MISS_CALLS,
        "sources": {"catalog": CATALOG_PATH.name, "queries": QUERIES_PATH.name},
        "rows": rows,
    }
    args.out.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
