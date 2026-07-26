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

from benchmarks.baselines import BM25Router, NaiveRouter  # noqa: E402
from benchmarks.data import CATALOG_PATH, QUERIES_PATH, Query, load_catalog, load_queries  # noqa: E402
from benchmarks.scoring import real_tokens, recall_at_k  # noqa: E402
from toolsieve.aggregator import AggregatedTool  # noqa: E402
from toolsieve.router import EMBED_MODEL, Router, saved_pct, tool_payload  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "results.json"
CATALOG_SIZES = [10, 25, 50, 100, None]  # None = the full catalog
TOP_K = 3
PAYLOAD_KEYS = ("server", "name", "description", "input_schema")


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


def score_method(
    router: RouterLike, queries: list[Query], naive_tokens: int
) -> dict[str, Any]:
    """One results row: recall and real token cost for this method at this size."""
    hits: list[float] = []
    by_difficulty: dict[str, list[float]] = defaultdict(list)
    delivered: list[int] = []
    actual_tokens = 0

    for query in queries:
        matches = router.find(query.text, k=TOP_K)["matches"]
        payloads = as_payloads(matches)
        # k = tools delivered (D7). Uniform across methods; no branching.
        hit = recall_at_k(payloads, query.expected, k=len(payloads))
        hits.append(hit)
        by_difficulty[query.difficulty].append(hit)
        delivered.append(len(payloads))
        actual_tokens += real_tokens(payloads)

    naive_total = naive_tokens * len(queries)
    return {
        "queries": len(queries),
        "k": round(mean(delivered), 1),
        "recall_at_k": round(mean(hits), 4),
        "recall_by_difficulty": {
            tier: round(mean(scores), 4) for tier, scores in sorted(by_difficulty.items())
        },
        "tokens_if_naive": naive_total,
        "tokens_actual": actual_tokens,
        "tokens_saved_pct": saved_pct(naive_total, actual_tokens),
    }


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
            "toolsieve": Router(tools, embedder=embedder),
        }
        for name, router in methods.items():
            started = time.monotonic()
            row = {"catalog_size": len(tools), "method": name, **score_method(router, answerable, naive_tokens)}
            row["seconds"] = round(time.monotonic() - started, 2)
            rows.append(row)
            if verbose:
                print(
                    f"  {name:<10} recall={row['recall_at_k']:.3f} "
                    f"saved={row['tokens_saved_pct']:.1f}% ({row['seconds']}s)"
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
        "sources": {"catalog": CATALOG_PATH.name, "queries": QUERIES_PATH.name},
        "rows": rows,
    }
    args.out.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
