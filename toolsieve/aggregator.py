"""Downstream server aggregation (D8, D13).

Connects out to every configured MCP server — stdio or HTTP/SSE (issue #1) —
collects its real published tool list, and holds the connections open so calls
can be proxied through. Failures are isolated per-server: one unreachable
backend costs you that backend's tools, not the whole catalog.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import (
    ClientTransport,
    SSETransport,
    StdioTransport,
    StreamableHttpTransport,
)

from .config import ServerConfig, load_config

log = logging.getLogger("toolsieve")

RELOAD_POLL_SECONDS = 1.0
HTTP_RETRY_SECONDS = 0.5


@dataclass(frozen=True)
class AggregatedTool:
    """One real tool published by one downstream server."""

    server: str
    name: str
    description: str
    input_schema: dict[str, Any]

    @property
    def match_text(self) -> str:
        """What the router embeds — the tool's own name + description (D9)."""
        return f"{self.name}: {self.description}".strip()


class DownstreamError(RuntimeError):
    """A named downstream server failed; other servers are unaffected (D13)."""


@dataclass
class Catalog:
    """An immutable snapshot of everything reachable at aggregation time."""

    tools: list[AggregatedTool]
    clients: dict[str, Client]
    failed: dict[str, str]

    def find(self, server: str, tool_name: str) -> AggregatedTool | None:
        return next(
            (t for t in self.tools if t.server == server and t.name == tool_name), None
        )


