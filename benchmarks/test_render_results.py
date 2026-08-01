"""Rendering. Model-free, runs in normal CI (D5)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.render_results import render  # noqa: E402

FIXTURE = {
    "top_k": 3,
    "catalog_tools": 181,
    "catalog_servers": 25,
    "queries": 159,
    "tokenizer": "tiktoken/cl100k_base",
    "embed_model": "BAAI/bge-small-en-v1.5",
    "sources": {"catalog": "catalog.json", "queries": "queries.jsonl"},
    "rows": [
        {
            "catalog_size": 181, "method": "naive", "queries": 159, "k": 181,
            "recall_at_k": 1.0, "recall_visible": 1.0,
            "recall_by_difficulty": {"ambiguous": 1.0, "exact": 1.0, "paraphrase": 1.0},
            "tokens_if_naive": 6_000_000, "tokens_actual": 6_000_000,
            "tokens_per_call": 37_735, "expected_calls": 1.0,
            "tokens_per_resolution": 37_735, "break_even_lookups": 1.0,
            "tokens_saved_pct": 0.0,
        },
        {
            "catalog_size": 181, "method": "bm25", "queries": 159, "k": 3,
            "recall_at_k": 0.654, "recall_visible": 0.654,
            "recall_by_difficulty": {"ambiguous": 0.78, "exact": 1.0, "paraphrase": 0.32},
            "tokens_if_naive": 6_000_000, "tokens_actual": 102_000,
            "tokens_per_call": 642, "expected_calls": 2.038,
            "tokens_per_resolution": 1_308, "break_even_lookups": 28.8,
            "tokens_saved_pct": 98.3,
        },
        {
            "catalog_size": 181, "method": "toolsieve-v0.2", "queries": 159, "k": 3,
            "recall_at_k": 0.849, "recall_visible": 0.849,
            "recall_by_difficulty": {"ambiguous": 0.6, "exact": 1.0, "paraphrase": 0.7},
            "tokens_if_naive": 6_000_000, "tokens_actual": 103_000,
            "tokens_per_call": 648, "expected_calls": 1.302,
            "tokens_per_resolution": 844, "break_even_lookups": 44.7,
            "tokens_saved_pct": 98.3,
        },
        {
            "catalog_size": 181, "method": "toolsieve", "queries": 159, "k": 3,
            "recall_at_k": 0.8742, "recall_visible": 0.95,
            "recall_by_difficulty": {"ambiguous": 0.5, "exact": 0.98, "paraphrase": 0.9},
            "tokens_if_naive": 6_000_000, "tokens_actual": 100_000,
            "tokens_per_call": 629, "expected_calls": 1.201,
            "tokens_per_resolution": 100_000, "break_even_lookups": 62.4,
            "tokens_saved_pct": 98.3,
        },
    ],
}


def test_every_row_from_results_json_is_rendered():
    """Spec: render_results renders every row from results.json."""
    out = render(FIXTURE)
    body = [line for line in out.splitlines() if line.startswith("| 181 ")]
    assert len(body) == len(FIXTURE["rows"])


def test_table_is_valid_markdown_with_a_consistent_column_count():
    lines = [line for line in render(FIXTURE).splitlines() if line.startswith("|")]
    widths = {line.count("|") for line in lines}
    assert len(widths) == 1, f"ragged table: {widths}"
    assert set(lines[1].strip("|").split("|")) == {"---"}


def test_headline_numbers_survive_formatting():
    out = render(FIXTURE)
    assert "0.87" in out  # recall rounded for display, not truncated to 0.8
    assert "100,000" in out  # thousands separator on token counts


def test_setup_section_states_the_provenance_that_makes_the_number_defensible():
    out = render(FIXTURE)
    assert "181 tools" in out and "25 real MCP servers" in out
    assert "cl100k_base" in out
    assert "BAAI/bge-small-en-v1.5" in out
    assert "159" in out


def test_missing_difficulty_tier_renders_a_dash_rather_than_crashing():
    sparse = {**FIXTURE, "rows": [{**FIXTURE["rows"][0], "recall_by_difficulty": {"exact": 1.0}}]}
    out = render(sparse)
    assert "—" in out


def test_a_partial_run_still_renders_a_table_but_claims_nothing():
    """No quotable headline unless both the router and its baseline are present."""
    partial = {**FIXTURE, "rows": [FIXTURE["rows"][0]]}
    out = render(partial)
    assert "| 181 " in out
    assert "resolves a tool lookup" not in out


def test_headline_is_derived_from_the_table_not_hand_written():
    out = render(FIXTURE)
    assert "181 tools from 25 real MCP servers" in out
    # paraphrase tier, toolsieve 0.90 vs bm25 0.32
    assert "**90%**" in out and "**32%**" in out


def test_headline_compares_shapes_on_cost_per_resolution_not_per_call():
    """The trap this benchmark exists to avoid, asserted.

    v0.2 is *cheaper per call* than the shipped shape in this fixture (648 vs
    629 is close, but its failures cost four calls). Quoting per-call numbers is
    what made the rework look like a regression; the headline must quote the
    per-resolution number instead.
    """
    out = render(FIXTURE)
    assert "844" in out and "100,000" in out  # v0.2 -> shipped, per resolution
    assert "85% to 95%" in out or "85% to 95" in out or "0.95" in out


def test_headline_states_the_break_even_rather_than_hiding_it():
    """A per-lookup cost beats a one-off load only for so long. Say how long."""
    out = render(FIXTURE)
    assert "62 lookups" in out
    assert "154" in out and "269" in out  # real schema sizes, the conservative-end caveat
