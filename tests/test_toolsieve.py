"""One check per spec scenario. Run: uv run pytest -q

Downstream backends are real stdio MCP servers (tests/fake_server.py), not mocks —
the whole point of toolsieve is that it aggregates real ones.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from toolsieve.aggregator import Aggregator, DownstreamError  # noqa: E402
from toolsieve.config import ConfigError, load_config  # noqa: E402
from toolsieve.router import Router, Savings, saved_pct  # noqa: E402

FAKE = str(Path(__file__).resolve().parent / "fake_server.py")


def write_config(path: Path, servers: dict) -> Path:
    path.write_text(json.dumps({"mcpServers": servers}))
    return path


def good(name: str = "docs") -> dict:
    return {"command": sys.executable, "args": [FAKE, name]}


BROKEN = {"command": sys.executable, "args": ["-c", "raise SystemExit(1)"]}


# --- config loading (task 2.1) -------------------------------------------------


def test_config_rejects_entry_without_command(tmp_path):
    cfg = write_config(tmp_path / "c.json", {"docs": {"args": ["x"]}})
    with pytest.raises(ConfigError, match="missing 'command'"):
        load_config(cfg)


def test_config_rejects_missing_mcpservers_key(tmp_path):
    (tmp_path / "c.json").write_text('{"servers": {}}')
    with pytest.raises(ConfigError, match="mcpServers"):
        load_config(tmp_path / "c.json")


# --- aggregation (mcp-aggregation spec) ----------------------------------------


def test_aggregates_every_reachable_server(tmp_path):
    """Scenario: Aggregating a valid multi-server config."""
    cfg = write_config(tmp_path / "c.json", {"docs": good("docs"), "weather": good("weather")})

    async def run():
        agg = Aggregator(cfg)
        await agg.start()
        try:
            return {(t.server, t.name) for t in agg.catalog.tools}
        finally:
            await agg.stop()

    tools = asyncio.run(run())
    assert ("docs", "get_weather") in tools
    assert ("weather", "search_docs") in tools
    assert len(tools) == 4  # 2 tools x 2 servers


def test_unreachable_server_is_isolated(tmp_path):
    """Scenario: One of several configured servers is unreachable."""
    cfg = write_config(tmp_path / "c.json", {"ok": good("ok"), "dead": BROKEN})

    async def run():
        agg = Aggregator(cfg)
        await agg.start()
        try:
            return sorted({t.server for t in agg.catalog.tools}), dict(agg.catalog.failed)
        finally:
            await agg.stop()

    servers, failed = asyncio.run(run())
    assert servers == ["ok"]  # the reachable server still aggregated
    assert "dead" in failed  # and the broken one is recorded, not fatal


def test_config_change_is_picked_up_without_restart(tmp_path):
    """Scenario: Adding a new downstream server while running."""
    cfg = write_config(tmp_path / "c.json", {"docs": good("docs")})

    async def run():
        agg = Aggregator(cfg)
        reloaded = asyncio.Event()
        agg.on_reload(lambda _catalog: reloaded.set() or asyncio.sleep(0))
        await agg.start()
        try:
            before = {t.server for t in agg.catalog.tools}
            await asyncio.sleep(0.05)  # ensure a distinct mtime
            write_config(cfg, {"docs": good("docs"), "weather": good("weather")})
            await asyncio.wait_for(reloaded.wait(), timeout=30)
            return before, {t.server for t in agg.catalog.tools}
        finally:
            await agg.stop()

    before, after = asyncio.run(run())
    assert before == {"docs"}
    assert after == {"docs", "weather"}


def test_missing_config_starts_empty_and_recovers(tmp_path):
    """D19: a fresh plugin install has no config — that must not be fatal."""
    cfg = tmp_path / "not-created-yet.json"

    async def run():
        agg = Aggregator(cfg)
        reloaded = asyncio.Event()
        agg.on_reload(lambda _c: reloaded.set() or asyncio.sleep(0))
        await agg.start()  # must not raise
        try:
            started_empty = (agg.catalog.tools == [], agg.config_error)
            write_config(cfg, {"docs": good("docs")})  # user creates it afterwards
            await asyncio.wait_for(reloaded.wait(), timeout=30)
            return started_empty, len(agg.catalog.tools), agg.config_error
        finally:
            await agg.stop()

    (was_empty, error), tool_count, error_after = asyncio.run(run())
    assert was_empty and error is not None and "not found" in error
    assert tool_count == 2  # picked up with no restart
    assert error_after is None  # and the error cleared


# --- proxying (smart-tool-router spec) -----------------------------------------


def test_proxied_call_returns_the_real_result(tmp_path):
    """Scenario: Successful proxied call."""
    cfg = write_config(tmp_path / "c.json", {"docs": good("docs")})

    async def run():
        agg = Aggregator(cfg)
        await agg.start()
        try:
            return await agg.call("docs", "get_weather", {"city": "Boston"})
        finally:
            await agg.stop()

    assert "sunny in Boston" in str(asyncio.run(run()))


def test_call_against_failed_server_names_it(tmp_path):
    """Scenario: Target server has failed."""
    cfg = write_config(tmp_path / "c.json", {"ok": good("ok"), "dead": BROKEN})

    async def run():
        agg = Aggregator(cfg)
        await agg.start()
        try:
            with pytest.raises(DownstreamError, match="dead"):
                await agg.call("dead", "get_weather", {"city": "X"})
            # the healthy server is unaffected
            return await agg.call("ok", "get_weather", {"city": "X"})
        finally:
            await agg.stop()

    assert "sunny in X" in str(asyncio.run(run()))


# --- routing + savings (smart-tool-router spec) --------------------------------


@pytest.fixture(scope="module")
def router(tmp_path_factory):
    cfg = write_config(tmp_path_factory.mktemp("cfg") / "c.json", {"docs": good("docs")})

    async def run():
        agg = Aggregator(cfg)
        await agg.start()
        try:
            return list(agg.catalog.tools)
        finally:
            await agg.stop()

    return Router(asyncio.run(run()))


def test_query_matches_the_right_tool(router):
    """Scenario: Query matches an aggregated tool."""
    result = router.find("what is the temperature outside today", k=3)
    assert result["matches"], result.get("message")
    top = result["matches"][0]
    assert top["name"] == "get_weather"
    assert top["server"] == "docs"
    assert "city" in json.dumps(top["input_schema"])  # schema returned, so it's callable


def test_weak_match_is_flagged_not_withheld(router):
    """Scenario: Query matches only weakly (D11 amended)."""
    result = router.find("refinance a thirty year fixed rate mortgage", k=1)
    assert result["matches"], "a usable tool must never be withheld for low confidence"
    assert result["matches"][0]["confidence"] == "low"
    assert result["matches"][0]["score"] < router.confidence_threshold
    assert "Low confidence" in result["message"]


def test_strong_match_is_not_flagged(router):
    """The confidence tag must actually discriminate, not tag everything."""
    top = router.find("search the documentation", k=1)["matches"][0]
    assert "confidence" not in top
    assert top["score"] >= router.confidence_threshold


def test_excluded_tool_is_withheld(router):
    """Scenario: Client rejects a returned tool."""
    query = "what is the temperature outside today"
    first = router.find(query, k=1)["matches"][0]
    assert first["name"] == "get_weather"

    result = router.find(query, k=1, exclude=[f"{first['server']}/{first['name']}"])
    names = [m["name"] for m in result["matches"]]
    assert "get_weather" not in names
    assert names == ["search_docs"]  # falls through to the next-best real tool


def test_empty_catalog_returns_empty_matches():
    """The only remaining empty-result path: nothing to match against."""
    result = Router([]).find("anything at all", k=3)
    assert result["matches"] == []
    assert "empty" in result["message"]


def test_per_call_savings_metadata(router):
    """Scenario: Per-call savings metadata."""
    savings = router.find("what is the weather", k=1)["savings"]
    assert savings["tokens_if_naive"] > savings["tokens_actual"] > 0
    assert savings["saved_pct"] > 0


def test_session_savings_report_accumulates():
    """Scenario: Session-total savings report."""
    s = Savings()
    s.record(1000, 250)
    s.record(1000, 250)
    report = s.report()
    assert report["find_tools_calls"] == 2
    assert report["tokens_if_naive"] == 2000
    assert report["tokens_saved"] == 1500
    assert report["saved_pct"] == 75.0


def test_saved_pct_handles_empty_catalog():
    assert saved_pct(0, 0) == 0.0


# --- the whole server, through a real MCP client --------------------------------


def test_server_end_to_end(tmp_path):
    """Exposes 3 tools regardless of catalog size, and a proxied call round-trips.

    Goes through a real client because the aggregator-level tests cannot see MCP
    serialization — a bare `Any` return silently dropped the structured payload
    while every lower-level test still passed (D18).
    """
    import os

    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    root = Path(__file__).resolve().parents[1]
    cfg = write_config(tmp_path / "c.json", {"docs": good("docs"), "weather": good("weather")})

    async def run():
        transport = StdioTransport(
            command=sys.executable,
            args=["-m", "toolsieve"],
            cwd=str(root),
            env={**os.environ, "TOOLSIEVE_CONFIG": str(cfg)},
        )
        async with Client(transport) as client:
            exposed = sorted(t.name for t in await client.list_tools())
            found = (await client.call_tool("find_tools", {"query": "the weather today"})).data
            top = found["matches"][0]
            called = (
                await client.call_tool(
                    "call_tool",
                    {"server": top["server"], "tool_name": top["name"], "args": {"city": "Boston"}},
                )
            ).data
            report = (await client.call_tool("get_savings_report", {})).data
            return exposed, found, called, report

    exposed, found, called, report = asyncio.run(run())

    # 4 aggregated tools, still exactly 3 exposed — the entire point of toolsieve
    assert exposed == ["call_tool", "find_tools", "get_savings_report"]
    assert report["tools_aggregated"] == 4

    assert found["matches"][0]["name"] == "get_weather"
    assert called["ok"] is True
    assert called["result"] == "sunny in Boston"  # structured payload survived
    assert report["find_tools_calls"] == 1
