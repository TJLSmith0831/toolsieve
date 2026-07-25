"""Semantic matching and the savings receipt (D9, D11, D12).

Embeds each aggregated tool's own name + description once per catalog, then
answers queries with a cosine top-k. No hand-authored utterances, no LLM call,
no API key needed to build the index (D9 amended).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
from fastembed import TextEmbedding

from .aggregator import AggregatedTool

log = logging.getLogger("toolsieve")

EMBED_MODEL = "BAAI/bge-small-en-v1.5"

# Confidence threshold, NOT a rejection gate (D11 amended). Measured over 20
# queries on bge-small against a real catalog: on-topic scored 0.5604-0.8281,
# off-topic 0.3836-0.5500 — the clusters overlap to within 0.0104, so no floor
# separates them. (BGE's query-instruction prefix was tried and made it worse.)
# Anything below this is still returned, just flagged, because telling a client
# "nothing matched" when a usable tool exists is the worse failure.
DEFAULT_CONFIDENCE_THRESHOLD = 0.70

# ponytail: chars/4 token estimate — no tokenizer dep, and it cancels out of
# saved_pct entirely (both sides use the same estimator), which is the number
# the receipt actually leads with. Absolute counts are labelled as estimates.
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN) if text else 0


def tool_payload(tool: AggregatedTool) -> dict[str, Any]:
    """What find_tools hands back per match — and what gets token-counted."""
    return {
        "server": tool.server,
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.input_schema,
    }


def payload_tokens(payloads: list[dict[str, Any]]) -> int:
    return estimate_tokens(json.dumps(payloads))


def _matched_tools(
    candidates: list[AggregatedTool], matches: list[dict[str, Any]]
) -> list[AggregatedTool]:
    keys = {(m["server"], m["name"]) for m in matches}
    return [t for t in candidates if (t.server, t.name) in keys]


@dataclass
class Savings:
    """Running session totals for get_savings_report (D12)."""

    calls: int = 0
    tokens_if_naive: int = 0
    tokens_actual: int = 0

    def record(self, naive: int, actual: int) -> None:
        self.calls += 1
        self.tokens_if_naive += naive
        self.tokens_actual += actual

    def report(self) -> dict[str, Any]:
        return {
            "find_tools_calls": self.calls,
            "tokens_if_naive": self.tokens_if_naive,
            "tokens_actual": self.tokens_actual,
            "tokens_saved": self.tokens_if_naive - self.tokens_actual,
            "saved_pct": saved_pct(self.tokens_if_naive, self.tokens_actual),
            "note": "Token counts are estimates (~4 chars/token); saved_pct is exact.",
        }


def saved_pct(naive: int, actual: int) -> float:
    if naive <= 0:
        return 0.0
    return round((naive - actual) / naive * 100, 1)


class Router:
    """An embedding index over one catalog snapshot."""

    def __init__(
        self,
        tools: list[AggregatedTool],
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
        embedder: TextEmbedding | None = None,
    ) -> None:
        self.tools = tools
        self.confidence_threshold = confidence_threshold
        self._embedder = embedder or TextEmbedding(model_name=EMBED_MODEL)
        self._vectors = self._embed([t.match_text for t in tools])
        # Naive cost = every aggregated tool's full schema in the client's context,
        # which is exactly what toolsieve exists to avoid (D12).
        self.naive_tokens = payload_tokens([tool_payload(t) for t in tools])
        log.info("indexed %d tools (naive catalog ~%d tokens)", len(tools), self.naive_tokens)

    def _embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 1), dtype=np.float32)
        vectors = np.asarray(list(self._embedder.embed(texts)), dtype=np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors / np.maximum(norms, 1e-12)

    def find(
        self, query: str, k: int = 3, exclude: list[str] | None = None
    ) -> dict[str, Any]:
        """Top-k semantically matching tools (D11 amended).

        Always returns the best available matches. A match below the confidence
        threshold is returned and flagged, not withheld — only an explicit
        `exclude` entry ("server/tool_name") keeps a tool out.
        """
        rejected = set(exclude or [])
        candidates = [t for t in self.tools if f"{t.server}/{t.name}" not in rejected]

        matches: list[dict[str, Any]] = []
        if candidates and query.strip():
            vectors = self._vectors
            if len(candidates) != len(self.tools):
                keep = [i for i, t in enumerate(self.tools) if f"{t.server}/{t.name}" not in rejected]
                vectors = self._vectors[keep]
            scores = vectors @ self._embed([query])[0]
            for idx in np.argsort(-scores)[: max(1, k)]:
                score = float(scores[idx])
                match = {**tool_payload(candidates[idx]), "score": round(score, 4)}
                if score < self.confidence_threshold:
                    match["confidence"] = "low"
                matches.append(match)

        # Count the same shape on both sides. `score`/`confidence` are toolsieve's
        # own annotations, not part of what a naive client would have loaded, so
        # including them would understate the saving (and can invert it on a tiny
        # catalog, which is how this was caught).
        actual = payload_tokens([tool_payload(t) for t in _matched_tools(candidates, matches)])
        result: dict[str, Any] = {
            "matches": matches,
            "savings": {
                "tokens_if_naive": self.naive_tokens,
                "tokens_actual": actual,
                "saved_pct": saved_pct(self.naive_tokens, actual),
            },
        }

        if not matches:
            result["message"] = (
                "No tools available to match against — the aggregated catalog is "
                "empty, every candidate was excluded, or the query was blank."
            )
        elif all(m.get("confidence") == "low" for m in matches):
            result["message"] = (
                f"Low confidence: the best match scored {matches[0]['score']}, below "
                f"{self.confidence_threshold}. It is still the closest tool available — "
                "check its description and schema before using it. If it is wrong, call "
                "find_tools again with exclude=['<server>/<tool_name>']."
            )
            log.info("low-confidence match for %r (best %.4f)", query, matches[0]["score"])
        return result