class Aggregator:
    """Owns the live catalog and the connections behind it.

    The catalog is swapped atomically: a reload builds an entirely new one and
    only then rebinds `self.catalog`, so an in-flight call never observes a
    half-built catalog (design.md, Risks).
    """

    def __init__(self, config_path: str | Path) -> None:
        self.config_path = Path(config_path)
        self.catalog = Catalog(tools=[], clients={}, failed={})
        self._stack: AsyncExitStack | None = None
        self._on_reload: list[Any] = []
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self.config_error: str | None = None

    def on_reload(self, callback: Any) -> None:
        """Register a coroutine fn called with the new Catalog after each swap."""
        self._on_reload.append(callback)

    async def start(self) -> None:
        """Aggregate once, then keep watching the config in the background.

        Never raises on a bad config (D19) — toolsieve starts with an empty
        catalog and keeps watching, so creating or fixing the file hot-loads it.

        Every connection is opened and closed inside the single `_run` task:
        anyio-backed stdio sessions bind their cancel scope to the entering
        task, so entering here and closing from the watcher would blow up.
        """
        self._task = asyncio.create_task(self._run())
        await self._ready.wait()

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.catalog = Catalog(tools=[], clients={}, failed={})

    async def _run(self) -> None:
        try:
            try:
                await self._aggregate()
            except Exception as exc:  # noqa: BLE001 — a bad config is not fatal (D19)
                self.config_error = str(exc)
                log.warning("starting with an empty catalog: %s", exc)
            finally:
                self._ready.set()
            await self._watch_config()
        finally:
            await self._close(self._stack)
            self._stack = None

    async def _aggregate(self) -> None:
        """Connect to every configured server and build a fresh catalog."""
        servers = load_config(self.config_path)
        stack = AsyncExitStack()
        tools: list[AggregatedTool] = []
        clients: dict[str, Client] = {}
        failed: dict[str, str] = {}

        for server in servers:
            try:
                client, published = await self._connect_and_list(stack, server)
            except Exception as exc:  # noqa: BLE001 — isolation is the point (D13)
                log.warning("server %r unavailable, skipping its tools: %s", server.name, exc)
                failed[server.name] = str(exc)
                continue

            clients[server.name] = client
            for tool in published:
                description = tool.description or ""
                if not description.strip():
                    # D9's accepted trade-off, made visible rather than papered over.
                    log.warning(
                        "tool %r on server %r has no description; it will match poorly",
                        tool.name,
                        server.name,
                    )
                tools.append(
                    AggregatedTool(
                        server=server.name,
                        name=tool.name,
                        description=description,
                        input_schema=tool.inputSchema or {},
                    )
                )

        old_stack = self._stack
        self._stack = stack
        self.catalog = Catalog(tools=tools, clients=clients, failed=failed)
        self.config_error = None
        await self._close(old_stack)

        log.info(
            "aggregated %d tools from %d server(s), %d unavailable",
            len(tools),
            len(clients),
            len(failed),
        )

    @staticmethod
    def _transport(server: ServerConfig) -> ClientTransport:
        """Pick a transport from the config shape (issue #1).

        The `/sse` suffix rule is fastmcp's own `infer_transport` convention;
        it is reimplemented here only because that helper takes no headers.
        """
        if server.url is not None:
            http = SSETransport if server.url.rstrip("/").endswith("/sse") else StreamableHttpTransport
            return http(url=server.url, headers=server.headers)
        return StdioTransport(
            command=server.command,
            args=server.args,
            env=server.env,
            cwd=server.cwd,
        )

    async def _connect(self, stack: AsyncExitStack, server: ServerConfig) -> Client:
        return await stack.enter_async_context(Client(self._transport(server)))

    async def _connect_and_list(
        self, stack: AsyncExitStack, server: ServerConfig
    ) -> tuple[Client, list[Any]]:
        """Connect and read the tool list, allowing a remote endpoint one retry.

        A remote endpoint gets one transient blip forgiven at startup — otherwise
        a momentary network hiccup silently costs you that whole server until the
        config file next changes. A bad stdio command is deterministic, so
        retrying it only adds latency to a failure you are going to get anyway.
        """
        try:
            client = await self._connect(stack, server)
            return client, await client.list_tools()
        except Exception as exc:  # noqa: BLE001 — decide by transport, then re-raise
            if not server.is_http:
                raise
            log.info("server %r did not answer (%s), retrying once", server.name, exc)
            await asyncio.sleep(HTTP_RETRY_SECONDS)
            client = await self._connect(stack, server)
            return client, await client.list_tools()

    @staticmethod
    async def _close(stack: AsyncExitStack | None) -> None:
        if stack is None:
            return
        try:
            await stack.aclose()
        except Exception as exc:  # noqa: BLE001 — a backend dying on shutdown is not our problem
            log.warning("error closing downstream connections: %s", exc)

    async def call(self, server: str, tool_name: str, args: dict[str, Any]) -> Any:
        """Proxy a call to the owning downstream server (D10, D13)."""
        catalog = self.catalog  # bind once; a reload may swap it mid-call
        client = catalog.clients.get(server)
        if client is None:
            reason = catalog.failed.get(server)
            raise DownstreamError(
                f"server '{server}' is unavailable"
                + (f": {reason}" if reason else " (not in the current catalog)")
            )
        try:
            return await client.call_tool(tool_name, args)
        except Exception as exc:  # noqa: BLE001 — name the failing server (D13)
            http = isinstance(client.transport, (SSETransport, StreamableHttpTransport))
            # Retry only a genuinely dead session. A still-connected client failed
            # for a reason reconnecting cannot fix — bad arguments, an unknown
            # tool, the server's own error — and retrying those would run a
            # side-effecting tool a second time. Ask the session rather than
            # pattern-matching exception types: client-side argument validation
            # never reaches the wire, so the exception type alone can't tell you.
            if not http or client.is_connected():
                raise DownstreamError(
                    f"call to '{tool_name}' on server '{server}' failed: {exc}"
                ) from exc
            # A long-lived HTTP session is dropped by idle timeouts, proxies and
            # redeploys — routine, and invisible until the next call. Retry once
            # on a fresh session over the same transport. Entered and closed in
            # this task, so it never crosses the aggregator's cancel scope.
            log.info("server %r HTTP session failed (%s), reconnecting once", server, exc)
            try:
                async with Client(client.transport) as reconnected:
                    return await reconnected.call_tool(tool_name, args)
            except Exception as retry_exc:  # noqa: BLE001 — report the retry, not the stale first failure
                raise DownstreamError(
                    f"call to '{tool_name}' on server '{server}' failed, "
                    f"and failed again after reconnecting: {retry_exc}"
                ) from retry_exc

    async def _watch_config(self) -> None:
        """Re-aggregate when the config file changes (D8).

        ponytail: mtime poll, not a filesystem-watch dependency. One stat() per
        second against one file. Swap to `watchfiles` only if watching a tree.
        """
        last = self._mtime()
        while True:
            await asyncio.sleep(RELOAD_POLL_SECONDS)
            current = self._mtime()
            if current == last:
                continue
            last = current
            log.info("config changed, re-aggregating")
            try:
                await self._aggregate()
            except Exception as exc:  # noqa: BLE001 — a bad edit must not kill the server
                self.config_error = str(exc)
                log.warning("reload failed, keeping the previous catalog: %s", exc)
                continue
            for callback in self._on_reload:
                await callback(self.catalog)

    def _mtime(self) -> float | None:
        try:
            return self.config_path.stat().st_mtime
        except OSError:
            return None
