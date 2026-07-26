"""Orchestrator wiring, with no embedding model involved (D5).

Spec scenario: "Orchestrator wiring is tested with a fake embedder" — injected
through `Router`'s existing `embedder` constructor parameter, so this exercises
the real `Router` code path without a `fastembed` download.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.data import Query  # noqa: E402
from benchmarks.run_benchmark import as_payloads, run, subsample  # noqa: E402
from toolsieve.aggregator import AggregatedTool  # noqa: E402

VOCAB = ["issue", "message", "time", "address", "file", "database"]


class FakeEmbedder:
    """Bag-of-words vectors over a fixed vocabulary.

    Deterministic and dependency-free, but not degenerate: texts sharing
    vocabulary land near each other, so the orchestrator's ranking path is
    actually exercised rather than fed constant vectors.
    """

    def embed(self, texts):
        for text in texts:
            lowered = text.lower()
            vector = [float(word in lowered) for word in VOCAB]
            # Never all-zero: Router normalises, and a zero vector would make
            # every score NaN and quietly turn recall into garbage.
            yield np.array(vector + [0.1], dtype=np.float32)


TOOLS = [
    AggregatedTool("github", "create_issue", "Create a new issue", {"a": 1}),
    AggregatedTool("slack", "post_message", "Post a message", {"a": 1}),
    AggregatedTool("time", "get_current_time", "Get the current time", {"a": 1}),
    AggregatedTool("maps", "geocode", "Convert an address", {"a": 1}),
    AggregatedTool("fs", "read_file", "Read a file", {"a": 1}),
    AggregatedTool("pg", "query", "Query a database", {"a": 1}),
]

QUERIES = [
    Query("create an issue", ("github", "create_issue"), "exact"),
    Query("post a message", ("slack", "post_message"), "exact"),
    Query("what is the time", ("time", "get_current_time"), "paraphrase"),
    Query("look up an address", ("maps", "geocode"), "paraphrase"),
    Query("read a file", ("fs", "read_file"), "ambiguous"),
]


@pytest.fixture(scope="module")
def rows():
    return run(TOOLS, QUERIES, sizes=[3, None], embedder=FakeEmbedder())


def test_one_row_per_method_per_catalog_size(rows):
    """Spec: `results.json` contains one row per (catalog size, method) pair."""
    assert len(rows) == 2 * 3
    assert {(r["catalog_size"], r["method"]) for r in rows} == {
        (size, method)
        for size in (3, len(TOOLS))
        for method in ("naive", "bm25", "toolsieve")
    }


def test_every_row_carries_recall_and_tokens_saved(rows):
    for row in rows:
        assert 0.0 <= row["recall_at_k"] <= 1.0
        assert 0.0 <= row["tokens_saved_pct"] <= 100.0
        assert row["tokens_if_naive"] > 0
        assert row["recall_by_difficulty"]


def test_naive_delivers_everything_so_it_never_misses_and_never_saves(rows):
    """D7: naive is scored at k = the whole catalog it dumped."""
    for row in [r for r in rows if r["method"] == "naive"]:
        assert row["recall_at_k"] == 1.0
        assert row["tokens_saved_pct"] == 0.0
        assert row["k"] == row["catalog_size"]


def test_ranked_methods_deliver_top_k_and_save_once_the_catalog_exceeds_it(rows):
    """A catalog of 3 routed to 3 saves nothing — that is the whole reason the
    benchmark sweeps catalog sizes rather than quoting one."""
    for row in [r for r in rows if r["method"] in ("bm25", "toolsieve")]:
        assert row["k"] <= 3
        if row["catalog_size"] > 3:
            assert row["tokens_saved_pct"] > 0
        else:
            assert row["tokens_saved_pct"] == 0.0


def test_toolsieve_row_used_the_fake_embedder_and_still_ranked(rows):
    """If the fake were wired in wrong, recall would collapse to chance."""
    full = next(r for r in rows if r["method"] == "toolsieve" and r["catalog_size"] == len(TOOLS))
    assert full["recall_at_k"] > 0.5


def test_smaller_catalogs_only_score_answerable_queries(rows):
    """A query whose tool was sampled out is excluded, not counted as a miss."""
    small = next(r for r in rows if r["method"] == "naive" and r["catalog_size"] == 3)
    full = next(r for r in rows if r["method"] == "naive" and r["catalog_size"] == len(TOOLS))
    assert small["queries"] <= full["queries"]
    assert small["queries"] > 0


def test_subsample_spreads_across_servers_rather_than_truncating():
    catalog = [
        AggregatedTool(server, f"tool_{i}", "d", {})
        for server in ("a", "b", "c")
        for i in range(5)
    ]
    picked = subsample(catalog, 3)
    assert len(picked) == 3
    assert {t.server for t in picked} == {"a", "b", "c"}


def test_subsample_of_none_or_oversize_returns_the_whole_catalog():
    assert len(subsample(TOOLS, None)) == len(TOOLS)
    assert len(subsample(TOOLS, 999)) == len(TOOLS)


def test_as_payloads_strips_toolsieve_annotations():
    """`score`/`confidence` are toolsieve's own, not what a client would load."""
    stripped = as_payloads(
        [{"server": "s", "name": "n", "description": "d", "input_schema": {}, "score": 0.9, "confidence": "low"}]
    )
    assert stripped == [{"server": "s", "name": "n", "description": "d", "input_schema": {}}]
