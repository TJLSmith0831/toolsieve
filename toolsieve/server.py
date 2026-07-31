"""The outward-facing MCP server (D5, D10).

Exposes exactly three tools — `find_tools`, `call_tool`, `get_savings_report` —
regardless of how many tools the aggregated downstream servers publish. That
gap is the whole product.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_context
from fastmcp.tools import FunctionTool

from .aggregator import Aggregator, Catalog, DownstreamError
from .config import DEFAULT_CONFIG_PATH
from .router import DEFAULT_CONFIDENCE_THRESHOLD, Router, Savings

log = logging.getLogger("toolsieve")

_state: dict[str, Any] = {"aggregator": None, "router": None, "savings": Savings()}


@asynccontextmanager
async def _lifespan(_server: FastMCP):
    """Aggregate downstream servers and build the index before serving."""
    await _startup()
    try:
        yield
    finally:
        aggregator: Aggregator | None = _state["aggregator"]
        if aggregator is not None:
            await aggregator.stop()


mcp: FastMCP = FastMCP(
    name="toolsieve",
    instructions=(
        "toolsieve routes across all your aggregated MCP servers. Call find_tools "
        "with a natural-language description of what you want to do, then call "
        "call_tool with the server, tool_name, and args from the match you picked."
    ),
    lifespan=_lifespan,
)


def _router() -> Router:
    router = _state["router"]
    if router is None:
        raise RuntimeError("toolsieve is still starting up; no catalog indexed yet")
    return router


@mcp.tool
async def find_tools(
    query: str, k: int = 3, exclude: list[str] | None = None
) -> dict[str, Any]:
    """Find the tool to call for a task, across every aggregated MCP server.

    Returns:
      - `tool`: the best match, with its input schema — call it via call_tool.
      - `alternatives`: runners-up, name and description only (no schema).
      - `also_available`: every nearby tool *name*, grouped by server. Read this
        before reformulating: if the tool you had in mind is not listed, it does
        not exist, and rewording the query will not conjure it.
      - `servers`: every aggregated server and its tool count.

    To get the schema for a tool you saw in `also_available` or `alternatives`,
    call again with its exact name as the query — exact names are matched
    directly, not ranked.

    A weak match is returned with `confidence: "low"` rather than withheld. If a
    returned tool is wrong, call again with exclude=["<server>/<tool_name>"].
    """
    router = _router()
    result = router.find(query, k=k, exclude=exclude)
    if result.get("tool"):
        result["tool"]["callable_as"] = await _promote(result["tool"])
    savings: Savings = _state["savings"]
    savings.record(result["savings"]["tokens_if_naive"], result["savings"]["tokens_actual"])
    _attach_config_error(result)
    return result


_PROMOTED: set[str] = set()


async def _promote(match: dict[str, Any]) -> str:
    """Register a found tool so the client can call it without going through us (D23).

    The measured gap against a client's own tool search is not the extra hop —
    that costs ~33 tokens a run. It is that their search returns a *pointer* the
    API expands into cached tool definitions (~99 tokens a call) while find_tools
    returns the schema inline in the conversation (~808). MCP offers the same
    mechanism: a server may change its own tool list, and clients that honour
    `notifications/tools/list_changed` then hold the schema in their cached
    definitions instead of re-reading it out of the transcript.

    Namespaced because `create_issue` exists on more than one server.
    """
    exposed = f"{match['server']}__{match['name']}"
    if exposed in _PROMOTED:
        return exposed

    server, tool_name = match["server"], match["name"]

    async def run(**kwargs: Any) -> Any:
        # No `ok`/`result` envelope: a promoted tool stands in for the real one,
        # so it returns what the real one returns and lets a failure surface as
        # an MCP tool error. `call_tool`'s envelope exists because a client that
        # reached a tool through a generic proxy cannot otherwise tell a
        # downstream error from a downstream result; here it can.
        aggregator: Aggregator = _state["aggregator"]
        return _unwrap(await aggregator.call(server, tool_name, kwargs))

    # Built directly rather than via Tool.from_function, which infers the schema
    # from the signature and rejects **kwargs. The schema here is the downstream
    # server's own — passing it through unaltered is the point.
    mcp.add_tool(
        FunctionTool(
            name=exposed,
            description=match["description"],
            parameters=match["input_schema"],
            output_schema=match.get("output_schema"),
            fn=run,
        )
    )
    _PROMOTED.add(exposed)

    # Adding the tool is not enough: a client re-reads its tool list only when
    # told to, so without this the promotion is invisible to everyone who is not
    # already polling — which is everyone. Best-effort: a client that never
    # re-lists still has `tool` in the response and `call_tool` to reach it, so
    # a failure here degrades to the old path rather than losing the call.
    try:
        await get_context().session.send_tool_list_changed()
    except Exception as exc:  # no active session, or a client that cannot be told
        log.info("could not announce %s: %s", exposed, exc)

    log.info("promoted %s to a directly callable tool", exposed)
    return exposed


def _attach_config_error(result: dict[str, Any]) -> None:
    """Surface config and backend problems through the tools, not a crash (D19).

    A server that failed to connect is otherwise only visible in stderr logs,
    which no MCP client shows you. That was survivable when every backend was a
    local stdio process; a remote one can be down, moved, or holding an expired
    token, so the client needs to be told why its tools vanished.

    Also stamps which catalog answered (D21). Two toolsieve instances in one
    session — the global plugin and a project-scoped one — are indistinguishable
    from the client's side, so a mis-routed call used to look like a correct
    answer from an empty catalog. Naming the config in the response makes the
    mistake self-evident instead of silent.
    """
    aggregator: Aggregator | None = _state["aggregator"]
    if aggregator is None:
        return
    result["catalog"] = str(_config_path())
    if aggregator.config_error is not None:
        result["config_error"] = (
            f"toolsieve has no usable config, so nothing is aggregated: "
            f"{aggregator.config_error}. Create or fix that file — it is picked up "
            "automatically, with no restart."
        )
    if aggregator.catalog.failed:
        result["unavailable_servers"] = dict(aggregator.catalog.failed)


@mcp.tool
async def call_tool(
    server: str, tool_name: str, args: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Run a tool on its downstream server. Use values returned by find_tools.

    Always returns an envelope: `ok` plus either `result` or `error`. A downstream
    result can be any shape, so it is nested under `result` rather than returned
    bare — a bare `Any` return gives MCP no output schema, which drops the
    structured payload and leaves clients reading raw text content.
    """
    aggregator: Aggregator = _state["aggregator"]
    if aggregator is None:
        raise RuntimeError("toolsieve is still starting up; no downstream servers connected")
    try:
        result = await aggregator.call(server, tool_name, args or {})
    except DownstreamError as exc:
        # D13: name the failing server; other servers stay usable.
        return {"ok": False, "server": server, "tool_name": tool_name, "error": str(exc)}
    return {"ok": True, "server": server, "tool_name": tool_name, "result": _unwrap(result)}


