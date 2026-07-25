"""Downstream server aggregation (D8, D13).

Connects out to every configured stdio MCP server, collects its real published
tool list, and holds the connections open so calls can be proxied through.
Failures are isolated per-server: one unreachable backend costs you that
backend's tools, not the whole catalog.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from .config import ServerConfig, load_config

log = logging.getLogger("toolsieve")

RELOAD_POLL_SECONDS = 1.0


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
                client = await self._connect(stack, server)
                published = await client.list_tools()
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
    async def _connect(stack: AsyncExitStack, server: ServerConfig) -> Client:
        transport = StdioTransport(
            command=server.command,
            args=server.args,
            env=server.env,
            cwd=server.cwd,
        )
        return await stack.enter_async_context(Client(transport))

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
            raise DownstreamError(f"call to '{tool_name}' on server '{server}' failed: {exc}") from exc

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
