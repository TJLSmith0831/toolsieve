"""`toolsieve-auth` — the one place a browser is allowed to open (D13-D16).

The MCP server process can never do this: it has no terminal, and a browser
it launched would block a startup nobody is watching. So the interactive half
of OAuth lives here, in a short-lived foreground command, and hands its
result to the server through the shared token store.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from .aggregator import Aggregator
from .config import (
    DEFAULT_CONFIG_PATH,
    ConfigError,
    ServerConfig,
    expand_env,
    load_config,
    load_dotenv_file,
)
from .oauth import NonInteractiveOAuth, find_auth_error, token_store


def _candidates(config_path: str | Path) -> list[ServerConfig]:
    """Servers that could possibly need OAuth: remote, and given no credential."""
    return [s for s in load_config(config_path) if s.is_http and not s.headers]


def unauthorized_servers(
    config_path: str | Path, token_dir: str | Path | None = None
) -> list[str]:
    """Names of the configured servers that are actually refusing us right now.

    Headerless is not the same as unauthorized — plenty of remote servers are
    simply open. Only the server can tell the difference, so ask it, and offer
    the user exactly the ones that answered "no" (D14).
    """
    agg = Aggregator(config_path, token_dir=token_dir)

    async def go() -> list[str]:
        names = []
        for server in _candidates(config_path):
            exc = await agg.connect_once(server)
            if exc is not None and find_auth_error(exc) is not None:
                names.append(server.name)
        return names

    return asyncio.run(go())


def server_url(server: ServerConfig, env_overrides: dict[str, str] | None = None) -> str:
    """The url with `${VAR}` resolved — the key everything stores tokens under.

    The aggregator connects with the expanded url, so anything here that keys
    on the raw template would write tokens the server can never find: a server
    would authorize successfully and still read as unauthorized, forever.
    """
    return expand_env(
        server.url or "", server=server.name, where="'url'", overrides=env_overrides
    )


def env_for(config_path: str | Path) -> dict[str, str]:
    """The `.env` beside the config, same file the aggregator reads (GH #6)."""
    return load_dotenv_file(Path(config_path).parent / ".env")


def clear_tokens(
    server: ServerConfig,
    token_dir: str | Path | None = None,
    env_overrides: dict[str, str] | None = None,
) -> None:
    """Forget everything stored for one server, so the next run starts clean."""

    async def go() -> None:
        auth = NonInteractiveOAuth(
            mcp_url=server_url(server, env_overrides), token_storage=token_store(token_dir)
        )
        await auth.token_storage_adapter.clear()

    asyncio.run(go())


def _config_path(explicit: str | Path | None = None) -> Path:
    """Same resolution order the server uses, so both read one file."""
    if explicit is not None:
        return Path(explicit)
    return Path(os.environ.get("TOOLSIEVE_CONFIG", DEFAULT_CONFIG_PATH))


# A fixed port, not a free one: on a headless box the redirect can only come
# back through an SSH tunnel, and you cannot forward a port you cannot predict
# (D7).
CALLBACK_PORT = 8765


def authorize_server(
    server: ServerConfig,
    token_dir: str | Path | None = None,
    port: int = CALLBACK_PORT,
    env_overrides: dict[str, str] | None = None,
) -> None:
    """Run the real browser flow for one server and persist what it returns.

    This is the interactive `OAuth`, not the aggregator's neutered subclass —
    the whole reason this command exists as a separate process (D16).
    """
    import webbrowser

    from fastmcp import Client
    from fastmcp.client.auth.oauth import OAuth

    from .aggregator import Aggregator

    auth = OAuth(
        mcp_url=server_url(server, env_overrides),
        token_storage=token_store(token_dir),
        callback_port=port,
        client_name="toolsieve",
    )
    if not _can_open_a_browser(webbrowser):
        print(
            f"\nNo browser on this machine. Forward the callback port, then re-run:\n"
            f"    ssh -L {port}:localhost:{port} <user>@<this-host>\n"
            f"and complete the sign-in in the browser on your own machine.\n"
        )

    async def go() -> None:
        transport = Aggregator._transport(server, env_overrides)
        async with Client(transport, auth=auth) as client:
            await client.list_tools()

    asyncio.run(go())
    print(f"{server.name} authorized.")


def _can_open_a_browser(webbrowser_module) -> bool:
    """Whether a local browser could actually be launched.

    `webbrowser.get()` raises when it can find nothing to open, which is the
    honest signal — checking $DISPLAY alone misreads macOS, and waiting for
    `open()` to return False costs the user the full callback timeout first.
    """
    try:
        webbrowser_module.get()
    except Exception:  # noqa: BLE001 — any failure means "no browser here"
        return False
    return True


def _wizard(path: Path, token_dir: str | Path | None, authorize) -> int:
    """Offer the servers that need signing in, and act on the ticked ones (D12)."""
    import questionary

    env_overrides = env_for(path)
    names = unauthorized_servers(path, token_dir)
    if not names:
        print("Every configured server is already authorized — nothing to do.")
        return 0

    by_name = {s.name: s for s in load_config(path)}
    picked = questionary.checkbox("Authorize which servers?", choices=names).ask()
    if not picked:  # ^C, or nothing ticked
        print("Nothing selected.")
        return 0

    for name in picked:
        authorize(by_name[name], token_dir=token_dir, env_overrides=env_overrides)
    return 0


def main(
    argv: list[str] | None = None,
    *,
    config_path: str | Path | None = None,
    token_dir: str | Path | None = None,
    authorize=None,
) -> int:
    """Bare invocation walks you through it; a name goes straight there (D14).

    `authorize` is injectable so the decision path above it can be tested
    without a browser; nothing but tests passes it.
    """
    parser = argparse.ArgumentParser(
        prog="toolsieve-auth",
        description="Sign in to the downstream MCP servers that need it.",
    )
    parser.add_argument("server", nargs="?", help="authorize just this one server")
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-authorize even if a valid token is already stored",
    )
    args = parser.parse_args(argv)

    if authorize is None:  # pragma: no cover — the real flow opens a browser
        authorize = authorize_server
    path = _config_path(config_path)
    try:
        by_name = {s.name: s for s in load_config(path)}
    except ConfigError as exc:
        # Same courtesy the rest of toolsieve extends a broken config: say what
        # is wrong, don't spray a traceback at someone who mistyped a path.
        print(f"{exc}", file=sys.stderr)
        return 1

    if args.server is None:
        return _wizard(path, token_dir, authorize)

    server = by_name.get(args.server)
    if server is None:
        print(f"No server named {args.server!r} in {path}.", file=sys.stderr)
        return 1

    env_overrides = env_for(path)
    if args.force:
        clear_tokens(server, token_dir, env_overrides)
    elif args.server not in unauthorized_servers(path, token_dir):
        print(f"{args.server} is already authorized — nothing to do.")
        return 0

    authorize(server, token_dir=token_dir, env_overrides=env_overrides)
    return 0
