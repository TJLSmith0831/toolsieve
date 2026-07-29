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
from .oauth import PUBLIC_CLIENT_METADATA, NonInteractiveOAuth, find_auth_error, token_store


def _candidates(config_path: str | Path) -> list[ServerConfig]:
    """Servers that could possibly need OAuth: remote, and given no credential."""
    return [s for s in load_config(config_path) if s.is_http and not s.headers]


# The three answers a server can give. "Silent" is not "fine": a server that
# never answered tells us nothing about whether it would want a sign-in, and
# reporting that as OK misleads whoever is already debugging it.
OK = "ok"
NEEDS_AUTH = "needs_auth"
SILENT = "silent"


def server_states(
    config_path: str | Path,
    token_dir: str | Path | None = None,
    only: list[str] | None = None,
) -> dict[str, tuple[str, BaseException | None]]:
    """Ask each candidate server how it actually answers, right now.

    Headerless is not the same as unauthorized — plenty of remote servers are
    simply open — and neither is the same as unreachable. Only the server can
    tell the three apart, so ask it, and keep the answers distinct all the way
    out to the user (D14). `only` narrows the probe, so naming a server — or
    migrating one — does not cost connections to every other one.
    """
    agg = Aggregator(config_path, token_dir=token_dir)
    candidates = [s for s in _candidates(config_path) if only is None or s.name in only]

    async def go() -> dict[str, tuple[str, BaseException | None]]:
        states = {}
        for server in candidates:
            exc = await agg.connect_once(server)
            if exc is None:
                states[server.name] = (OK, None)
            elif find_auth_error(exc) is not None:
                states[server.name] = (NEEDS_AUTH, exc)
            else:
                states[server.name] = (SILENT, exc)
        return states

    return asyncio.run(go())


def unauthorized_servers(
    config_path: str | Path, token_dir: str | Path | None = None
) -> list[str]:
    """Names of the configured servers that are actually refusing us right now."""
    return [
        name
        for name, (state, _) in server_states(config_path, token_dir).items()
        if state == NEEDS_AUTH
    ]


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
        additional_client_metadata=PUBLIC_CLIENT_METADATA,
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


def _ask_which(names: list[str]) -> list[str]:
    """The checkbox — or, with no terminal to draw it on, the commands instead.

    questionary raises a bare `EOFError` when stdin is not a tty: a pipe, a
    provisioning script, CI. That is not worth a traceback, least of all from
    `toolsieve-setup --apply`, where the migration has already succeeded by
    the time we get here and the user would be left staring at a crash.
    """
    import questionary

    try:
        picked = questionary.checkbox("Authorize which servers?", choices=names).ask()
    except EOFError:
        print("No terminal to prompt on. Sign in when you next have one:")
        for name in names:
            print(f"    toolsieve-auth {name}")
        return []
    if not picked:  # ^C, or nothing ticked
        print("Nothing selected.")
        return []
    return picked


def _authorize_or_report(
    authorize,
    server: ServerConfig,
    path: Path,
    token_dir: str | Path | None,
    clear_first: bool = False,
) -> bool:
    """Run one authorization, reporting a failure rather than raising it.

    A sign-in fails for ordinary reasons — the server is down, the callback
    port is taken, the browser tab was closed — and each is a sentence, not a
    traceback (D19's spirit). Isolated per server, so one failure inside the
    wizard does not cost the other ticked servers their turn.

    `clear_first` is `--force`: the old token has to go before the flow runs,
    or switching accounts silently keeps the old identity (D15). It is done
    in here rather than at the call sites so that it, too, fails as a sentence.
    """
    env_overrides = env_for(path)
    try:
        if clear_first:
            clear_tokens(server, token_dir, env_overrides)
        authorize(server, token_dir=token_dir, env_overrides=env_overrides)
    except Exception as exc:  # noqa: BLE001 — any failure is this server's alone
        print(f"{server.name} was not authorized: {exc}", file=sys.stderr)
        return False
    # A running toolsieve reloads on the config's mtime and nothing else, and
    # the new token landed in the token store instead — so without this the
    # failure message's own promise ("it reconnects on its own afterwards, with
    # no restart") is false, and the user runs the command we told them to and
    # watches nothing happen.
    #
    # ponytail: touch, not a second watcher. The token store is a tree whose
    # leaves change on a re-auth without the root's mtime moving, so watching
    # it means walking it every second; this reuses the one watch that already
    # exists. If tokens ever change from outside this command, that walk (or
    # `watchfiles`) is the upgrade.
    path.touch()
    return True


