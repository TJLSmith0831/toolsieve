"""Scoring maths. Model-free, so it runs in normal CI (D5)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.scoring import real_tokens, recall_at_k  # noqa: E402
from toolsieve.router import saved_pct  # noqa: E402

MATCHES = [
    {"server": "github", "name": "create_issue", "description": "..."},
    {"server": "slack", "name": "post_message", "description": "..."},
    {"server": "linear", "name": "list_issues", "description": "..."},
]


def test_recall_hits_when_expected_is_in_top_k():
    assert recall_at_k(MATCHES, ("slack", "post_message"), k=3) == 1.0


def test_recall_misses_when_expected_is_absent():
    assert recall_at_k(MATCHES, ("notion", "search"), k=3) == 0.0


def test_recall_respects_k_smaller_than_the_match_list():
    """A method returning extra matches must not get credit for them at k=1."""
    assert recall_at_k(MATCHES, ("linear", "list_issues"), k=1) == 0.0
    assert recall_at_k(MATCHES, ("github", "create_issue"), k=1) == 1.0


def test_recall_is_server_qualified():
    """Same tool name on a different server is a miss, not a hit."""
    assert recall_at_k(MATCHES, ("gitlab", "create_issue"), k=3) == 0.0


def test_recall_on_no_matches_is_zero():
    assert recall_at_k([], ("github", "create_issue"), k=3) == 0.0


def test_real_tokens_uses_a_real_tokenizer_not_chars_over_four():
    """D4: the marketing number comes from tiktoken, not the runtime estimate."""
    payloads = [{"server": "github", "name": "create_issue", "description": "Create a new issue"}]
    tokens = real_tokens(payloads)
    assert tokens > 0
    # chars/4 on this payload lands ~20; a real tokenizer must not merely echo it.
    assert tokens != len(str(payloads)) // 4


def test_real_tokens_grows_with_payload_count():
    one = real_tokens([{"server": "a", "name": "b", "description": "c" * 200}])
    two = real_tokens([{"server": "a", "name": "b", "description": "c" * 200}] * 2)
    assert two > one


def test_real_tokens_on_empty_payloads_is_small_and_nonnegative():
    assert real_tokens([]) >= 0


@pytest.mark.parametrize(
    ("naive", "actual", "expected"),
    [(1000, 100, 90.0), (0, 0, 0.0), (100, 100, 0.0)],
)
def test_saved_pct_is_the_shipped_one(naive, actual, expected):
    """Reused from toolsieve.router, not reimplemented — same maths as the receipt."""
    assert saved_pct(naive, actual) == expected
