"""Baseline routers. Model-free, runs in normal CI (D5)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.baselines import BM25Router, NaiveRouter  # noqa: E402
from toolsieve.aggregator import AggregatedTool  # noqa: E402

TOOLS = [
    AggregatedTool("github", "create_issue", "Create a new issue in a repository", {}),
    AggregatedTool("slack", "post_message", "Post a message to a Slack channel", {}),
    AggregatedTool("time", "get_current_time", "Get the current time in a timezone", {}),
    AggregatedTool("maps", "geocode", "Convert an address into coordinates", {}),
]


@pytest.fixture(params=[NaiveRouter, BM25Router])
def router(request):
    return request.param(TOOLS)


def test_every_router_returns_the_router_find_shape(router):
    """Spec: same `{"matches": [{"server", "name", ...}]}` shape as toolsieve."""
    result = router.find("post a message", k=3)
    assert set(result) >= {"matches", "savings"}
    for match in result["matches"]:
        assert {"server", "name", "description", "input_schema"} <= set(match)


def test_naive_delivers_the_whole_catalog_regardless_of_k():
    """D7: no ranking — the client gets everything, which is the point of naive."""
    result = NaiveRouter(TOOLS).find("post a message", k=3)
    assert len(result["matches"]) == len(TOOLS)
    assert {(m["server"], m["name"]) for m in result["matches"]} == {
        (t.server, t.name) for t in TOOLS
    }


def test_naive_saves_nothing():
    savings = NaiveRouter(TOOLS).find("anything", k=3)["savings"]
    assert savings["tokens_actual"] == savings["tokens_if_naive"]
    assert savings["saved_pct"] == 0.0


def test_bm25_ranks_the_keyword_match_first():
    matches = BM25Router(TOOLS).find("post a message to a channel", k=2)["matches"]
    assert (matches[0]["server"], matches[0]["name"]) == ("slack", "post_message")


def test_bm25_respects_k():
    assert len(BM25Router(TOOLS).find("message", k=2)["matches"]) == 2


def test_bm25_returns_fewer_than_k_only_when_the_catalog_is_smaller(router):
    assert len(router.find("message", k=99)["matches"]) == len(TOOLS)


def test_bm25_saves_tokens_against_the_full_catalog():
    savings = BM25Router(TOOLS).find("post a message", k=1)["savings"]
    assert savings["tokens_actual"] < savings["tokens_if_naive"]
    assert savings["saved_pct"] > 0


def test_routers_handle_an_empty_catalog(router):
    empty = type(router)([])
    result = empty.find("anything", k=3)
    assert result["matches"] == []
    assert result["savings"]["saved_pct"] == 0.0


def test_bm25_has_no_keyword_overlap_blind_spot_hidden_by_the_test_fixture():
    """The paraphrase tier exists because this is BM25's real weakness."""
    matches = BM25Router(TOOLS).find("what o'clock is it in Berlin", k=1)["matches"]
    # No shared term with "Get the current time in a timezone" — BM25 cannot find it.
    assert (matches[0]["server"], matches[0]["name"]) != ("time", "get_current_time")
