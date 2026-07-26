"""Loading the benchmark dataset.

The catalog is loaded straight into `AggregatedTool` — the same dataclass the
live aggregator produces — so the benchmark feeds toolsieve's real `Router` the
exact shape it sees in production, rather than a benchmark-only stand-in.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, NamedTuple

from toolsieve.aggregator import AggregatedTool

HERE = Path(__file__).resolve().parent
CATALOG_PATH = HERE / "catalog.json"
QUERIES_PATH = HERE / "queries.jsonl"


class Query(NamedTuple):
    text: str
    expected: tuple[str, str]
    difficulty: str


def load_catalog(path: Path = CATALOG_PATH) -> list[AggregatedTool]:
    raw: dict[str, Any] = json.loads(path.read_text())
    return [
        AggregatedTool(
            server=t["server"],
            name=t["name"],
            description=t["description"],
            input_schema=t.get("input_schema", {}),
        )
        for t in raw["tools"]
    ]


def load_queries(path: Path = QUERIES_PATH) -> list[Query]:
    """Read queries.jsonl, skipping the leading `_note` documentation record."""
    queries = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if "query" not in row:  # the dataset's own header note
            continue
        queries.append(
            Query(row["query"], (row["expected"][0], row["expected"][1]), row["difficulty"])
        )
    return queries
