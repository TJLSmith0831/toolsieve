"""Semantic matching and the savings receipt (D9, D11, D12, D20).

Embeds each aggregated tool's own name + description once per catalog, then
answers queries with a cosine top-k. No hand-authored utterances, no LLM call,
no API key needed to build the index (D9 amended).

D20 — what a response carries, and why it is shaped this way. The metric is
tokens per *resolved query*, not per call: v0.2 looked cheap per call precisely
because its failures cost four calls, and a per-call number cannot see that.

- **One schema, not k.** Only the top match is returned callable. A schema is
  ~10x a bare name and dominates the response; recall@1 is 0.69 against
  recall@3's 0.85, so buying 16pt of hit-rate at triple the cost loses once the
  cheaper recovery below exists.
- **`also_available`: tool *names*, ranked, capped.** The v0.2 shape could not
  express "no such tool exists", so a client hunting a plausible-sounding name
  reformulated until it gave up — four queries to land three calls in the
  pre-release smoke test, each paying for five schemas. Names are ~6 tokens, so
  25 of them cost a fraction of one schema and lift the odds the right tool is
  *visible* on call #1 from 0.69 to 0.95 (1.00 on the smoke queries that
  actually failed). Negative knowledge is the cheapest knowledge here: seeing
  what exists is what stops the search.
- **Exact names short-circuit.** A client that spots the tool it wants in
  `also_available` must be able to fetch that schema in one hop, deterministic,
  not by hoping the ranker agrees with a choice it has already made.

Net, priced at real schema sizes (154 tok/tool measured across live public MCP
servers; 269 on the GitHub-heavy catalog the smoke test used), this resolves a
query for 46-63% fewer tokens than the shape it replaces.
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

# ponytail: 25 names, flat. Swept 15/25/40 against measured schema sizes (72
# tok/tool on the benchmark catalog, 154 on real public MCP servers, 269 on the
# GitHub-heavy catalog the smoke test used). 25 minimises expected tokens per
# *resolved query* at every one of those sizes; 40 raises visible recall
# 0.950 -> 0.981 but costs more than the rarer loop it prevents, and 15 saves
# too little to give up the robustness. Ceiling: on a catalog far larger than
# ~200 tools this slice thins out, and the upgrade is per-server quotas so one
# chatty server cannot crowd the roster — not a bigger N, which costs tokens
# linearly for a sub-linear gain.
ROSTER_SIZE = 25

# ponytail: chars/4 token estimate — no tokenizer dep, and it cancels out of
# saved_pct entirely (both sides use the same estimator), which is the number
# the receipt actually leads with. Absolute counts are labelled as estimates.
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN) if text else 0


def tool_payload(tool: AggregatedTool) -> dict[str, Any]:
    """A tool the client can call right now — the expensive shape, with schema."""
    return {
        "server": tool.server,
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.input_schema,
    }


def tool_headline(tool: AggregatedTool) -> dict[str, Any]:
    """A tool the client can *choose*, but not yet call — no schema, ~90% cheaper."""
    return {"server": tool.server, "name": tool.name, "description": tool.description}


def payload_tokens(payloads: list[dict[str, Any]]) -> int:
    return estimate_tokens(json.dumps(payloads))


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
        # Exact-name lookup. A bare name is only usable as a key when it is
        # unambiguous — `create_issue` exists on both github and gitlab, and
        # silently picking one would be worse than ranking them.
        self._by_key: dict[str, int] = {f"{t.server}/{t.name}": i for i, t in enumerate(tools)}
        counts: dict[str, int] = {}
        for tool in tools:
            counts[tool.name] = counts.get(tool.name, 0) + 1
        self._by_key.update(
            {t.name: i for i, t in enumerate(tools) if counts[t.name] == 1}
        )
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

    def rank(
        self, query: str, exclude: list[str] | None = None
    ) -> list[tuple[AggregatedTool, float]]:
        """Every candidate tool, best first, with scores. Exact names win outright.

        The seam `find` composes a response from — separated so that ranking
        quality and response shape can be measured independently. They are
        different failure modes and the benchmark scores them as such.
        """
        rejected = set(exclude or [])
        keep = [i for i, t in enumerate(self.tools) if f"{t.server}/{t.name}" not in rejected]
        if not keep or not query.strip():
            return []
        scores = self._vectors[keep] @ self._embed([query])[0]
        ranked = [(keep[i], float(scores[i])) for i in np.argsort(-scores)]
        exact = self._by_key.get(query.strip())
        if exact is not None and exact in keep:
            ranked = [(exact, 1.0)] + [r for r in ranked if r[0] != exact]
        return [(self.tools[i], s) for i, s in ranked]

    def find(
        self, query: str, k: int = 3, exclude: list[str] | None = None
    ) -> dict[str, Any]:
        """The best tool to call, the runners-up, and what else exists (D20).

        Always returns the best available match. A match below the confidence
        threshold is returned and flagged, not withheld — only an explicit
        `exclude` entry ("server/tool_name") keeps a tool out.
        """
        ranked = self.rank(query, exclude=exclude)
        if not ranked:
            return self._respond(None, [], [], self._empty_message(exclude))
        # The roster is drawn from the whole ranked list, not just the top k, so
        # it can surface the tool the ranker put 12th — the case that costs a
        # round trip.
        return self._respond(
            ranked[0], ranked[1 : max(1, k)], ranked[:ROSTER_SIZE], None, len(ranked)
        )

    def _respond(
        self,
        best: tuple[AggregatedTool, float] | None,
        alternatives: list[tuple[AggregatedTool, float]],
        roster: list[tuple[AggregatedTool, float]],
        message: str | None,
        searched: int = 0,
    ) -> dict[str, Any]:
        tool: dict[str, Any] | None = None
        if best is not None:
            found, score = best
            tool = {**tool_payload(found), "score": round(score, 4)}
            if score < self.confidence_threshold:
                tool["confidence"] = "low"

        alts = [{**tool_headline(t), "score": round(s, 4)} for t, s in alternatives]
        also: dict[str, list[str]] = {}
        for t, _ in roster:
            also.setdefault(t.server, []).append(t.name)

        result: dict[str, Any] = {
            "tool": tool,
            "alternatives": alts,
            "also_available": also,
            "servers": self._server_counts(),
        }

        # Count what this response actually costs the client — schema, headlines
        # and roster alike. Excluding the roster would flatter the receipt by
        # hiding a real (if small) cost, and the receipt is the product's claim.
        actual = estimate_tokens(
            json.dumps([tool] if tool else []) + json.dumps(alts) + json.dumps(also)
        )
        result["savings"] = {
            "tokens_if_naive": self.naive_tokens,
            "tokens_actual": actual,
            "saved_pct": saved_pct(self.naive_tokens, actual),
        }

        if message:
            result["message"] = message
        elif tool is not None:
            # Unconditional, not just on low confidence (D20 amended). The live
            # smoke re-run showed the roster being *delivered and ignored*: the
            # top match for "get repository details" was `get_tag` at 0.77 —
            # above the threshold, so no guidance was attached — while
            # `search_repositories` sat ninth in `also_available`. The client
            # rephrased three more times. Confidently-wrong is the failure mode
            # that costs round trips, and a score cannot detect it, so the
            # pointer has to be on every response. ~25 tokens against an ~800
            # token wasted search.
            result["message"] = (
                f"If `{tool['name']}` is not what you meant, do not reword this query: "
                "`also_available` lists every nearby tool name, and a name that is not "
                "there does not exist on these servers. Call find_tools again with an "
                "exact name from that list to get its schema."
            )
            if tool.get("confidence") == "low":
                result["message"] = (
                    f"Low confidence: the best match scored {tool['score']}, below "
                    f"{self.confidence_threshold}. " + result["message"]
                )
                log.info("low-confidence match (best %.4f of %d)", tool["score"], searched)
        return result

    def _server_counts(self) -> dict[str, int]:
        """Every server in the catalog. O(servers), so it survives any catalog size."""
        counts: dict[str, int] = {}
        for tool in self.tools:
            counts[tool.server] = counts.get(tool.server, 0) + 1
        return counts

    def _empty_message(self, exclude: list[str] | None) -> str:
        if not self.tools:
            return (
                "No tools available to match against — the aggregated catalog is "
                "empty. Check that toolsieve's config lists reachable servers."
            )
        if exclude and len(set(exclude)) >= len(self.tools):
            return "Every tool in the catalog was excluded by `exclude`."
        return "The query was blank, so there was nothing to match on."
