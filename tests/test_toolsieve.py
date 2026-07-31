"""One check per spec scenario. Run: uv run pytest -q

Downstream backends are real MCP servers (tests/fake_server.py) over real
transports — stdio and HTTP — not mocks. The whole point of toolsieve is that it
aggregates real ones, so a mock would be testing the wrong thing.
"""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from toolsieve.aggregator import Aggregator, DownstreamError  # noqa: E402
from toolsieve.config import (  # noqa: E402
    ConfigError,
    ServerConfig,
    expand_env,
    load_config,
    load_dotenv_file,
)
from toolsieve.router import Router, Savings, saved_pct  # noqa: E402

FAKE = str(Path(__file__).resolve().parent / "fake_server.py")


def write_config(path: Path, servers: dict) -> Path:
    path.write_text(json.dumps({"mcpServers": servers}))
    return path


def good(name: str = "docs") -> dict:
    return {"command": sys.executable, "args": [FAKE, name]}


BROKEN = {"command": sys.executable, "args": ["-c", "raise SystemExit(1)"]}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def http_url():
    """A real HTTP MCP server on localhost for the duration of the module.

    Real transport and a real MCP handshake, but no network egress — so the
    suite still passes on a plane and in CI without outbound access.
    """
    port = free_port()
    proc = subprocess.Popen([sys.executable, FAKE, "remote", "--http", str(port)])
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"fake http server exited early with {proc.returncode}")
        with socket.socket() as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.1)
    else:
        proc.kill()
        raise RuntimeError("fake http server never came up")
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


# --- config loading (task 2.1) -------------------------------------------------


def test_config_rejects_entry_with_no_transport(tmp_path):
    cfg = write_config(tmp_path / "c.json", {"docs": {"args": ["x"]}})
    with pytest.raises(ConfigError, match="'command'.*'url'"):
        load_config(cfg)


def test_config_rejects_missing_mcpservers_key(tmp_path):
    (tmp_path / "c.json").write_text('{"servers": {}}')
    with pytest.raises(ConfigError, match="mcpServers"):
        load_config(tmp_path / "c.json")


# --- http config loading (issue #1) --------------------------------------------


def test_url_entry_loads_as_http(tmp_path):
    cfg = write_config(tmp_path / "c.json", {"linear": {"url": "https://example.test/mcp"}})
    (server,) = load_config(cfg)
    assert server.is_http and server.url == "https://example.test/mcp"
    assert server.command is None


def test_config_rejects_both_transports(tmp_path):
    cfg = write_config(tmp_path / "c.json", {"x": {"command": "uv", "url": "https://a.test/mcp"}})
    with pytest.raises(ConfigError, match="pick one transport"):
        load_config(cfg)


def test_env_refs_expand_in_headers_and_url(tmp_path, monkeypatch):
    """Expansion happens at connect time, so the config keeps the raw ${VAR}."""
    monkeypatch.setenv("TS_TEST_TOKEN", "s3cret")
    monkeypatch.setenv("TS_TEST_KEY", "abc123")
    cfg = write_config(
        tmp_path / "c.json",
        {
            "linear": {
                "url": "https://example.test/mcp?apiKey=${TS_TEST_KEY}",
                "headers": {"Authorization": "Bearer ${TS_TEST_TOKEN}"},
            }
        },
    )
    (server,) = load_config(cfg)
    assert server.url.endswith("apiKey=${TS_TEST_KEY}")  # unexpanded on disk

    transport = Aggregator._transport(server)
    assert transport.headers == {"Authorization": "Bearer s3cret"}
    assert str(transport.url).endswith("apiKey=abc123")


def test_unset_env_ref_is_an_error_not_an_empty_token(monkeypatch):
    """Substituting "" would send an unauthenticated request and surface as a 401."""
    monkeypatch.delenv("TS_TEST_MISSING", raising=False)
    server = ServerConfig(
        name="linear",
        url="https://x.test/mcp",
        headers={"Authorization": "${TS_TEST_MISSING}"},
    )
    with pytest.raises(ConfigError, match="TS_TEST_MISSING"):
        Aggregator._transport(server)