def wizard(
    path: Path,
    token_dir: str | Path | None,
    authorize,
    only: list[str] | None = None,
    force: bool = False,
) -> int:
    """Offer the servers that need signing in, and act on the ticked ones (D12).

    `only` narrows the offer to a caller's own list — `toolsieve-setup` passes
    the servers it just migrated, so a migration does not re-offer servers
    that were already there.
    """
    states = server_states(path, token_dir, only=only)

    # `--force` is the user overriding the diagnosis, exactly as in the named
    # path: an already-authorized server is offered too, because re-authorizing
    # one (to switch accounts) is the only reason to pass the flag here.
    wanted = (NEEDS_AUTH, OK) if force else (NEEDS_AUTH,)
    names = [n for n, (state, _) in states.items() if state in wanted]

    # Named, not offered: we don't know that signing in is what they want,
    # but dropping them from the list without a word leaves the user
    # wondering why the server they came here for was never mentioned.
    silent = [(n, exc) for n, (state, exc) in states.items() if state == SILENT]
    if silent:
        print("These servers did not answer, so whether they need a sign-in is unknown:")
        for name, exc in silent:
            print(f"  - {name}: {exc}")
        print()

    if not names:
        print("No configured server is asking for a sign-in right now.")
        return 0

    by_name = {s.name: s for s in load_config(path)}
    picked = _ask_which(names)
    if not picked:  # ^C, nothing ticked, or no terminal — _ask_which said why
        return 0

    failed = 0
    for name in picked:
        if not _authorize_or_report(
            authorize, by_name[name], path, token_dir, clear_first=force
        ):
            failed += 1
    return 1 if failed else 0


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
    try:
        return _run(argv, config_path, token_dir, authorize)
    except ConfigError as exc:
        # Same courtesy the rest of toolsieve extends a broken config: say what
        # is wrong, don't spray a traceback at someone who mistyped a path or
        # left a ${VAR} unset. Wrapping the whole command, not just the load:
        # `expand_env` raises this from deeper in too, wherever a url is needed.
        print(f"{exc}", file=sys.stderr)
        return 1


def _run(
    argv: list[str] | None,
    config_path: str | Path | None,
    token_dir: str | Path | None,
    authorize,
) -> int:
    """The command itself; `main` is the wrapper that turns a bad config into a
    message. Split only so that handler covers every line, not just the load."""
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
    by_name = {s.name: s for s in load_config(path)}

    if args.server is None:
        return wizard(path, token_dir, authorize, force=args.force)

    server = by_name.get(args.server)
    if server is None:
        print(f"No server named {args.server!r} in {path}.", file=sys.stderr)
        return 1

    if args.server not in {s.name for s in _candidates(path)}:
        # Above `--force`, not inside the `else`: this is structural, read
        # from the config, not a verdict a probe reached — and `--force`
        # overrides verdicts. A stdio server has no OAuth endpoint to sign in
        # to at all, and forcing one through anyway got as far as
        # "This transport does not support auth" before saying so.
        print(
            f"{args.server} does not use OAuth — it is a local command, or it "
            f"already carries auth headers. Nothing to sign in to."
        )
        return 0

    if not args.force:
        # `--force` is the user overriding the diagnosis, so skip it entirely —
        # including for a server we could not reach.
        state, exc = server_states(path, token_dir, only=[args.server])[args.server]
        if state == OK:
            print(f"{args.server} is already authorized — nothing to do.")
            return 0
        if state == SILENT:
            # Never "already authorized": this is exactly when someone is
            # debugging, and a false all-clear sends them the wrong way.
            print(f"{args.server} did not answer, so it is unknown whether it needs "
                  f"a sign-in: {exc}", file=sys.stderr)
            print("Fix the connection first, or re-run with --force to sign in anyway.",
                  file=sys.stderr)
            return 1

    return 0 if _authorize_or_report(
        authorize, server, path, token_dir, clear_first=args.force
    ) else 1
