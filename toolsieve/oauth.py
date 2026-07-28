"""OAuth for downstream servers (D3, D6, D16, D17).

toolsieve implements no OAuth protocol of its own: fastmcp already ships a
full client (dynamic registration, PKCE, refresh), so everything here is
wiring — where tokens live, and which of the two flows a given process is
allowed to run.
"""

from __future__ import annotations

from pathlib import Path

from fastmcp.client.auth.oauth import OAuth
from key_value.aio.stores.filetree import (
    FileTreeStore,
    FileTreeV1CollectionSanitizationStrategy,
    FileTreeV1KeySanitizationStrategy,
)

# Sibling of ~/.toolsieve/config.json — one place for toolsieve's state,
# whatever the install method (D3).
DEFAULT_TOKEN_DIR = Path.home() / ".toolsieve/oauth"


def token_store(directory: Path | str | None = None) -> FileTreeStore:
    """The shared token store, used by both the aggregator and `toolsieve-auth`.

    One store holds every server's tokens: fastmcp's `TokenStorageAdapter`
    keys entries by server URL, so they never collide. Those keys are
    URL-shaped (`https://host/mcp/tokens`), which a filesystem will not take
    verbatim — hence the sanitization strategies, the same pair fastmcp uses
    for its own persistent OAuth storage.

    ponytail: FileTreeStore, not DiskStore — DiskStore needs a
    `py-key-value-aio[disk]` extra nothing installs, and plain JSON files
    cost no new dependency (D3 as amended). The ceiling is concurrency: no
    file locking, so a refresh racing an authorize on the same server can
    tear a file. That reads as "needs authorization" and is fixed by
    re-running `toolsieve-auth`; add the [disk] extra if it ever bites.
    """
    directory = Path(directory) if directory is not None else DEFAULT_TOKEN_DIR
    directory.mkdir(parents=True, exist_ok=True)
    # mkdir's mode is masked by umask, so harden explicitly: these files are
    # live refresh tokens in plaintext (D17).
    directory.chmod(0o700)
    return FileTreeStore(
        data_directory=directory,
        key_sanitization_strategy=FileTreeV1KeySanitizationStrategy(directory),
        collection_sanitization_strategy=FileTreeV1CollectionSanitizationStrategy(directory),
    )


class AuthorizationRequiredError(RuntimeError):
    """A server needs an interactive login that this process must not run."""


def find_auth_error(exc: BaseException) -> AuthorizationRequiredError | None:
    """Dig an AuthorizationRequiredError out of whatever wrapped it.

    It is raised deep inside httpx's auth flow and surfaces through anyio task
    groups, so it arrives grouped or chained rather than bare. Matching only a
    bare instance would silently degrade the one message that tells the user
    how to fix this back to an anonymous connection error.
    """
    seen: set[int] = set()
    queue: list[BaseException | None] = [exc]
    while queue:
        current = queue.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, AuthorizationRequiredError):
            return current
        queue.extend(getattr(current, "exceptions", []))
        queue.extend([current.__cause__, current.__context__])
    return None


class NonInteractiveOAuth(OAuth):
    """OAuth minus the browser — the only variant the MCP server may use (D16).

    Everything that can happen without a human is inherited untouched: a
    stored access token authenticates, an expired one refreshes silently.
    Only the two steps that need a human are refused, so an unauthorized
    server fails fast into `Catalog.failed` (D9) instead of launching a
    browser nobody is watching and blocking on a callback nobody will make.

    `toolsieve-auth` runs the real `OAuth` instead — same tokens, same store.
    """

    async def redirect_handler(self, authorization_url: str) -> None:
        raise AuthorizationRequiredError(authorization_url)

    async def callback_handler(self) -> tuple[str, str | None]:
        raise AuthorizationRequiredError(self.mcp_url)