def test_unset_env_ref_costs_one_server_not_the_catalog(tmp_path, monkeypatch, http_url):
    """One stale token must not blank servers that never referenced it (D13).

    Expanding at load time made this fail for the whole file — a config with ten
    servers and one bad token aggregated nothing at all.
    """
    monkeypatch.delenv("TS_TEST_MISSING", raising=False)
    cfg = write_config(
        tmp_path / "c.json",
        {
            "ok": good("ok"),
            "remote": {"url": http_url},
            "broken": {"url": http_url, "headers": {"Authorization": "${TS_TEST_MISSING}"}},
        },
    )

    async def run():
        agg = Aggregator(cfg)
        await agg.start()
        try:
            return sorted({t.server for t in agg.catalog.tools}), dict(agg.catalog.failed)
        finally:
            await agg.stop()

    servers, failed = asyncio.run(run())
    assert servers == ["ok", "remote"]  # unaffected servers still aggregate
    assert "TS_TEST_MISSING" in failed["broken"]  # and the cause is named


# --- .env loading (GH #6) --------------------------------------------------


def test_load_dotenv_file_parses_key_value_pairs(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "\n".join(
            [
                "# a comment",
                "",
                "TOKEN=s3cret",
                "QUOTED='wrapped'",
                "DOUBLE=\"also wrapped\"",
                "SPACED = padded ",
            ]
        )
    )
    assert load_dotenv_file(path) == {
        "TOKEN": "s3cret",
        "QUOTED": "wrapped",
        "DOUBLE": "also wrapped",
        "SPACED": "padded",
    }


def test_load_dotenv_file_missing_is_empty_not_an_error(tmp_path):
    assert load_dotenv_file(tmp_path / "nope.env") == {}


def test_expand_env_falls_back_to_overrides(monkeypatch):
    monkeypatch.delenv("TS_TEST_DOTENV_ONLY", raising=False)
    result = expand_env(
        "${TS_TEST_DOTENV_ONLY}",
        server="x",
        where="'url'",
        overrides={"TS_TEST_DOTENV_ONLY": "from-dotenv"},
    )
    assert result == "from-dotenv"


def test_expand_env_prefers_real_environ_over_overrides(monkeypatch):
    monkeypatch.setenv("TS_TEST_BOTH", "from-environ")
    result = expand_env(
        "${TS_TEST_BOTH}",
        server="x",
        where="'url'",
        overrides={"TS_TEST_BOTH": "from-dotenv"},
    )
    assert result == "from-environ"


def test_aggregator_reads_dotenv_next_to_config(tmp_path, monkeypatch, http_url):
    """A token in `.env` next to the config resolves without exporting anything."""
    monkeypatch.delenv("TS_TEST_AGG_TOKEN", raising=False)
    cfg = write_config(
        tmp_path / "c.json",
        {"remote": {"url": http_url, "headers": {"Authorization": "Bearer ${TS_TEST_AGG_TOKEN}"}}},
    )
    (tmp_path / ".env").write_text("TS_TEST_AGG_TOKEN=s3cret\n")

    async def run():
        agg = Aggregator(cfg)
        await agg.start()
        try:
            return await agg.call("remote", "echo_auth", {})
        finally:
            await agg.stop()

    assert "Bearer s3cret" in str(asyncio.run(run()))