def _unwrap(result: Any) -> Any:
    """Pull the usable value out of a downstream CallToolResult."""
    data = getattr(result, "data", None)
    if data is not None:
        return data
    content = getattr(result, "content", None) or []
    texts = [c.text for c in content if getattr(c, "text", None) is not None]
    if texts:
        return texts[0] if len(texts) == 1 else texts
    return data if content == [] else str(content)


@mcp.tool
async def get_savings_report() -> dict[str, Any]:
    """Cumulative token savings from routing instead of loading every tool."""
    report: dict[str, Any] = _state["savings"].report()
    router = _state["router"]
    if router is not None:
        report["tools_aggregated"] = len(router.tools)
        report["tools_exposed"] = 3
    _attach_config_error(report)
    return report


async def _reindex(catalog: Catalog) -> None:
    """Rebuild the embedding index after the catalog is swapped (D8)."""
    _state["router"] = Router(catalog.tools, confidence_threshold=_threshold())
    await _label_instance()
    log.info("reindexed after config reload: %d tools", len(catalog.tools))


# Descriptions are rewritten on every reload, so the originals are kept here
# rather than re-read from the (already rewritten) live tool.
_BASE_DESCRIPTIONS: dict[str, str] = {}


async def _label_instance() -> None:
    """Tell this toolsieve apart from any other one in the same session (D21).

    A globally-installed toolsieve plugin and a project-scoped toolsieve publish
    the same three tool names with the same three descriptions. A client has
    nothing to choose between them on, so it picks one — and if it picks the one
    whose catalog lacks the server the task needs, the honest answer ("that
    server is not configured") is indistinguishable from the truth ("it is, in
    the other instance"). Stamping the config path and server list into the
    descriptions gives the client the one fact that separates them.
    """
    aggregator: Aggregator | None = _state["aggregator"]
    servers = sorted({t.server for t in aggregator.catalog.tools}) if aggregator else []
    label = (
        f"[catalog: {_config_path()} — {len(servers)} server(s): "
        f"{', '.join(servers) or 'none reachable'}]\n"
        "If more than one toolsieve is connected, each serves a different catalog; "
        "pick the one whose servers you need."
    )
    mcp.instructions = (
        "toolsieve routes across all your aggregated MCP servers. Call find_tools "
        "with a natural-language description of what you want to do, then call "
        "call_tool with the server, tool_name, and args from the match you picked. "
        "find_tools also returns `also_available` — the names of every nearby tool. "
        "Read it before rewording a query: a tool that is not listed does not exist.\n"
        f"{label}"
    )
    for name in ("find_tools", "call_tool", "get_savings_report"):
        tool = await mcp.get_tool(name)
        base = _BASE_DESCRIPTIONS.setdefault(name, tool.description or "")
        tool.description = f"{base}\n\n{label}"


def _threshold() -> float:
    return float(os.environ.get("TOOLSIEVE_CONFIDENCE_THRESHOLD", DEFAULT_CONFIDENCE_THRESHOLD))


def _config_path() -> Path:
    return Path(os.environ.get("TOOLSIEVE_CONFIG", DEFAULT_CONFIG_PATH))


async def _startup() -> None:
    aggregator = Aggregator(_config_path())
    aggregator.on_reload(_reindex)
    await aggregator.start()
    _state["aggregator"] = aggregator
    _state["router"] = Router(aggregator.catalog.tools, confidence_threshold=_threshold())
    await _label_instance()


def main() -> None:
    # Logs go to stderr — stdout is the MCP stdio channel and must stay clean.
    level = os.environ.get("TOOLSIEVE_LOG_LEVEL", "WARNING").upper()
    logging.basicConfig(level=level, format="[toolsieve] %(levelname)s %(message)s")
    mcp.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
