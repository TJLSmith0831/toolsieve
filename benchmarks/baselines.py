"""The three things toolsieve claims to beat: no routing, lexical routing, and
its own previous release.

All expose `find(query, k)` returning a `matches` list, which the orchestrator
adapts alongside the shipped router's richer shape (D20) through one scoring
path.

Token counts here use `real_tokens` (tiktoken, D4) rather than the router's
chars/4 estimator, so every cell of the results table is comparable.
"""

from __future__ import annotations

import re
from typing import Any

from rank_bm25 import BM25Okapi

from toolsieve.aggregator import AggregatedTool
from toolsieve.router import Router, saved_pct, tool_payload

from .scoring import real_tokens

_WORD = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase word split, also breaking snake_case/camelCase tool names.

    Without the split, `post_message` is one opaque term and BM25 can never
    match the word "message" — which would hand toolsieve an unearned win on
    exactly the queries BM25 should be good at.
    """
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text).replace("_", " ")
    return _WORD.findall(spaced.lower())


class _Baseline:
    """Shared plumbing: the savings receipt, computed the same way for both."""

    def __init__(self, tools: list[AggregatedTool]) -> None:
        self.tools = tools
        self.naive_tokens = real_tokens([tool_payload(t) for t in tools])

    def _result(self, selected: list[AggregatedTool]) -> dict[str, Any]:
        payloads = [tool_payload(t) for t in selected]
        actual = real_tokens(payloads)
        return {
            "matches": payloads,
            "savings": {
                "tokens_if_naive": self.naive_tokens,
                "tokens_actual": actual,
                "saved_pct": saved_pct(self.naive_tokens, actual),
            },
        }


class NaiveRouter(_Baseline):
    """No routing: every aggregated tool goes into the client's context.

    Ignores `k` on purpose (D7). This is what an MCP client does today with no
    sieve in front of it, and the reason its recall is scored at the number of
    tools it delivered rather than at 3 — it hands over everything, unranked.
    """

    def find(self, query: str, k: int = 3, exclude: list[str] | None = None) -> dict[str, Any]:
        return self._result(self.tools)


class BM25Router(_Baseline):
    """Lexical routing via `rank-bm25` (D4) over each tool's name + description.

    Indexes `match_text` — the same string toolsieve's router embeds — so the
    two methods see identical input and differ only in how they match it.
    """

    def __init__(self, tools: list[AggregatedTool]) -> None:
        super().__init__(tools)
        corpus = [tokenize(t.match_text) for t in tools]
        # BM25Okapi rejects an empty corpus (division by zero on avgdl).
        self._bm25 = BM25Okapi(corpus) if corpus else None

    def find(self, query: str, k: int = 3, exclude: list[str] | None = None) -> dict[str, Any]:
        if self._bm25 is None or not query.strip():
            return self._result([])
        scores = self._bm25.get_scores(tokenize(query))
        ranked = sorted(range(len(self.tools)), key=lambda i: -scores[i])[: max(1, k)]
        return self._result([self.tools[i] for i in ranked])


class SchemaPerMatchRouter(_Baseline):
    """toolsieve v0.2: the same embeddings, the response shape D20 replaced.

    Identical ranking to the shipped router — it reuses `Router.rank` — so any
    difference between this row and the `toolsieve` row is attributable to the
    response shape alone, not to a change in matching. That is the point of
    including it: the rework's claim is about what a response *costs* and what
    it lets a client conclude, and this isolates exactly that.
    """

    def __init__(self, tools: list[AggregatedTool], embedder: Any = None) -> None:
        super().__init__(tools)
        self._router = Router(tools, embedder=embedder)

    def find(self, query: str, k: int = 3, exclude: list[str] | None = None) -> dict[str, Any]:
        ranked = self._router.rank(query, exclude=exclude)
        return self._result([t for t, _ in ranked[: max(1, k)]])