def test_dotenv_edit_is_picked_up_without_restart(tmp_path, monkeypatch, http_url):
    """Editing `.env` after startup moves a server from failed to aggregated."""
    monkeypatch.delenv("TS_TEST_LIVE_TOKEN", raising=False)
    cfg = write_config(
        tmp_path / "c.json",
        {"remote": {"url": http_url, "headers": {"Authorization": "Bearer ${TS_TEST_LIVE_TOKEN}"}}},
    )

    async def run():
        agg = Aggregator(cfg)
        reloaded = asyncio.Event()
        agg.on_reload(lambda _catalog: reloaded.set() or asyncio.sleep(0))
        await agg.start()
        try:
            before_failed = dict(agg.catalog.failed)
            await asyncio.sleep(0.05)  # ensure a distinct mtime
            (tmp_path / ".env").write_text("TS_TEST_LIVE_TOKEN=s3cret\n")
            await asyncio.wait_for(reloaded.wait(), timeout=30)
            return before_failed, sorted({t.server for t in agg.catalog.tools})
        finally:
            await agg.stop()

    before_failed, after_servers = asyncio.run(run())
    assert "remote" in before_failed
    assert after_servers == ["remote"]


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


# --- http transport, against a real HTTP MCP server (issue #1) ------------------


def test_aggregates_stdio_and_http_side_by_side(tmp_path, http_url):
    """Both transports land in one catalog, indistinguishable downstream."""
    cfg = write_config(tmp_path / "c.json", {"local": good("local"), "remote": {"url": http_url}})

    async def run():
        agg = Aggregator(cfg)
        await agg.start()
        try:
            return {(t.server, t.name) for t in agg.catalog.tools}, dict(agg.catalog.failed)
        finally:
            await agg.stop()

    tools, failed = asyncio.run(run())
    assert not failed
    assert ("local", "get_weather") in tools
    assert ("remote", "get_weather") in tools
    assert ("remote", "echo_auth") in tools  # http-only tool proves the transport


def test_proxied_call_over_http_returns_the_real_result(tmp_path, http_url):
    cfg = write_config(tmp_path / "c.json", {"remote": {"url": http_url}})

    async def run():
        agg = Aggregator(cfg)
        await agg.start()
        try:
            return await agg.call("remote", "get_weather", {"city": "Boston"})
        finally:
            await agg.stop()

    assert "sunny in Boston" in str(asyncio.run(run()))


def test_auth_header_reaches_the_downstream_server(tmp_path, http_url, monkeypatch):
    """The ${VAR} token must actually arrive on the wire, not merely parse."""
    monkeypatch.setenv("TS_TEST_TOKEN", "s3cret")
    cfg = write_config(
        tmp_path / "c.json",
        {"remote": {"url": http_url, "headers": {"Authorization": "Bearer ${TS_TEST_TOKEN}"}}},
    )

    async def run():
        agg = Aggregator(cfg)
        await agg.start()
        try:
            return await agg.call("remote", "echo_auth", {})
        finally:
            await agg.stop()

    assert "Bearer s3cret" in str(asyncio.run(run()))


def test_unreachable_http_server_is_isolated(tmp_path):
    """A dead remote costs its own tools, not the catalog — and retries first."""
    dead = f"http://127.0.0.1:{free_port()}/mcp"  # nothing is listening there
    cfg = write_config(tmp_path / "c.json", {"ok": good("ok"), "remote": {"url": dead}})

    async def run():
        agg = Aggregator(cfg)
        await agg.start()
        try:
            return sorted({t.server for t in agg.catalog.tools}), dict(agg.catalog.failed)
        finally:
            await agg.stop()

    servers, failed = asyncio.run(run())
    assert servers == ["ok"]
    assert "remote" in failed


def test_http_call_survives_a_dropped_session(tmp_path, http_url):
    """A killed session must reconnect on the next call, not fail it.

    Closing the underlying session mid-flight is what an idle timeout, a proxy,
    or a redeploy does to a long-lived HTTP connection — routine, and invisible
    until the next call.
    """
    cfg = write_config(tmp_path / "c.json", {"remote": {"url": http_url}})

    async def run():
        agg = Aggregator(cfg)
        await agg.start()
        try:
            first = await agg.call("remote", "get_weather", {"city": "Boston"})
            await agg.catalog.clients["remote"]._disconnect()  # drop it under us
            return first, await agg.call("remote", "get_weather", {"city": "Denver"})
        finally:
            await agg.stop()

    first, after_drop = asyncio.run(run())
    assert "sunny in Boston" in str(first)
    assert "sunny in Denver" in str(after_drop)  # reconnected transparently


