"""Dataset integrity. Model-free, runs in normal CI (D5).

These are the checks that keep the published numbers honest: a query pointing at
a tool that does not exist would silently score 0 for every method, and a
shrunken catalog would quietly weaken the "at least 150 real tools" claim.
"""

from __future__ import annotations

import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.data import load_catalog, load_queries  # noqa: E402

CATALOG = load_catalog()
QUERIES = load_queries()
DIFFICULTIES = {"exact", "paraphrase", "ambiguous"}


def test_catalog_reaches_benchmark_relevant_scale():
    """Spec: at least 150 tools across at least 20 distinct servers."""
    assert len(CATALOG) >= 150
    assert len({t.server for t in CATALOG}) >= 20


def test_no_duplicate_tool_names_within_a_server():
    counts = collections.Counter((t.server, t.name) for t in CATALOG)
    assert [key for key, n in counts.items() if n > 1] == []


def test_every_tool_has_a_description_to_match_on():
    """An empty description would match poorly and skew every method's recall."""
    assert [t.name for t in CATALOG if not t.description.strip()] == []


def test_query_set_is_large_enough_to_be_meaningful():
    """Floor only. tasks.md said ~100-150; the set landed at 159 because
    covering all 25 servers across three tiers needed the extra queries, and
    dropping real coverage to hit an approximate number would only make the
    result noisier. Balance across tiers is guarded by the spread test below.
    """
    assert len(QUERIES) >= 100


def test_every_query_expects_a_tool_that_exists_in_the_catalog():
    known = {(t.server, t.name) for t in CATALOG}
    missing = sorted({q.expected for q in QUERIES} - known)
    assert missing == [], f"queries point at tools not in the catalog: {missing}"


def test_all_three_difficulty_tiers_are_present_and_populated():
    spread = collections.Counter(q.difficulty for q in QUERIES)
    assert set(spread) == DIFFICULTIES
    assert all(count >= 20 for count in spread.values()), spread


def test_queries_are_unique():
    dupes = [q for q, n in collections.Counter(x.text for x in QUERIES).items() if n > 1]
    assert dupes == []
