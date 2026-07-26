"""How a routing method is scored: did it find the tool, and at what token cost.

Two numbers, deliberately reported together (D1): token savings alone is not a
win if the router saved them by returning the wrong tool.

`saved_pct` is imported from the shipped router rather than reimplemented — the
benchmark should report the same percentage maths the live receipt does. What it
does *not* reuse is the chars/4 estimator: the marketing-facing absolute count
comes from a real tokenizer (D4).
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Iterable

import tiktoken

# Same encoding GPT-4/4o-class clients use, which is whose context window the
# savings claim is actually about.
ENCODING = "cl100k_base"


@lru_cache(maxsize=1)
def _encoder() -> tiktoken.Encoding:
    """ponytail: one process-wide encoder — construction reads a BPE file."""
    return tiktoken.get_encoding(ENCODING)


def real_tokens(payloads: Iterable[dict[str, Any]]) -> int:
    """Token count of what a client would actually load into its context.

    Mirrors `toolsieve.router.payload_tokens` in shape (JSON of the payload
    list) so naive and routed sides are counted identically — only the counter
    differs.
    """
    return len(_encoder().encode(json.dumps(list(payloads))))


def recall_at_k(
    matches: list[dict[str, Any]], expected: tuple[str, str], k: int
) -> float:
    """1.0 if the ground-truth (server, tool) is in the top *k* matches, else 0.0.

    Server-qualified: `create_issue` on gitlab is not a hit for a github query.
    Binary because every benchmark query carries exactly one expected tool, so
    the mean of this over a query set is the method's recall@k.
    """
    top = matches[:k]
    return 1.0 if any((m["server"], m["name"]) == tuple(expected) for m in top) else 0.0