def test_http_rejection_is_not_retried(tmp_path, http_url):
    """The server answered and said no — reconnecting cannot change that answer.

    Retrying here would run a side-effecting tool a second time for every call
    made with bad arguments.
    """
    cfg = write_config(tmp_path / "c.json", {"remote": {"url": http_url}})

    async def run():
        agg = Aggregator(cfg)
        await agg.start()
        try:
            with pytest.raises(DownstreamError) as caught:
                await agg.call("remote", "get_weather", {"wrong_arg": "x"})
            return str(caught.value)
        finally:
            await agg.stop()

    message = asyncio.run(run())
    assert "remote" in message
    assert "after reconnecting" not in message  # single attempt, not two


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
    top = result["tool"]
    assert top is not None, result.get("message")
    assert top["name"] == "get_weather"
    assert top["server"] == "docs"
    assert "city" in json.dumps(top["input_schema"])  # schema returned, so it's callable


def test_only_the_top_match_carries_a_schema(router):
    """The token fix (D20): schemas are ~85% of a response, so k-1 go unshipped.

    Returning a schema per match is what made v0.2 cost ~1.7k tokens a query.
    Runners-up are name + description, which is enough to *choose* one; the
    schema is a separate, exact-name lookup away.
    """
    result = router.find("what is the temperature outside today", k=3)
    assert result["tool"]["input_schema"], "the tool you are told to call must be callable"
    assert result["alternatives"], "this catalog has a runner-up to report"
    for alt in result["alternatives"]:
        assert "input_schema" not in alt
        assert alt["name"] and alt["description"]


def test_roster_names_every_tool_so_absence_is_provable(router):
    """Negative knowledge (D20) — the fix for the smoke test's reformulation loop.

    A client that cannot see what exists cannot tell "ranked low" from "does not
    exist", so it rewords the query and pays for another search. The roster is
    names only, so this costs a fraction of one schema.
    """
    result = router.find("something completely unrelated to this catalog", k=1)
    listed = {name for names in result["also_available"].values() for name in names}
    assert listed == {"get_weather", "search_docs"}
    assert result["servers"] == {"docs": 2}


def test_exact_name_query_returns_that_tools_schema(router):
    """A name spotted in `also_available` must resolve in one deterministic hop.

    Ranking is probabilistic; this path is not. Without it, acting on the roster
    would mean hoping the embedder agrees with a choice already made.
    """
    for key in ("search_docs", "docs/search_docs"):
        tool = router.find(key, k=1)["tool"]
        assert (tool["server"], tool["name"]) == ("docs", "search_docs"), key
        assert tool["score"] == 1.0
        assert "confidence" not in tool  # an exact hit is never "low"


def test_weak_match_is_flagged_not_withheld(router):
    """Scenario: Query matches only weakly (D11 amended)."""
    result = router.find("refinance a thirty year fixed rate mortgage", k=1)
    assert result["tool"], "a usable tool must never be withheld for low confidence"
    assert result["tool"]["confidence"] == "low"
    assert result["tool"]["score"] < router.confidence_threshold
    assert "Low confidence" in result["message"]
    # The nudge that ends the loop: check the roster before rewording.
    assert "also_available" in result["message"]


def test_strong_match_is_not_flagged(router):
    """The confidence tag must actually discriminate, not tag everything."""
    top = router.find("search the documentation", k=1)["tool"]
    assert "confidence" not in top
    assert top["score"] >= router.confidence_threshold


def test_excluded_tool_is_withheld(router):
    """Scenario: Client rejects a returned tool."""
    query = "what is the temperature outside today"
    first = router.find(query, k=1)["tool"]
    assert first["name"] == "get_weather"

    result = router.find(query, k=1, exclude=[f"{first['server']}/{first['name']}"])
    assert result["tool"]["name"] == "search_docs"  # next-best real tool
    listed = {name for names in result["also_available"].values() for name in names}
    assert "get_weather" not in listed  # excluded from the roster too, not just the match


