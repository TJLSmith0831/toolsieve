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


METHODS = ("naive", "bm25", "toolsieve-v0.2", "toolsieve")


def test_one_row_per_method_per_catalog_size(rows):
    """Spec: `results.json` contains one row per (catalog size, method) pair."""
    assert len(rows) == 2 * len(METHODS)
    assert {(r["catalog_size"], r["method"]) for r in rows} == {
        (size, method) for size in (3, len(TOOLS)) for method in METHODS
    }


def test_every_row_carries_recall_and_tokens_saved(rows):
    for row in rows:
        assert 0.0 <= row["recall_at_k"] <= 1.0
        assert 0.0 <= row["recall_visible"] <= 1.0
        # Not bounded below at 0: a catalog smaller than one response costs more
        # to route than to hand over whole. See the size-3 rows.
        assert row["tokens_saved_pct"] <= 100.0
        assert row["tokens_if_naive"] > 0
        assert row["recall_by_difficulty"]


def test_a_tool_is_never_callable_without_being_visible(rows):
    """`recall_visible` counts names *and* schemas, so it can only be the larger.

    If this inverts, `delivered()` has stopped folding the top match into the
    visible set and every visible-recall number in RESULTS.md is understated.
    """
    for row in rows:
        assert row["recall_visible"] >= row["recall_at_k"], row["method"]


def test_naive_delivers_everything_so_it_never_misses_and_never_saves(rows):
    """D7: naive is scored at k = the whole catalog it dumped."""
    for row in [r for r in rows if r["method"] == "naive"]:
        assert row["recall_at_k"] == 1.0
        assert row["tokens_saved_pct"] == 0.0
        assert row["k"] == row["catalog_size"]


def test_ranked_methods_deliver_top_k_and_save_once_the_catalog_exceeds_it(rows):
    """A catalog of 3 routed to 3 saves nothing — that is the whole reason the
    benchmark sweeps catalog sizes rather than quoting one."""
    for row in [r for r in rows if r["method"] in ("bm25", "toolsieve-v0.2")]:
        assert row["k"] <= 3
        assert row["tokens_saved_pct"] > 0 if row["catalog_size"] > 3 else True


def test_sieving_a_tiny_catalog_costs_more_than_it_saves(rows):
    """Routing has a floor price, and below it the honest answer is "don't".

    The shipped router spends tokens on a roster and headlines that a 3-tool
    catalog does not need — it would have been cheaper to hand all 3 over. This
    is asserted rather than hidden because the README's "when not to use this"
    has to stay true, and because a silently-negative saving is exactly the kind
    of number a benchmark should refuse to round up to zero.
    """
    tiny = next(r for r in rows if r["method"] == "toolsieve" and r["catalog_size"] == 3)
    assert tiny["tokens_saved_pct"] < 0
    big = next(r for r in rows if r["method"] == "toolsieve" and r["catalog_size"] == len(TOOLS))
    assert big["tokens_saved_pct"] > tiny["tokens_saved_pct"]


def test_shipped_shape_returns_one_schema_and_sees_more_than_it_delivers(rows):
    """The D20 trade, in one assertion: fewer schemas, wider visibility."""
    new = next(r for r in rows if r["method"] == "toolsieve" and r["catalog_size"] == len(TOOLS))
    old = next(r for r in rows if r["method"] == "toolsieve-v0.2" and r["catalog_size"] == len(TOOLS))
    assert new["k"] == 1.0, "only the top match ships with a schema"
    assert old["k"] > new["k"], "v0.2 shipped a schema per match"
    assert new["recall_visible"] >= old["recall_visible"]


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
