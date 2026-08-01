"""A tiny real MCP server, used as a downstream backend in tests.

    python fake_server.py <name>               # stdio
    python fake_server.py <name> --http 8123   # real HTTP on localhost
    python fake_server.py <name> --oauth 8123  # real HTTP, real OAuth 2.1 gate
"""

import sys

from fastmcp import FastMCP

# An access token the --oauth server accepts. Tests seed this into toolsieve's
# token store to reach the "already authorized" path without driving a browser;
# the server still validates it through its real auth middleware.
SEEDED_TOKEN = "seeded-access-token"


def _oauth_app(name: str, port: int) -> FastMCP:
    """A real OAuth-gated MCP server: 401 + WWW-Authenticate, PRM, ASM, DCR.

    fastmcp's own in-memory provider supplies the whole OAuth 2.1 surface, so
    the client under test performs genuine spec discovery rather than talking
    to a hand-rolled stub that only looks like an authorization server.
    """
    from mcp.server.auth.provider import AccessToken

    from fastmcp.server.auth.auth import ClientRegistrationOptions
    from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider

    base = f"http://127.0.0.1:{port}"
    provider = InMemoryOAuthProvider(
        base_url=base,
        resource_base_url=base,
        # Dynamic client registration on: it is how a client with no
        # pre-issued credentials joins, which is exactly toolsieve's case.
        client_registration_options=ClientRegistrationOptions(enabled=True),
    )
    provider.access_tokens[SEEDED_TOKEN] = AccessToken(
        token=SEEDED_TOKEN, client_id="seeded-client", scopes=[], expires_at=None
    )
    return FastMCP(name=name, auth=provider)


mcp = FastMCP(name=sys.argv[1] if len(sys.argv) > 1 else "fake")
if "--oauth" in sys.argv:
    mcp = _oauth_app(sys.argv[1], int(sys.argv[sys.argv.index("--oauth") + 1]))


@mcp.tool
def get_weather(city: str) -> str:
    """Get the current weather forecast, temperature and rain, for a city."""
    return f"sunny in {city}"


@mcp.tool
def search_docs(query: str) -> str:
    """Search the project's markdown documentation files for a phrase."""
    return f"docs matching {query}"


# `--catalog` adds a realistic roster on top of the two tools above. Only demo.py
# uses it: routing a 4-tool catalog saves ~5%, which is a true number and a
# terrible demonstration, because 4 tools is below the size where a sieve pays
# for itself at all. The tests keep the 2-tool default, which several of them
# assert on directly.
CATALOG = {
    "create_issue": ("Create a new issue in a repository with a title and body.", "title"),
    "list_issues": ("List issues in a repository, filtered by state and label.", "state"),
    "get_issue": ("Read one issue by number, with its body and current labels.", "number"),
    "create_pull_request": ("Open a pull request from a head branch into a base branch.", "head"),
    "merge_pull_request": ("Merge an open pull request using the given merge method.", "number"),
    "list_commits": ("List commits on a branch, newest first, with author and message.", "branch"),
    "read_file": ("Read the complete contents of a file from the filesystem.", "path"),
    "write_file": ("Write content to a file, creating or overwriting it.", "path"),
    "list_directory": ("List the files and subdirectories inside a directory.", "path"),
    "move_file": ("Move or rename a file or directory to a new location.", "source"),
    "send_message": ("Post a message to a channel or direct message conversation.", "channel"),
    "list_channels": ("List the channels in the workspace that are visible to you.", "cursor"),
    "create_page": ("Create a page in the workspace under a parent page.", "title"),
    "query_database": ("Query a database with a filter and sort, returning matching rows.", "filter"),
    "run_sql": ("Execute a read-only SQL statement against the connected database.", "sql"),
    "list_tables": ("List the tables in a schema, with their row counts.", "schema"),
    "capture_screenshot": ("Take a screenshot of the current browser page.", "selector"),
    "navigate_to": ("Navigate the browser to a URL and wait for it to load.", "url"),
}

if "--catalog" in sys.argv:
    for _name, (_summary, _param) in CATALOG.items():
        # Default-arg binding, not closure capture: a bare closure over the loop
        # variables would give every tool the last entry's name.
        mcp.tool(name=_name, description=_summary)(
            lambda value="", _n=_name, _p=_param: f"{_n} ran with {_p}={value}"
        )


if __name__ == "__main__":
    if "--http" in sys.argv:
        from fastmcp.server.dependencies import get_http_headers

        @mcp.tool
        def echo_auth() -> str:
            """Return the Authorization header this request arrived with."""
            # include= is required: fastmcp strips `authorization` by default.
            return get_http_headers(include={"authorization"}).get("authorization", "<none>")

        mcp.run(transport="http", host="127.0.0.1", port=int(sys.argv[sys.argv.index("--http") + 1]))
    elif "--oauth" in sys.argv:
        mcp.run(
            transport="http",
            host="127.0.0.1",
            port=int(sys.argv[sys.argv.index("--oauth") + 1]),
        )
    else:
        mcp.run(transport="stdio")