def test_empty_catalog_returns_no_tool():
    """The only remaining empty-result path: nothing to match against."""
    result = Router([]).find("anything at all", k=3)
    assert result["tool"] is None
    assert result["alternatives"] == []
    assert "empty" in result["message"]


def test_per_call_savings_metadata(router):
    """Scenario: Per-call savings metadata."""
    savings = router.find("what is the weather", k=1)["savings"]
    assert savings["tokens_if_naive"] > savings["tokens_actual"] > 0
    assert savings["saved_pct"] > 0


def test_savings_receipt_counts_the_roster_too(router):
    """The receipt must not flatter itself by hiding the roster's cost."""
    result = router.find("what is the weather", k=3)
    body = json.dumps(
        [result["tool"]]
    ) + json.dumps(result["alternatives"]) + json.dumps(result["also_available"])
    # chars/4, the same estimator the receipt uses — so this is exact, not fuzzy.
    assert result["savings"]["tokens_actual"] == len(body) // 4


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
            top = found["tool"]
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

    assert found["tool"]["name"] == "get_weather"
    assert called["ok"] is True
    assert called["result"] == "sunny in Boston"  # structured payload survived
    assert report["find_tools_calls"] == 1


def test_failed_server_is_visible_to_the_client(tmp_path):
    """stderr logs are invisible to an MCP client — a dead backend must not be.

    With remote servers this is the common case: expired token, moved endpoint,
    network. The client has to be told why its tools disappeared.
    """
    import os

    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    root = Path(__file__).resolve().parents[1]
    cfg = write_config(tmp_path / "c.json", {"ok": good("ok"), "dead": BROKEN})

    async def run():
        transport = StdioTransport(
            command=sys.executable,
            args=["-m", "toolsieve"],
            cwd=str(root),
            env={**os.environ, "TOOLSIEVE_CONFIG": str(cfg)},
        )
        async with Client(transport) as client:
            return (await client.call_tool("get_savings_report", {})).data

    report = asyncio.run(run())
    assert "dead" in report["unavailable_servers"]
    assert report["tools_aggregated"] == 2  # the healthy server is still usable


def test_two_instances_are_distinguishable(tmp_path):
    """Scenario: two toolsieve instances in one session (D21).

    Found in the pre-release smoke test: a globally-installed toolsieve plugin
    and a project-scoped toolsieve published identical tool names *and* identical
    descriptions. The client had nothing to choose on, picked one, and reported
    a server as unconfigured when it was configured — in the other instance.
    Both the advertised descriptions and the responses must name their catalog.
    """
    import os

    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    root = Path(__file__).resolve().parents[1]
    configs = {
        "weather-only": write_config(tmp_path / "a.json", {"weather": good("weather")}),
        "docs-only": write_config(tmp_path / "b.json", {"docs": good("docs")}),
    }

    async def probe(cfg: Path):
        transport = StdioTransport(
            command=sys.executable,
            args=["-m", "toolsieve"],
            cwd=str(root),
            env={**os.environ, "TOOLSIEVE_CONFIG": str(cfg)},
        )
        async with Client(transport) as client:
            described = {t.name: t.description for t in await client.list_tools()}
            found = (await client.call_tool("find_tools", {"query": "weather"})).data
            return described, found

    async def run():
        return {name: await probe(cfg) for name, cfg in configs.items()}

    probed = asyncio.run(run())
    (desc_a, found_a) = probed["weather-only"]
    (desc_b, found_b) = probed["docs-only"]

    # What the client picks between, before it calls anything.
    assert desc_a["find_tools"] != desc_b["find_tools"]
    for name in ("find_tools", "call_tool", "get_savings_report"):
        assert str(configs["weather-only"]) in desc_a[name]
        assert "weather" in desc_a[name]
        assert str(configs["docs-only"]) in desc_b[name]

    # And after: a response says which catalog answered it.
    assert found_a["catalog"] == str(configs["weather-only"])
    assert found_b["catalog"] == str(configs["docs-only"])
